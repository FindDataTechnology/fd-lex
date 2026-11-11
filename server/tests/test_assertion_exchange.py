"""平台断言校验与兑换端点（change converge-line-identity-and-keys 2.1/2.2）。

判据（任务 2.1 原文）：单测覆盖 **错 audience / 过期 / 重放** 三种拒绝，另加
这里必须自己守住的几条：

  - TTL 超过 5 分钟要拒（PyJWT 只管"没到期"，不管"给太久"）；
  - 缺 sub/jti 要拒（jti 是一次性的载体，没有它就谈不上重放保护）；
  - jit_policy=off 时一律拒（默认值，不接受任何平台代做）；
  - 未配置时 503，不是放行也不是 401（"没配"与"配了但凭据不对"必须可分）。

离线：不网络、不建表、不读写库——伪造的 jti 台账与 JWKS 都由测试注入。
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from fd_lex_api.assertion import (
    AssertionRejected,
    AssertionSettings,
    JitPolicy,
    verify_assertion,
)
from fd_lex_api.main import app

CLIENT = TestClient(app)

ISSUER = "https://platform.test"
AUDIENCE = "https://lex.finddatatech.cloud"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _settings(public_pem: str, *, policy: str = JitPolicy.FIRST_USE, ttl: int = 300) -> AssertionSettings:
    return AssertionSettings(
        issuer=ISSUER,
        audience=AUDIENCE,
        public_key_pem=public_pem,
        algorithms=("RS256",),
        max_ttl_seconds=ttl,
        jit_policy=policy,
    )


def _token(private_pem: str, *, aud=AUDIENCE, iss=ISSUER, ttl=120, jti="jti-1", sub="user-1", scope="line:write"):
    now = int(time.time())
    claims = {"iss": iss, "sub": sub, "aud": aud, "iat": now, "exp": now + ttl}
    if jti is not None:
        claims["jti"] = jti
    if scope is not None:
        claims["scope"] = scope
    return pyjwt.encode(claims, private_pem, algorithm="RS256")


class FakeReplay:
    def __init__(self):
        self.seen: set[str] = set()

    def claim(self, jti: str, expires_at: int) -> bool:
        if jti in self.seen:
            return False
        self.seen.add(jti)
        return True


# ── 任务要求的三种拒绝 ───────────────────────────────────────────────────────


def test_wrong_audience_is_rejected(keypair):
    private_pem, public_pem = keypair
    token = _token(private_pem, aud="https://other-line.test")
    with pytest.raises(AssertionRejected) as e:
        verify_assertion(token, settings=_settings(public_pem))
    assert e.value.reason == AssertionRejected.WRONG_AUDIENCE


def test_expired_is_rejected(keypair):
    private_pem, public_pem = keypair
    now = int(time.time())
    token = pyjwt.encode(
        {"iss": ISSUER, "sub": "u", "aud": AUDIENCE, "iat": now - 600, "exp": now - 300, "jti": "j"},
        private_pem,
        algorithm="RS256",
    )
    with pytest.raises(AssertionRejected) as e:
        verify_assertion(token, settings=_settings(public_pem))
    assert e.value.reason == AssertionRejected.EXPIRED


def test_replayed_jti_is_rejected(keypair):
    private_pem, public_pem = keypair
    replay = FakeReplay()
    token = _token(private_pem, jti="once")
    first = verify_assertion(token, settings=_settings(public_pem), replay=replay)
    assert first.jti == "once"
    with pytest.raises(AssertionRejected) as e:
        verify_assertion(token, settings=_settings(public_pem), replay=replay)
    assert e.value.reason == AssertionRejected.REPLAYED


# ── 本模块自己必须守住的几条 ─────────────────────────────────────────────────


def test_ttl_over_five_minutes_is_rejected(keypair):
    private_pem, public_pem = keypair
    token = _token(private_pem, ttl=301)
    with pytest.raises(AssertionRejected) as e:
        verify_assertion(token, settings=_settings(public_pem))
    assert e.value.reason == AssertionRejected.TTL_TOO_LONG


def test_missing_jti_is_rejected(keypair):
    private_pem, public_pem = keypair
    token = _token(private_pem, jti=None)
    with pytest.raises(AssertionRejected) as e:
        verify_assertion(token, settings=_settings(public_pem))
    assert e.value.reason == AssertionRejected.MISSING_CLAIM


def test_forged_signature_is_rejected(keypair):
    _private_pem, public_pem = keypair
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    token = _token(other_pem)
    with pytest.raises(AssertionRejected) as e:
        verify_assertion(token, settings=_settings(public_pem))
    assert e.value.reason in (AssertionRejected.BAD_SIGNATURE, AssertionRejected.TOKEN_MALFORMED)


def test_jit_off_rejects_everything(keypair):
    private_pem, public_pem = keypair
    token = _token(private_pem)
    with pytest.raises(AssertionRejected) as e:
        verify_assertion(token, settings=_settings(public_pem, policy=JitPolicy.OFF))
    assert e.value.reason == AssertionRejected.JIT_DISABLED


def test_valid_assertion_returns_identity(keypair):
    private_pem, public_pem = keypair
    ident = verify_assertion(
        _token(private_pem, scope="line:read line:write"),
        settings=_settings(public_pem),
        replay=FakeReplay(),
    )
    assert ident.sub == "user-1"
    assert ident.scope == ("line:read", "line:write")
    assert ident.audience == AUDIENCE


# ── 端点层：未配置必须 fail-closed，且与"凭据不对"可区分 ──────────────────────


def test_exchange_unconfigured_is_503_not_401(monkeypatch):
    # 默认配置（三项皆空）→ 503；用 401 会让人以为"我凭据错了"而去重试。
    monkeypatch.delenv("PLATFORM_ASSERTION_ISSUER", raising=False)
    monkeypatch.delenv("PLATFORM_ASSERTION_JWKS_URI", raising=False)
    monkeypatch.delenv("PLATFORM_ASSERTION_PUBLIC_KEY_PEM", raising=False)
    from fd_lex_api.config import get_settings

    get_settings.cache_clear()
    resp = CLIENT.post("/api/auth/exchange", headers={"authorization": "Bearer whatever"})
    assert resp.status_code == 503
    get_settings.cache_clear()


def test_exchange_missing_bearer_is_400():
    resp = CLIENT.post("/api/auth/exchange")
    assert resp.status_code == 400
    assert "missing bearer assertion" in resp.json()["detail"]

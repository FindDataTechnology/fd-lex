"""门 K 换发令牌的线侧离线校验（change converge-line-identity-and-keys 7.2）。

判据（7.2 原文）：**断开与网关的出站连接后，线侧用本地公钥仍能验签通过**。
所以这里的每一条用例都只用本地 PEM，且注入一个"被调用就炸"的 JWKS 工厂——
如果实现偷偷去问网关，这些用例会立刻红。

此外必须自己守住的几条：

  - `source` 必须是本线：签名与 `aud` 都合法、但票是给别的线签的，照样拒
    （它会把别线的计量记到这里，也是跨线复用票的入口）；
  - TTL 超过 600 秒要拒（PyJWT 只管"没到期"，不管"给太久"）；
  - **同一枚票可以反复用**：门 K 的票在有效期内会被多次调用复用，不能像门 A
    那样做 jti 一次性（那会让第二次调用就 401）；
  - 未配置时给 not_configured，不是放行也不是 token 错（"没配"与"配了但票不对"
    必须可分）。

离线：不网络、不建表、不读写库。
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from fd_lex_api.line_token import (
    MAX_TTL_SECONDS,
    LineTokenRejected,
    LineTokenSettings,
    verify_line_token,
)

ISSUER = "https://mcp.finddatatech.cloud"
LINE = "lex"


@pytest.fixture(scope="module")
def keypair():
    """与网关同族的 ES256 钥（P-256）；测试自己造，生产钥来自 Secret。"""
    key = ec.generate_private_key(ec.SECP256R1())
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


def _settings(public_pem: str, *, ttl: int = MAX_TTL_SECONDS) -> LineTokenSettings:
    return LineTokenSettings(
        issuer=ISSUER,
        line=LINE,
        public_key_pem=public_pem,
        algorithms=("ES256",),
        max_ttl_seconds=ttl,
    )


def _token(private_pem: str, **overrides) -> str:
    """按网关的形状签一枚票（claims 与 gateway/line-token-exchange.js 逐字对齐）。"""
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "42",
        "email": "c@example.com",
        "aud": LINE,
        "source": LINE,
        "scope": "line:read",
        "iat": now,
        "exp": now + MAX_TTL_SECONDS,
        "jti": "b6c1f0d2-0000-4000-8000-000000000001",
    }
    claims.update(overrides)
    return pyjwt.encode(claims, private_pem, algorithm="ES256")


def _no_network(*_args, **_kwargs):
    raise AssertionError("校验过程不得联网（JWKS 工厂被调用了）")


def test_本地公钥就能验过_全程不联网(keypair):
    private_pem, public_pem = keypair
    identity = verify_line_token(
        _token(private_pem),
        settings=_settings(public_pem),
        jwks_client_factory=_no_network,
    )
    assert identity.subject == "42"
    assert identity.email == "c@example.com"
    assert identity.source == LINE
    assert identity.scope == ("line:read",)
    assert identity.expires_at > int(time.time())


def test_同一枚票可以反复用_门K不是一次性的(keypair):
    private_pem, public_pem = keypair
    token = _token(private_pem)
    settings = _settings(public_pem)
    first = verify_line_token(token, settings=settings, jwks_client_factory=_no_network)
    second = verify_line_token(token, settings=settings, jwks_client_factory=_no_network)
    assert first.jti == second.jti
    assert first.expires_at == second.expires_at


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"aud": "wire"}, LineTokenRejected.WRONG_AUDIENCE),
        ({"source": "wire"}, LineTokenRejected.WRONG_SOURCE),
        ({"iss": "https://someone-else.test"}, LineTokenRejected.WRONG_ISSUER),
        ({"exp": int(time.time()) - 5}, LineTokenRejected.EXPIRED),
        ({"sub": ""}, LineTokenRejected.MISSING_CLAIM),
        ({"source": ""}, LineTokenRejected.MISSING_CLAIM),
    ],
)
def test_拒绝分支逐条给稳定原因码(keypair, overrides, reason):
    private_pem, public_pem = keypair
    with pytest.raises(LineTokenRejected) as excinfo:
        verify_line_token(
            _token(private_pem, **overrides),
            settings=_settings(public_pem),
            jwks_client_factory=_no_network,
        )
    assert excinfo.value.reason == reason


def test_ttl_超过600秒被拒(keypair):
    private_pem, public_pem = keypair
    now = int(time.time())
    token = _token(private_pem, iat=now, exp=now + MAX_TTL_SECONDS + 1)
    with pytest.raises(LineTokenRejected) as excinfo:
        verify_line_token(token, settings=_settings(public_pem), jwks_client_factory=_no_network)
    assert excinfo.value.reason == LineTokenRejected.TTL_TOO_LONG


def test_换个公钥就验不过_签名按线隔离(keypair):
    private_pem, _ = keypair
    other = ec.generate_private_key(ec.SECP256R1())
    other_public_pem = other.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    with pytest.raises(LineTokenRejected) as excinfo:
        verify_line_token(
            _token(private_pem),
            settings=_settings(other_public_pem),
            jwks_client_factory=_no_network,
        )
    assert excinfo.value.reason == LineTokenRejected.BAD_SIGNATURE


def test_未配置时是_not_configured_不是放行也不是票错():
    with pytest.raises(LineTokenRejected) as excinfo:
        verify_line_token("a.b.c", settings=LineTokenSettings(issuer="", line=LINE))
    assert excinfo.value.reason == LineTokenRejected.NOT_CONFIGURED


def test_空票被当畸形而不是未配置(keypair):
    _, public_pem = keypair
    with pytest.raises(LineTokenRejected) as excinfo:
        verify_line_token("", settings=_settings(public_pem), jwks_client_factory=_no_network)
    assert excinfo.value.reason == LineTokenRejected.TOKEN_MALFORMED

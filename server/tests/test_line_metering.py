"""门 K 计量归属（change converge-line-identity-and-keys 7.3）。

判据（7.3 原文）：**一次换票 + 一次调用只产生一条账；只换票不调用产生零条。**

三条用例分别钉住：

1. 只换票（校验令牌）→ 计量表仍然是 0 行。这条不是"记得别写"，而是
   `line_token.py` 根本不 touch 计量模块（校验是无副作用纯函数）。
2. 一次调用 → 恰好一行，`source` 是线名、`credential_track=customer`、
   `caller` 是票里的 email。
3. 同一 `request_id` 再来一次（重试/重放）→ 仍然只有一行；
   换一个 `request_id` → 第二行。幂等靠唯一约束，不靠先查后写。

库：sqlite 临时文件（不碰开发机真实库）。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from fd_lex_api.line_metering import meter_line_call
from fd_lex_api.line_token import LineTokenSettings, verify_line_token
from fd_lex_api.metering.db import to_utc
from fd_lex_api.metering.models import MeteringBase, MeteringRecord, TRACK_CUSTOMER

ISSUER = "https://mcp.finddatatech.cloud"
LINE = "lex"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    MeteringBase.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


@pytest.fixture(scope="module")
def keypair():
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


def _token(private_pem: str, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "42",
        "email": "c@example.com",
        "aud": LINE,
        "source": LINE,
        "scope": "line:read",
        "iat": now,
        "exp": now + 600,
        "jti": "11111111-2222-4333-8444-555555555555",
    }
    claims.update(overrides)
    return pyjwt.encode(claims, private_pem, algorithm="ES256")


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(MeteringRecord)) or 0


def _identity(private_pem: str, public_pem: str):
    return verify_line_token(
        _token(private_pem),
        settings=LineTokenSettings(issuer=ISSUER, line=LINE, public_key_pem=public_pem, algorithms=("ES256",)),
    )


def test_verify_token_writes_no_ledger_row(session, keypair):
    """只换票不调用 → 零条账（校验器是无副作用纯函数）。"""
    private_pem, public_pem = keypair
    _identity(private_pem, public_pem)
    assert _count(session) == 0


def test_one_call_makes_exactly_one_row_attributed_to_the_line(session, keypair):
    private_pem, public_pem = keypair
    identity = _identity(private_pem, public_pem)

    row = meter_line_call(session, identity, request_id="req-1", tool_name="lex_case_read")
    assert row is not None
    session.commit()

    assert _count(session) == 1
    stored = session.scalars(select(MeteringRecord)).one()
    assert stored.source == LINE
    assert stored.credential_track == TRACK_CUSTOMER
    assert stored.caller == "c@example.com"
    assert stored.caller_principal == "c@example.com"
    assert stored.tool_name == "lex_case_read"
    assert stored.quantity == 1
    assert stored.server_name == "lex-ops-mcp"


def test_retry_with_same_request_id_does_not_double_book(session, keypair):
    private_pem, public_pem = keypair
    identity = _identity(private_pem, public_pem)

    first = meter_line_call(session, identity, request_id="req-1")
    session.commit()
    second = meter_line_call(session, identity, request_id="req-1")
    session.commit()

    assert first is not None
    # 第二次返回 None = "没有新增"，调用方能区分「记了」与「早就记过」
    assert second is None
    assert _count(session) == 1


def test_a_different_request_id_is_a_second_call(session, keypair):
    private_pem, public_pem = keypair
    identity = _identity(private_pem, public_pem)
    meter_line_call(session, identity, request_id="req-1")
    meter_line_call(session, identity, request_id="req-2")
    session.commit()
    assert _count(session) == 2


def test_request_id_is_mandatory_because_it_is_the_idempotency_key(session, keypair):
    private_pem, public_pem = keypair
    identity = _identity(private_pem, public_pem)
    with pytest.raises(ValueError):
        meter_line_call(session, identity, request_id="  ")
    assert _count(session) == 0


def test_row_carries_event_timestamp_when_given(session, keypair):
    private_pem, public_pem = keypair
    identity = _identity(private_pem, public_pem)
    stamp = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
    meter_line_call(session, identity, request_id="req-ts", event_timestamp=stamp)
    session.commit()
    # sqlite 的 DateTime(timezone=True) 回读是 naive（4.3 撞过的同一个坑），
    # 所以用仓里的 to_utc 归一化再比，而不是把断言放宽成"忽略时区"。
    assert to_utc(session.scalars(select(MeteringRecord)).one().event_timestamp) == stamp

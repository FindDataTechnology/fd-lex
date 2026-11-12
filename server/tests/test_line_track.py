"""门 K 顾客轨接入控制台/工具面（change converge-line-identity-and-keys 8.2）。

判据（8.2 原文）：工具面增加词元客户轨，**保留 lex PAT**；且
`lex_credential_mint` 仅对带 `line:admin` 与 consent 行的服务主体开放。

这里钉四件事：

1. **未配置 = 与改动前逐字节一致**：没给公钥/JWKS 时，错凭据回的还是 Logto
   那条 401（同状态码、同 detail）——不能因为多了一条轨就把错误形状换掉。
2. **有效门 K 令牌 → 同一个 ConsoleUser 类型**，`claims` 里带标记、scope 与
   customer_id，控制台各端点继续按原样工作。
3. **身份永不自动创建**：验过但解析不到客户 → 403 `unknown_identity`。
4. **铸造/吊销的闸**：门 K 这条路缺 `line:admin` → 403 `admin_scope_required`；
   有 `line:admin` 但没有生效 consent 行（或授权行不含该 scope）→
   `consent_required`；两者齐备才放行。人（Logto）那条路不受影响。

离线：不网络、不建外部服务；库用 sqlite 内存库。依赖是 async 函数，同步用例
用 asyncio.run 驱动（仓里没有 async 测试插件，不在这一笔里引入）。
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from fd_lex_api.billing.models import Base as BillingBase, Customer, Plan
from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.console_api.identity import binding_for
from fd_lex_api.console_api.line_auth import (
    LINE_CALLER_CLAIM,
    REASON_ADMIN_SCOPE_REQUIRED,
    REASON_CONSENT_REQUIRED,
    REASON_UNKNOWN_IDENTITY,
    ensure_credential_admin_allowed,
    is_line_caller,
    require_console_or_line,
)
from fd_lex_api.console_api.models import (
    ConsoleBase,
    ConsoleIdentityBinding,
    ConsolePlatformConsent,
)
from fd_lex_api.main import app
from fd_lex_api.metering.db import MeteringBase, get_engine, reset_metering_runtime

ISSUER = "https://mcp.finddatatech.cloud"
LINE = "lex"


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


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    BillingBase.metadata.create_all(engine)
    ConsoleBase.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture()
def line_env(monkeypatch, keypair):
    """打开门 K 那条路（公钥 + issuer + line）。"""
    _private, public_pem = keypair
    monkeypatch.setenv("LINE_TOKEN_ISSUER", ISSUER)
    monkeypatch.setenv("LINE_TOKEN_LINE", LINE)
    monkeypatch.setenv("LINE_TOKEN_PUBLIC_KEY_PEM", public_pem)
    reset_settings()
    yield
    reset_settings()


def _token(private_pem: str, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "8tajhoprelg0",
        "email": "c@example.com",
        "aud": LINE,
        "source": LINE,
        "scope": "line:read",
        "iat": now,
        "exp": now + 600,
        "jti": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    }
    claims.update(overrides)
    return pyjwt.encode(claims, private_pem, algorithm="ES256")


def _request(token: str) -> Request:
    return Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


def _call(request: Request, db: Session) -> ConsoleUser:
    return asyncio.run(require_console_or_line(request, db))


def _customer(db: Session, *, sub2api_user_id: str | None = "8tajhoprelg0") -> Customer:
    plan = db.query(Plan).filter_by(code="std").one_or_none()
    if plan is None:
        plan = Plan(code="std", name="标准", monthly_price_micro=0)
        db.add(plan)
        db.flush()
    customer = Customer(name="测试客户", sub2api_user_id=sub2api_user_id, plan_id=plan.id)
    db.add(customer)
    db.flush()
    return customer


# ---------------------------------------------------------------- 未配置 ----

def test_unconfigured_is_byte_identical_to_logto_only(db, monkeypatch):
    """没给公钥/JWKS 时：错凭据的失败形状必须与改动前完全一致。"""
    monkeypatch.delenv("LINE_TOKEN_PUBLIC_KEY_PEM", raising=False)
    monkeypatch.delenv("LINE_TOKEN_JWKS_URI", raising=False)
    reset_settings()
    request = _request("nope")

    with pytest.raises(HTTPException) as via_combined:
        _call(request, db)
    with pytest.raises(HTTPException) as via_logto:
        asyncio.run(require_console_user(request))

    # 断言的是"与 Logto 那条错误逐项相同"，不硬写某个码：测试环境里 Logto
    # 未配置时既有的错是 503（console login not configured），配了则是 401。
    # 两种情形下"多一条轨"都不得改变调用方看到的形状。
    assert via_combined.value.status_code == via_logto.value.status_code
    assert via_combined.value.detail == via_logto.value.detail


# ------------------------------------------------------------ 有效令牌 ----

def test_line_token_resolves_customer_and_marks_the_claim(db, line_env, keypair):
    private_pem, _ = keypair
    customer = _customer(db)

    user = _call(_request(_token(private_pem)), db)

    assert is_line_caller(user) is True
    assert user.claims[LINE_CALLER_CLAIM] is True
    assert user.claims["customer_id"] == customer.id
    assert user.claims["scope"] == "line:read"
    # identity = **当初命中客户的那个值**（这里没有 email 绑定行，所以是
    # sub2api 用户 id）；端点内部会拿它再解析一次客户，口径必须一致。
    assert user.identity == "8tajhoprelg0"
    assert user.email == "c@example.com"
    assert user.sub == "8tajhoprelg0"


def test_line_token_resolves_via_binding_before_sub2api_id(db, line_env, keypair):
    """有绑定表行时按 email 命中，且**不要求已确认**（客户看自己的东西）。"""
    private_pem, _ = keypair
    customer = _customer(db, sub2api_user_id=None)
    db.add(ConsoleIdentityBinding(customer_id=customer.id, identity="c@example.com"))
    db.commit()

    user = _call(_request(_token(private_pem)), db)
    assert user.claims["customer_id"] == customer.id
    # 有绑定行时按 email 命中，identity 就是 email
    assert user.identity == "c@example.com"
    assert binding_for(db, "c@example.com") is not None


def test_unknown_identity_is_403_and_creates_nothing(db, line_env, keypair):
    private_pem, _ = keypair
    with pytest.raises(HTTPException) as exc:
        _call(_request(_token(private_pem)), db)
    assert exc.value.status_code == 403
    assert exc.value.detail["reason"] == REASON_UNKNOWN_IDENTITY
    assert db.query(Customer).count() == 0


def test_token_for_another_line_falls_back_to_the_logto_error(db, line_env, keypair):
    """aud/source 不是本线的票在本线一律不认：退回**原封不动**的 Logto 错误。"""
    private_pem, _ = keypair
    _customer(db)
    foreign = _request(_token(private_pem, aud="wire", source="wire"))

    with pytest.raises(HTTPException) as via_combined:
        _call(foreign, db)
    with pytest.raises(HTTPException) as via_logto:
        asyncio.run(require_console_user(foreign))

    assert via_combined.value.status_code == via_logto.value.status_code
    assert via_combined.value.detail == via_logto.value.detail


# --------------------------------------------------------- 铸造/吊销闸 ----

def test_human_path_is_untouched_by_the_gate(db):
    """人（Logto）这条路不过闸——既有能力不变。"""
    human = ConsoleUser(identity="c@example.com", username="c", email="c@example.com", sub="s")
    ensure_credential_admin_allowed(db, human)  # 不抛即通过


def test_mint_without_admin_scope_is_refused(db, line_env, keypair):
    private_pem, _ = keypair
    _customer(db)
    user = _call(_request(_token(private_pem, scope="line:read line:write")), db)
    with pytest.raises(HTTPException) as exc:
        ensure_credential_admin_allowed(db, user)
    assert exc.value.status_code == 403
    assert exc.value.detail["reason"] == REASON_ADMIN_SCOPE_REQUIRED


def test_mint_with_admin_scope_but_no_consent_row_is_refused(db, line_env, keypair):
    private_pem, _ = keypair
    _customer(db)
    user = _call(_request(_token(private_pem, scope="line:read line:admin")), db)
    with pytest.raises(HTTPException) as exc:
        ensure_credential_admin_allowed(db, user)
    assert exc.value.detail["reason"] == REASON_CONSENT_REQUIRED


def test_consent_row_without_admin_scope_is_still_refused(db, line_env, keypair):
    private_pem, _ = keypair
    customer = _customer(db)
    db.add(
        ConsolePlatformConsent(
            customer_id=customer.id,
            platform_subject="8tajhoprelg0",
            scope="line:read",  # 授权行存在但不含 line:admin
        )
    )
    db.commit()
    user = _call(_request(_token(private_pem, scope="line:read line:admin")), db)
    with pytest.raises(HTTPException) as exc:
        ensure_credential_admin_allowed(db, user)
    assert exc.value.detail["reason"] == REASON_CONSENT_REQUIRED


def test_admin_scope_plus_active_consent_passes(db, line_env, keypair):
    private_pem, _ = keypair
    customer = _customer(db)
    db.add(
        ConsolePlatformConsent(
            customer_id=customer.id,
            platform_subject="8tajhoprelg0",
            scope="line:read line:admin",
            granted_by="test",
        )
    )
    db.commit()
    user = _call(_request(_token(private_pem, scope="line:read line:admin")), db)
    ensure_credential_admin_allowed(db, user)  # 不抛即通过


def test_revoked_consent_does_not_count(db, line_env, keypair):
    private_pem, _ = keypair
    customer = _customer(db)
    db.add(
        ConsolePlatformConsent(
            customer_id=customer.id,
            platform_subject="8tajhoprelg0",
            scope="line:read line:admin",
            granted_by="test",
            revoked_at=datetime.now(UTC),
        )
    )
    db.commit()
    user = _call(_request(_token(private_pem, scope="line:read line:admin")), db)
    with pytest.raises(HTTPException) as exc:
        ensure_credential_admin_allowed(db, user)
    assert exc.value.detail["reason"] == REASON_CONSENT_REQUIRED

# ------------------------------------------------------------- 真接缝 ----

def test_routes_really_use_the_combined_dependency(monkeypatch, tmp_path, keypair):
    """接缝证明：不打 dependency override，直接拿门 K 令牌打真实路由。

    `test_line_token_resolves_customer_and_marks_the_claim` 之类只证明判定函数是对的；
    这个仓反复吃过的亏正是「判定绿了不等于接缝真的调了它」——如果 routes.py 还挂在
    `require_console_user` 上，这条会拿 401/503，而不是 200。
    """
    from fastapi.testclient import TestClient

    private_pem, public_pem = keypair
    db_path = tmp_path / "seam.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("LINE_TOKEN_ISSUER", ISSUER)
    monkeypatch.setenv("LINE_TOKEN_LINE", LINE)
    monkeypatch.setenv("LINE_TOKEN_PUBLIC_KEY_PEM", public_pem)
    monkeypatch.delenv("LOGTO_ISSUER", raising=False)
    monkeypatch.delenv("LOGTO_CLIENT_ID", raising=False)
    reset_metering_runtime()
    reset_settings()

    engine = get_engine()
    BillingBase.metadata.create_all(engine)
    MeteringBase.metadata.create_all(engine)
    ConsoleBase.metadata.create_all(engine)
    with Session(engine) as session:
        plan = Plan(code="std", name="标准", monthly_price_micro=0)
        session.add(plan)
        session.flush()
        customer = Customer(name="接缝客户", sub2api_user_id="8tajhoprelg0", plan_id=plan.id)
        session.add(customer)
        session.commit()
        customer_id = customer.id

    try:
        client = TestClient(app)
        resp = client.get(
            "/api/console/me", headers={"Authorization": f"Bearer {_token(private_pem)}"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["customer"]["id"] == customer_id
    finally:
        reset_metering_runtime()
        reset_settings()

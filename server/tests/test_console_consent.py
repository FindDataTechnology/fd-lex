"""平台代做的授权入口（change converge-line-identity-and-keys 2.2）。

任务 2.2 的验收里有一句容易被当成文案：「`consent` 缺授权行时拒绝并**给出授权
入口**」。只回一句"缺授权"是空话——调用方拿 403 无处可去。本文件验的就是那个
入口真的存在、真的只能给本人授权、真的留痕。

五条硬断言：
  1. 没配 `PLATFORM_ASSERTION_ISSUER` → 503（本线还不知道该信谁）；
  2. 客户身份只认登录身份，请求体带 `customer_id` 无效（pydantic 直接丢弃）；
  3. 未绑定 → 403 binding_required，**且不建任何行**；
  4. 改 scope 是"撤旧 + 记新"，不是原地覆盖（审计要看得到撤销时间）；
  5. 撤销留痕不删行；别人的行按"不存在"处理（不泄漏存在性）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fd_lex_api.billing import Base as BillingBase
from fd_lex_api.billing import Customer, Plan
from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.console_api.models import ConsoleBase, ConsoleIdentityBinding, ConsolePlatformConsent
from fd_lex_api.main import app
from fd_lex_api.metering.db import MeteringBase, get_engine, reset_metering_runtime

PLATFORM = "https://platform.test"
OTHER_PLATFORM = "https://evil.test"
IDENTITY = "consent-demo"

USER = ConsoleUser(
    identity=IDENTITY,
    username=IDENTITY,
    email="demo@example.com",
    sub="sub-consent",
    roles=["lex-user"],
    organizations=[],
)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db_path = tmp_path / "lex_consent_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("PLATFORM_ASSERTION_ISSUER", PLATFORM)
    monkeypatch.setenv("CONSOLE_ORIGIN", "https://lex.example.test")
    reset_metering_runtime()
    reset_settings()

    engine = get_engine()
    BillingBase.metadata.create_all(engine)
    MeteringBase.metadata.create_all(engine)
    ConsoleBase.metadata.create_all(engine)

    app.dependency_overrides[require_console_user] = lambda: USER
    try:
        yield TestClient(app), engine
    finally:
        app.dependency_overrides.clear()
        reset_metering_runtime()
        reset_settings()


def _bind(engine, identity: str = IDENTITY) -> int:
    """套餐 + 客户 + 身份绑定（绑定的正确性靠后台建档，这里直接铺好）。"""
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        plan = Plan(code="std", name="Standard", monthly_price_micro=0)
        s.add(plan)
        s.commit()
        customer = Customer(name="Consent Demo", sub2api_user_id=f"u-{identity}", plan_id=plan.id)
        s.add(customer)
        s.commit()
        s.add(ConsoleIdentityBinding(customer_id=customer.id, identity=identity))
        s.commit()
        return customer.id


def _rows(engine):
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        return s.execute(select(ConsolePlatformConsent).order_by(ConsolePlatformConsent.id)).scalars().all()


# ── 前提：本线得先知道该信谁 ────────────────────────────────────────────────


def test_unconfigured_platform_is_503_not_a_row(env, monkeypatch):
    """没配 issuer 就不该记授权：记下来也永远不会被兑换端点认。"""
    client, engine = env
    _bind(engine)
    monkeypatch.delenv("PLATFORM_ASSERTION_ISSUER", raising=False)
    reset_settings()

    resp = client.post(
        "/api/console/consent", json={"platform_subject": PLATFORM, "scope": "line:read"}
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["reason"] == "platform_not_configured"
    assert _rows(engine) == []


def test_unknown_platform_subject_is_refused(env):
    """只收本线配置的那一个主体；放开等于允许客户把权限授给任意地址。"""
    client, engine = env
    _bind(engine)
    resp = client.post(
        "/api/console/consent", json={"platform_subject": OTHER_PLATFORM, "scope": "line:read"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["reason"] == "unknown_platform"
    assert _rows(engine) == []


# ── 铁律：不建绑定、不猜身份 ────────────────────────────────────────────────


def test_unbound_identity_cannot_grant_and_creates_nothing(env):
    client, engine = env
    resp = client.post(
        "/api/console/consent", json={"platform_subject": PLATFORM, "scope": "line:read"}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["reason"] == "binding_required"
    assert _rows(engine) == []
    assert client.get("/api/console/consent").json() == {
        "bound": False,
        "customer_id": None,
        "consents": [],
    }


def test_request_body_cannot_pick_the_customer(env):
    """请求体里塞 customer_id 应被丢弃——否则任何登录用户都能替别人授权。"""
    client, engine = env
    mine = _bind(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        other = Customer(name="Other", sub2api_user_id="u-other", plan_id=1)
        s.add(other)
        s.commit()
        other_id = other.id
    assert other_id != mine
    resp = client.post(
        "/api/console/consent",
        json={"platform_subject": PLATFORM, "scope": "line:read", "customer_id": other_id},
    )
    assert resp.status_code == 200, resp.text
    rows = _rows(engine)
    assert len(rows) == 1
    assert rows[0].customer_id == mine, "授权必须落在登录身份对应的客户上"


# ── 正路与留痕 ─────────────────────────────────────────────────────────────


def test_grant_normalizes_scope_and_is_idempotent(env):
    client, engine = env
    _bind(engine)
    first = client.post(
        "/api/console/consent",
        json={"platform_subject": PLATFORM, "scope": "  line:read   line:write line:read "},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["created"] is True
    assert body["consent"]["scope"] == ["line:read", "line:write"], "去重且保持首次出现顺序"
    assert body["consent"]["granted_by"] == USER.email

    again = client.post(
        "/api/console/consent",
        json={"platform_subject": PLATFORM, "scope": "line:write line:read"},
    )
    assert again.json()["created"] is False
    assert again.json()["consent"]["id"] == body["consent"]["id"]
    assert len(_rows(engine)) == 1, "同一 (客户, 平台) 只留一条生效行"


def test_changing_scope_revokes_then_records(env):
    """改 scope 不许原地覆盖：旧行要留下 revoked_at，审计才看得到什么时候收回。"""
    client, engine = env
    _bind(engine)
    first = client.post(
        "/api/console/consent", json={"platform_subject": PLATFORM, "scope": "line:read"}
    ).json()["consent"]

    second = client.post(
        "/api/console/consent", json={"platform_subject": PLATFORM, "scope": "line:read line:admin"}
    ).json()
    assert second["created"] is True
    assert second["consent"]["id"] != first["id"]

    rows = _rows(engine)
    assert len(rows) == 2, "旧行不删，只是撤销"
    by_id = {r.id: r for r in rows}
    assert by_id[first["id"]].revoked_at is not None
    assert by_id[second["consent"]["id"]].revoked_at is None

    listed = client.get("/api/console/consent").json()
    assert [c["id"] for c in listed["consents"]] == [second["consent"]["id"]], "列表只出生效行"


def test_revoke_keeps_the_row_and_is_not_silently_idempotent(env):
    client, engine = env
    _bind(engine)
    row = client.post(
        "/api/console/consent", json={"platform_subject": PLATFORM, "scope": "line:read"}
    ).json()["consent"]

    ok = client.delete(f"/api/console/consent/{row['id']}")
    assert ok.status_code == 200, ok.text
    assert ok.json()["revoked"] is True
    assert ok.json()["consent"]["revoked_at"] is not None
    assert len(_rows(engine)) == 1, "撤销留痕，不删行"

    again = client.delete(f"/api/console/consent/{row['id']}")
    assert again.status_code == 409, "重复撤销要如实报，不当成功"
    assert again.json()["detail"]["reason"] == "already_revoked"


def test_other_customers_consent_is_not_found_never_403(env):
    """别人的行按"不存在"回：区分 403/404 就是在探测别人授权了什么。"""
    client, engine = env
    _bind(engine)
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        stranger = ConsolePlatformConsent(
            customer_id=9999, platform_subject=PLATFORM, scope="line:admin", granted_by="x"
        )
        s.add(stranger)
        s.commit()
        stranger_id = stranger.id

    resp = client.delete(f"/api/console/consent/{stranger_id}")
    assert resp.status_code == 404, resp.text
    with Session(engine) as s:
        assert s.get(ConsolePlatformConsent, stranger_id).revoked_at is None, "不能碰别人的行"


# ── 兑换端点拒绝时必须指名这个入口 ─────────────────────────────────────────


def test_exchange_refusal_names_the_consent_entry_point(env, monkeypatch):
    """纯函数层面证明 403 里带的是**真实存在**的地址，不是一个装饰字段。"""
    from fd_lex_api.assertion import AssertionIdentity
    from fd_lex_api.auth_exchange import _entry_point
    from fd_lex_api.console_api.delegation import DelegationDenied

    ident = AssertionIdentity(
        sub="u-1", scope=("line:read",), issuer=PLATFORM,
        audience="https://lex.finddatatech.cloud", expires_at=0, jti="j",
    )
    point = _entry_point(DelegationDenied.CONSENT_REQUIRED, ident)
    assert point is not None
    assert point["actionable"] is True
    assert point["method"] == "POST"
    assert point["url"] == "https://lex.example.test/api/console/consent"
    assert point["request"] == {"platform_subject": PLATFORM, "scope": "line:read"}

    binding = _entry_point(DelegationDenied.BINDING_REQUIRED, ident)
    assert binding is not None
    assert binding["actionable"] is False, "绑定不是自助动作，别假装是"
    assert binding["url"].endswith("/api/console/me")

    assert _entry_point(DelegationDenied.JIT_DISABLED, ident) is None, "off 档没有下一步可走"


# ── 端到端：真签一份断言打到真端点（补上此前没覆盖的库会话接缝） ────────────


def test_exchange_end_to_end_with_real_assertion(env, monkeypatch):
    """绑定 + first-use 档下，一份真签的 RS256 断言应换回身份文档。

    这条存在的理由很具体：`_session()` 曾导入一个不存在的 `get_sessionmaker`，
    而当时所有兑换端点测试都停在"未配置 503 / 缺 bearer 400"——根本走不到那行，
    于是单测全绿、真机一调即 500。只要这条在，那个接缝就不可能再静默。
    """
    import time

    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from fd_lex_api.config import reset_settings

    client, engine = env
    customer_id = _bind(engine)

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

    monkeypatch.setenv("PLATFORM_ASSERTION_ISSUER", PLATFORM)
    monkeypatch.setenv("PLATFORM_ASSERTION_AUDIENCE", "https://lex.example.test")
    monkeypatch.setenv("PLATFORM_ASSERTION_PUBLIC_KEY_PEM", public_pem)
    monkeypatch.setenv("PLATFORM_ASSERTION_JIT_POLICY", "first-use")
    reset_settings()

    now = int(time.time())
    token = pyjwt.encode(
        {
            "iss": PLATFORM,
            "sub": IDENTITY,
            "aud": "https://lex.example.test",
            "iat": now,
            "exp": now + 120,
            "jti": "e2e-once",
            "scope": "line:read",
        },
        private_pem,
        algorithm="RS256",
    )

    resp = client.post("/api/auth/exchange", headers={"authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sub"] == IDENTITY
    assert body["customer_id"] == customer_id, "绑定解析必须落到真客户上"
    assert body["scope"] == ["line:read"]
    assert body["audience"] == "https://lex.example.test"
    assert body["consent_id"] is None, "first-use 档不产生授权行"

    # 同一个 jti 再来一次 → 重放要拒（不是 200 也不是 500）
    again = client.post("/api/auth/exchange", headers={"authorization": f"Bearer {token}"})
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["reason"] == "replayed"

"""本人确认预绑定 + 管理员批量建档（change converge-line-identity-and-keys 4.3）。

判据是任务里那句"**不得零确认**"，所以要盯的是三件事：

  1. 管理员批量建档 = 全部未确认（`created_pending`），且同一身份落第二个客户
     只报冲突、不抢行；
  2. 本人确认只能确认**自己**那一行，且从不建行（没有绑定 → 404）；
  3. 确认幂等：重复调用不报错、不刷新 `confirmed_at`（那是审计事实，不是心跳）。

端点级（真起 app、真走依赖），因为这三条里有两处的错法都只在接线层出现——
把 identity 从请求体读、或者"顺手建一个"，单测纯函数时都看不出来。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from fd_lex_api.billing import Base as BillingBase
from fd_lex_api.billing import Customer, Plan
from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.console_api.models import ConsoleBase, ConsoleIdentityBinding
from fd_lex_api.main import app
from fd_lex_api.metering.db import MeteringBase, get_engine, reset_metering_runtime
from fd_lex_api.admin_api.auth import AdminActor, require_admin

ME = "me@example.com"
OTHER = "colleague@example.com"

USER = ConsoleUser(
    identity=ME,
    username=ME,
    email=ME,
    sub="sub-me",
    roles=["lex-user"],
    organizations=[],
)

ADMIN = AdminActor(via="internal-token", identity="internal", roles=[])


@pytest.fixture()
def env(monkeypatch, tmp_path):
    db_path = tmp_path / "lex_prebind_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_metering_runtime()
    reset_settings()

    engine = get_engine()
    BillingBase.metadata.create_all(engine)
    MeteringBase.metadata.create_all(engine)
    ConsoleBase.metadata.create_all(engine)

    app.dependency_overrides[require_console_user] = lambda: USER
    app.dependency_overrides[require_admin] = lambda: ADMIN
    try:
        yield TestClient(app), engine
    finally:
        app.dependency_overrides.clear()
        reset_metering_runtime()
        reset_settings()


def _customer(engine, name: str = "Acme") -> int:
    with Session(engine) as s:
        # plan.code 唯一：第二个客户复用同一个 std 计划，不能重复插。
        plan = s.query(Plan).filter_by(code="std").one_or_none()
        if plan is None:
            plan = Plan(code="std", name="Standard", monthly_price_micro=0)
            s.add(plan)
            s.commit()
        customer = Customer(name=name, sub2api_user_id=f"u-{name}", plan_id=plan.id)
        s.add(customer)
        s.commit()
        return customer.id


def test_bulk_prebind_creates_pending_rows_and_never_confirms(env):
    client, engine = env
    cid = _customer(engine)
    r = client.post(
        f"/api/admin/customers/{cid}/identities/bulk",
        json={"identities": [ME, OTHER, ME, "   "], "note": "org rollout"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # 去重且保序：重复的 ME 只建一次，空白项被丢
    assert [x["identity"] for x in body["results"]] == [ME, OTHER]
    assert body["summary"] == {"created_pending": 2, "already_bound": 0, "conflict": 0, "invalid": 0}
    assert all(x["confirmed"] is False for x in body["results"])
    assert "confirm" in body["next"], "响应要指出去哪确认，否则运营不知道还差一步"

    with Session(engine) as s:
        rows = s.query(ConsoleIdentityBinding).all()
        assert {row.identity for row in rows} == {ME, OTHER}
        assert all(row.confirmed_at is None for row in rows), "批量建档不得顺手确认"


def test_bulk_prebind_reports_conflict_without_stealing_the_row(env):
    client, engine = env
    mine = _customer(engine, "Acme")
    theirs = _customer(engine, "Globex")
    first = client.post(f"/api/admin/customers/{theirs}/identities/bulk", json={"identities": [OTHER]})
    assert first.status_code == 201

    r = client.post(f"/api/admin/customers/{mine}/identities/bulk", json={"identities": [OTHER, ME]})
    body = r.json()
    status = {x["identity"]: x["status"] for x in body["results"]}
    assert status[OTHER] == "conflict"
    assert status[ME] == "created_pending"
    # 冲突项不返回对方 customer_id（不该被顺手抄走）
    assert all("customer_id" not in x for x in body["results"])

    with Session(engine) as s:
        row = s.query(ConsoleIdentityBinding).filter_by(identity=OTHER).one()
        assert row.customer_id == theirs, "别人的行不能被改写"


def test_confirm_endpoint_confirms_only_my_own_row(env):
    client, engine = env
    cid = _customer(engine)
    client.post(f"/api/admin/customers/{cid}/identities/bulk", json={"identities": [ME, OTHER]})

    r = client.post("/api/console/binding/confirm")
    assert r.status_code == 200, r.text
    assert r.json()["binding"]["confirmed"] is True
    assert r.json()["binding"]["confirmed_via"] == "self"

    with Session(engine) as s:
        mine = s.query(ConsoleIdentityBinding).filter_by(identity=ME).one()
        theirs = s.query(ConsoleIdentityBinding).filter_by(identity=OTHER).one()
        assert mine.confirmed_at is not None
        assert theirs.confirmed_at is None, "只确认自己那一行"


def test_confirm_is_idempotent_and_keeps_the_original_timestamp(env):
    client, engine = env
    cid = _customer(engine)
    client.post(f"/api/admin/customers/{cid}/identities/bulk", json={"identities": [ME]})

    first = client.post("/api/console/binding/confirm")
    stamp = first.json()["binding"]["confirmed_at"]
    assert stamp is not None

    second = client.post("/api/console/binding/confirm")
    assert second.status_code == 200
    assert second.json()["binding"]["confirmed_at"] == stamp, "确认时间戳是审计事实，不该被刷新"


def test_confirm_without_binding_is_404_and_creates_nothing(env):
    client, engine = env
    r = client.post("/api/console/binding/confirm")
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "binding_required"
    with Session(engine) as s:
        assert s.query(ConsoleIdentityBinding).count() == 0, "确认绝不能建绑定"


def test_get_binding_reports_state_without_404(env):
    client, engine = env
    empty = client.get("/api/console/binding")
    assert empty.status_code == 200
    assert empty.json()["binding"] is None, "没有绑定是合法状态，不该与路由不存在同形"
    assert empty.json()["identity"] == ME

    cid = _customer(engine)
    client.post(f"/api/admin/customers/{cid}/identities/bulk", json={"identities": [ME]})
    pending = client.get("/api/console/binding").json()["binding"]
    assert pending["confirmed"] is False
    client.post("/api/console/binding/confirm")
    done = client.get("/api/console/binding").json()["binding"]
    assert done["confirmed"] is True

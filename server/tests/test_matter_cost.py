"""案件维度计量（spec lex-billing-metering「案件维度计量」）。

纯查询侧聚合：写入路径与既有三方对账口径不变；`matter_id` 可空，
未携带时照旧记账（既有测试已覆盖），携带时可按案件合计。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.main import app
from fd_lex_api.metering.db import get_session, reset_metering_runtime
from fd_lex_api.metering.models import MeteringRecord

USER = ConsoleUser(identity="user-a", username="user-a", email="a@example.com", sub="s-a")
SHA = hashlib.sha256(b"doc").hexdigest()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lex.db'}")
    reset_metering_runtime()
    reset_settings()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    reset_metering_runtime()
    reset_settings()


def _as(user: ConsoleUser) -> None:
    app.dependency_overrides[require_console_user] = lambda: user


def _rec(db, *, request_id: str, matter_id: str | None, unit: str = "call", quantity: int = 1):
    db.add(
        MeteringRecord(
            event_timestamp=datetime.now(timezone.utc),
            request_id=request_id,
            caller="user-a",
            credential_track="customer",
            server_name="law-bench",
            tool_name="validation_check",
            quantity=quantity,
            unit=unit,
            source="customer",
            matter_id=matter_id,
        )
    )


def test_cost_sums_by_unit_and_matches_details(client):
    _as(USER)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]

    db = get_session()
    try:
        _rec(db, request_id="r1", matter_id=matter_id, unit="call", quantity=1)
        _rec(db, request_id="r2", matter_id=matter_id, unit="call", quantity=1)
        _rec(db, request_id="r3", matter_id=matter_id, unit="line", quantity=350)
        _rec(db, request_id="r4", matter_id=None, unit="call", quantity=1)  # 无案件维度
        db.commit()
    finally:
        db.close()

    cost = client.get(f"/api/console/matter/{matter_id}/cost").json()
    assert cost["matter_id"] == matter_id
    assert cost["by_unit"]["call"] == {"records": 2, "quantity": 2}
    assert cost["by_unit"]["line"] == {"records": 1, "quantity": 350}
    assert cost["total_records"] == 3
    # 合计 == 明细之和
    assert cost["total_quantity"] == cost["by_unit"]["call"]["quantity"] + cost["by_unit"]["line"]["quantity"]
    assert cost["total_quantity"] == 352


def test_cost_ignores_other_matter_and_null(client):
    _as(USER)
    m1 = client.post("/api/console/matter", json={"title": "一"}).json()["id"]
    m2 = client.post("/api/console/matter", json={"title": "二"}).json()["id"]

    db = get_session()
    try:
        _rec(db, request_id="a", matter_id=m1, quantity=5)
        _rec(db, request_id="b", matter_id=m2, quantity=7)
        _rec(db, request_id="c", matter_id=None, quantity=9)
        db.commit()
    finally:
        db.close()

    assert client.get(f"/api/console/matter/{m1}/cost").json()["total_quantity"] == 5
    assert client.get(f"/api/console/matter/{m2}/cost").json()["total_quantity"] == 7


def test_cost_empty_matter_is_zero_not_error(client):
    _as(USER)
    matter_id = client.post("/api/console/matter", json={"title": "空案"}).json()["id"]
    cost = client.get(f"/api/console/matter/{matter_id}/cost")
    assert cost.status_code == 200
    assert cost.json()["total_records"] == 0
    assert cost.json()["total_quantity"] == 0


def test_cost_cross_tenant_404(client):
    _as(USER)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]
    other = ConsoleUser(identity="user-b", username="user-b", email="b@example.com", sub="s-b")
    _as(other)
    assert client.get(f"/api/console/matter/{matter_id}/cost").status_code == 404


def test_metering_without_matter_id_unchanged(client):
    """既有口径不变：不带案件维度的记录照常写入。"""
    db = get_session()
    try:
        _rec(db, request_id="no-matter", matter_id=None, quantity=1)
        db.commit()
        row = db.query(MeteringRecord).filter_by(request_id="no-matter").one()
        assert row.matter_id is None
        assert row.quantity == 1
        assert row.unit == "call"
    finally:
        db.close()

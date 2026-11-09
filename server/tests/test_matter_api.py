"""案件脊柱 API 测试（spec lex-matter）。

覆盖：创建/列表/详情/产物登记/列举、契约拒绝不落库、租户隔离（他人案件 404）、
归档禁止新增、证据链断链显式标记。
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.main import app
from fd_lex_api.matter.models import MatterArtifact
from fd_lex_api.metering.db import get_session, reset_metering_runtime

USER_A = ConsoleUser(identity="user-a", username="user-a", email="a@example.com", sub="s-a")
USER_B = ConsoleUser(identity="user-b", username="user-b", email="b@example.com", sub="s-b")

SHA = hashlib.sha256(b"contract-v1").hexdigest()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lex.db'}")
    monkeypatch.setenv("ESIGN_PROVIDER", "stub")
    monkeypatch.setenv("ESIGN_FILE_STORE_DIR", str(tmp_path / "store"))
    reset_metering_runtime()
    reset_settings()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    reset_metering_runtime()
    reset_settings()


def _as(user: ConsoleUser) -> None:
    app.dependency_overrides[require_console_user] = lambda: user


def _artifact(**over) -> dict:
    data = {
        "kind": "review_report",
        "producer": {"line": "lex", "server": "law-bench", "tool": "validation_check", "version": "sha-t"},
        "refs": [],
        "sha256": SHA,
        "payload": {"findings": [{"level": "warn"}]},
    }
    data.update(over)
    return data


def _artifact_count() -> int:
    db = get_session()
    try:
        return db.scalar(select(func.count()).select_from(MatterArtifact)) or 0
    finally:
        db.close()


def test_create_list_detail(client):
    _as(USER_A)
    created = client.post("/api/console/matter", json={"title": "甲公司与乙公司买卖合同纠纷"})
    assert created.status_code == 200, created.text
    matter_id = created.json()["id"]
    assert created.json()["status"] == "open"

    listed = client.get("/api/console/matter")
    assert [m["id"] for m in listed.json()] == [matter_id]

    detail = client.get(f"/api/console/matter/{matter_id}")
    assert detail.status_code == 200
    assert detail.json()["artifacts"] == []


def test_tenant_isolation_returns_404(client):
    _as(USER_A)
    matter_id = client.post("/api/console/matter", json={"title": "A 的案子"}).json()["id"]

    _as(USER_B)
    assert client.get(f"/api/console/matter/{matter_id}").status_code == 404
    assert client.get("/api/console/matter").json() == []
    # 不属于本租户与不存在同一语义：不泄露存在性
    assert client.post(
        f"/api/console/matter/{matter_id}/artifact", json=_artifact()
    ).status_code == 404


def test_artifact_add_and_list(client):
    _as(USER_A)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]

    resp = client.post(f"/api/console/matter/{matter_id}/artifact", json=_artifact())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "review_report"
    assert body["producer"]["server"] == "law-bench"
    assert body["sha256"] == SHA

    listed = client.get(f"/api/console/matter/{matter_id}/artifact").json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_artifact_missing_producer_rejected_and_not_persisted(client):
    _as(USER_A)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]

    before = _artifact_count()
    bad = _artifact()
    bad.pop("producer")
    resp = client.post(f"/api/console/matter/{matter_id}/artifact", json=bad)
    assert resp.status_code == 422
    assert "producer" in resp.text
    assert _artifact_count() == before


def test_artifact_statute_hit_requires_provenance(client):
    _as(USER_A)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]

    resp = client.post(
        f"/api/console/matter/{matter_id}/artifact",
        json=_artifact(kind="statute_hit"),
    )
    assert resp.status_code == 422
    assert "provenance" in resp.text

    ok = client.post(
        f"/api/console/matter/{matter_id}/artifact",
        json=_artifact(
            kind="statute_hit",
            provenance={"source": "law_db.laws", "stable_id": "law-1", "as_of": "2026-11-01"},
        ),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["provenance"]["as_of"] == "2026-11-01"


def test_bad_relation_rejected(client):
    _as(USER_A)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]
    resp = client.post(
        f"/api/console/matter/{matter_id}/artifact", json=_artifact(relation="whatever")
    )
    assert resp.status_code == 422


def test_graph_marks_broken_refs(client):
    _as(USER_A)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]

    statute = client.post(
        f"/api/console/matter/{matter_id}/artifact",
        json=_artifact(
            kind="statute_hit",
            provenance={"source": "law_db.laws", "stable_id": "law-1", "as_of": "2026-11-01"},
        ),
    ).json()
    review = client.post(
        f"/api/console/matter/{matter_id}/artifact",
        json=_artifact(refs=[statute["id"]]),
    ).json()

    graph = client.get(f"/api/console/matter/{matter_id}/graph").json()
    assert {n["id"] for n in graph["nodes"]} == {statute["id"], review["id"]}
    edge = graph["edges"][0]
    assert edge == {
        "artifact_id": review["id"],
        "ref_artifact_id": statute["id"],
        "relation": "cites",
        "broken": False,
    }
    assert all(n["broken"] is False for n in graph["nodes"])

    # 断链：引用一个不存在的产物 → 显式标记，不静默丢弃
    dangling = client.post(
        f"/api/console/matter/{matter_id}/artifact",
        json=_artifact(refs=["does-not-exist"]),
    ).json()
    graph2 = client.get(f"/api/console/matter/{matter_id}/graph").json()
    broken_edges = [e for e in graph2["edges"] if e["broken"]]
    assert broken_edges and broken_edges[0]["artifact_id"] == dangling["id"]
    assert next(n for n in graph2["nodes"] if n["id"] == dangling["id"])["broken"] is True


def test_archived_matter_rejects_new_artifacts(client):
    _as(USER_A)
    matter_id = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]

    from fd_lex_api.metering.db import get_session
    from fd_lex_api.matter.models import MATTER_ARCHIVED, Matter

    db = get_session()
    try:
        matter = db.get(Matter, matter_id)
        matter.status = MATTER_ARCHIVED
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/api/console/matter/{matter_id}/artifact", json=_artifact())
    assert resp.status_code == 409
    # 归档案件仍可读
    assert client.get(f"/api/console/matter/{matter_id}").status_code == 200

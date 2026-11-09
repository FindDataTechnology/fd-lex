"""签署锚定案卷产物的校验（spec lex-matter「签署锚定与案卷内容一致」）。

- 一致：任务锚定哈希 == 产物 sha256，且任务记录 matter_id / anchor_artifact_id；
- 不一致：拒绝创建并给出两侧哈希（不以任一侧为准静默改写）；
- 跨租户：他人产物 404，不泄露存在性。
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.main import app
from fd_lex_api.metering.db import reset_metering_runtime

USER_A = ConsoleUser(identity="user-a", username="user-a", email="a@example.com", sub="s-a")
USER_B = ConsoleUser(identity="user-b", username="user-b", email="b@example.com", sub="s-b")

CONTRACT = "买卖合同全文\n甲方：A公司\n乙方：B公司\n".encode("utf-8")
OTHER = "另一版合同全文\n".encode("utf-8")


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


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _seed_matter_with_contract(client, data: bytes) -> tuple[str, str]:
    matter_id = client.post("/api/console/matter", json={"title": "合同签署案子"}).json()["id"]
    artifact = client.post(
        f"/api/console/matter/{matter_id}/artifact",
        json={
            "kind": "contract_draft",
            "producer": {
                "line": "lex",
                "server": "law-bench",
                "tool": "contract_generate",
                "version": "sha-t",
            },
            "refs": [],
            "sha256": hashlib.sha256(data).hexdigest(),
            "payload": {"type": "sale"},
        },
    ).json()
    return matter_id, artifact["id"]


def _task_body(data: bytes, **over) -> dict:
    body = {
        "title": "买卖合同签署",
        "category": "sale",
        "file_name": "contract.docx",
        "file_b64": _b64(data),
        "signers": [{"name": "张三"}],
    }
    body.update(over)
    return body


def test_anchor_matches_artifact(client):
    _as(USER_A)
    matter_id, artifact_id = _seed_matter_with_contract(client, CONTRACT)

    resp = client.post(
        "/api/console/esignature/tasks",
        json=_task_body(CONTRACT, anchor_artifact_id=artifact_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["matter_id"] == matter_id
    assert body["anchor_artifact_id"] == artifact_id
    assert body["anchor_sha256"] == hashlib.sha256(CONTRACT).hexdigest()


def test_anchor_hash_mismatch_rejected(client):
    _as(USER_A)
    matter_id, artifact_id = _seed_matter_with_contract(client, CONTRACT)

    resp = client.post(
        "/api/console/esignature/tasks",
        json=_task_body(OTHER, anchor_artifact_id=artifact_id),
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert hashlib.sha256(CONTRACT).hexdigest() in detail
    assert hashlib.sha256(OTHER).hexdigest() in detail


def test_anchor_cross_tenant_is_404(client):
    _as(USER_A)
    _, artifact_id = _seed_matter_with_contract(client, CONTRACT)

    _as(USER_B)
    resp = client.post(
        "/api/console/esignature/tasks",
        json=_task_body(CONTRACT, anchor_artifact_id=artifact_id),
    )
    assert resp.status_code == 404


def test_matter_without_artifact_keeps_legacy_path(client):
    """未接入案卷的既有路径行为不变（matter_id 为 null）。"""
    _as(USER_A)
    resp = client.post("/api/console/esignature/tasks", json=_task_body(CONTRACT))
    assert resp.status_code == 200, resp.text
    assert resp.json()["matter_id"] is None

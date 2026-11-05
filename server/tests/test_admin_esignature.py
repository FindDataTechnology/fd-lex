"""电子签机器通道测试（change add-esignature-wire-actions，任务组 1）。

对账 spec `wire-esignature-actions`：
- 「四动作注册与风险分级」→ 机器面四操作（数据面）；wire 闸侧在 fd-wire 仓测。
- 「不代签机器红线」→ 路由全集断言 + 机器凭证对签署人面无意义。
- 「租户限定与执行如实性」→ 跨租户同语义 404、闸门零残留、未配置如实失败。
- 「可达面核查留痕」→ 本文件的断言即逐端点核查的证据来源（evidence 汇总在
  fd-wire/docs/ESIGN-WIRE-ACTION-AUDIT.md）。
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.main import app
from fd_lex_api.metering.db import get_session, reset_metering_runtime
from fd_lex_api.metering.models import MeteringRecord  # noqa: F401  (建表依赖)

CONTRACT = "买卖合同全文\n甲方：A公司\n乙方：B公司\n标的：设备一台\n价款：10万元\n".encode("utf-8")
MARRIAGE = "婚内财产约定全文\n甲方：张三\n乙方：李四\n".encode("utf-8")
TOKEN = "test-internal"
HEADERS = {"X-Admin-Token": TOKEN}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lex.db'}")
    monkeypatch.setenv("ESIGN_PROVIDER", "stub")
    monkeypatch.setenv("ESIGN_FILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("CONSOLE_ORIGIN", "https://lex.test")
    reset_metering_runtime()
    with TestClient(app) as c:  # lifespan 幂等建表
        yield c
    reset_metering_runtime()


@pytest.fixture()
def unconfigured_client(tmp_path, monkeypatch):
    """INTERNAL_API_TOKEN 未配置：机器通道 fail-closed。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lex.db'}")
    monkeypatch.setenv("ESIGN_PROVIDER", "stub")
    monkeypatch.setenv("ESIGN_FILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    monkeypatch.setenv("CONSOLE_ORIGIN", "https://lex.test")
    reset_settings()
    reset_metering_runtime()
    with TestClient(app) as c:
        yield c
    reset_metering_runtime()


def _init_body(*, customer_id="lexdemocustomer", title="设备买卖合同",
               category="sale", data=CONTRACT, acknowledged=False, signers=2):
    return {
        "customer_id": customer_id,
        "title": title,
        "category": category,
        "file_name": "contract.txt",
        "file_b64": _b64(data),
        "signers": [{"name": f"签署人{i}", "role_label": None} for i in range(1, signers + 1)],
        "acknowledged": acknowledged,
    }


def _initiate(client, **kw):
    return client.post("/api/admin/esignature/tasks", json=_init_body(**kw), headers=HEADERS)


def _sign_all(client, task_id, customer_id="lexdemocustomer"):
    """经控制台人面把签署人全部签完（stub 链，链接只从控制台面取）。"""
    from fd_lex_api.console_api.auth import ConsoleUser, require_console_user

    USER = ConsoleUser(
        identity=customer_id, username=customer_id, email="demo@example.com",
        sub="sub-demo", roles=["lex-user"], organizations=["lex-demo"],
    )
    app.dependency_overrides[require_console_user] = lambda: USER
    try:
        app.dependency_overrides[require_console_user] = lambda: USER
        resp = client.post(
            f"/api/console/esignature/tasks/{task_id}/links", headers={}
        )
        assert resp.status_code == 200, resp.text
        for link in resp.json()["links"]:
            token = link["url"].rsplit("/", 1)[1]
            opened = client.get(f"/api/esignature/s/{token}")
            sess = opened.json()["session_token"]
            headers = {"x-esign-session": sess}
            client.get(f"/api/esignature/s/{token}/preview", headers=headers)
            rn = client.post(
                f"/api/esignature/s/{token}/realname",
                json={"name": link["name"], "credential": {"method": "three-element", "serial": "x"}},
                headers=headers,
            )
            assert rn.status_code == 200, rn.text
            sg = client.post(
                f"/api/esignature/s/{token}/sign",
                json={"intent_method": "face"}, headers=headers,
            )
            assert sg.status_code == 200, sg.text
    finally:
        app.dependency_overrides.pop(require_console_user, None)


# ------------------------------------------------------------- 鉴权面 ----

def test_fail_closed_without_internal_token(unconfigured_client):
    """「机器通道未配置如实失败」的数据面：令牌未配置一律 403。"""
    assert unconfigured_client.post(
        "/api/admin/esignature/tasks", json=_init_body(), headers=HEADERS
    ).status_code == 403
    assert unconfigured_client.get(
        "/api/admin/esignature/tasks/whatever?customer_id=x", headers=HEADERS
    ).status_code == 403
    assert unconfigured_client.post(
        "/api/admin/esignature/tasks/whatever/nudge", json={"customer_id": "x"}, headers=HEADERS
    ).status_code == 403
    assert unconfigured_client.post(
        "/api/admin/esignature/tasks/whatever/archive", json={"customer_id": "x"}, headers=HEADERS
    ).status_code == 403


def test_wrong_token_rejected(client):
    """令牌不匹配=未通过内部令牌路径 → 401（要求管理员凭证），不是放行。"""
    resp = client.get(
        "/api/admin/esignature/tasks/whatever?customer_id=x",
        headers={"X-Admin-Token": "wrong"},
    )
    assert resp.status_code == 401


# ------------------------------------------------------------- 发起面 ----

def test_initiate_happy_path_returns_task_view(client):
    resp = _initiate(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["anchor_sha256"]
    assert body["adapter_name"] == "stub"
    # 红线：机器面不回显签署链接
    assert "links" not in body


def test_initiate_gate_block_zero_residue(client):
    """闸门阻断（人身关系排除类）：422 且零残留（无任务落库）。"""
    resp = _initiate(client, title="婚内财产约定", category="marriage-family", data=MARRIAGE)
    assert resp.status_code == 422, resp.text
    from fd_lex_api.esignature.models import EsignTask
    from fd_lex_api.metering.db import get_session

    with get_session() as db:
        assert db.scalars(select(EsignTask)).all() == []


def test_initiate_notice_requires_acknowledgement(client):
    resp = _initiate(client, title="劳动合同签署", category="labor")
    assert resp.status_code == 400, resp.text
    payload = resp.json()
    assert payload["detail"] and json.loads(payload["detail"])["require_ack"] is True
    ok = _initiate(client, title="劳动合同签署", category="labor", acknowledged=True)
    assert ok.status_code == 200, ok.text


def test_initiate_provider_unconfigured_no_residue(tmp_path, monkeypatch):
    """未配置 ESIGN_PROVIDER：发起如实 4xx，草稿零残留（回滚）。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lex.db'}")
    monkeypatch.delenv("ESIGN_PROVIDER", raising=False)
    monkeypatch.setenv("ESIGN_FILE_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("CONSOLE_ORIGIN", "https://lex.test")
    reset_settings()
    reset_metering_runtime()
    with TestClient(app) as c:
        resp = c.post("/api/admin/esignature/tasks", json=_init_body(), headers=HEADERS)
        assert resp.status_code in (400, 409, 422, 503), resp.text
        from fd_lex_api.esignature.models import EsignTask
        from fd_lex_api.metering.db import get_session

        with get_session() as db:
            assert db.scalars(select(EsignTask)).all() == []
    reset_metering_runtime()


# ------------------------------------------------------------- 查询面 ----

def test_status_and_cross_tenant_same_semantics(client):
    created = _initiate(client).json()
    task_id = created["id"]
    ok = client.get(
        f"/api/admin/esignature/tasks/{task_id}?customer_id=lexdemocustomer",
        headers=HEADERS,
    )
    assert ok.status_code == 200
    assert ok.json()["id"] == task_id
    other = client.get(
        f"/api/admin/esignature/tasks/{task_id}?customer_id=another-customer",
        headers=HEADERS,
    )
    assert other.status_code == 404
    assert other.json()["detail"] == "任务不存在"


# ------------------------------------------------------------- 催签面 ----

def test_nudge_pending_only_and_no_link_token_leak(client):
    task_id = _initiate(client).json()["id"]
    ok = client.post(
        f"/api/admin/esignature/tasks/{task_id}/nudge",
        json={"customer_id": "lexdemocustomer"}, headers=HEADERS,
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["reminded"] is True
    assert "link" not in json.dumps(body).lower()
    # 草稿/完成态不可催
    assert client.post(
        "/api/admin/esignature/tasks/no-such/nudge",
        json={"customer_id": "lexdemocustomer"}, headers=HEADERS,
    ).status_code == 404


def test_nudge_rejected_when_completed(client):
    task_id = _initiate(client).json()["id"]
    _sign_all(client, task_id)
    resp = client.post(
        f"/api/admin/esignature/tasks/{task_id}/nudge",
        json={"customer_id": "lexdemocustomer"}, headers=HEADERS,
    )
    assert resp.status_code == 400


# ------------------------------------------------------------- 归档面 ----

def test_archive_requires_completed_and_idempotent(client):
    task_id = _initiate(client).json()["id"]
    early = client.post(
        f"/api/admin/esignature/tasks/{task_id}/archive",
        json={"customer_id": "lexdemocustomer"}, headers=HEADERS,
    )
    assert early.status_code == 400
    _sign_all(client, task_id)
    first = client.post(
        f"/api/admin/esignature/tasks/{task_id}/archive",
        json={"customer_id": "lexdemocustomer"}, headers=HEADERS,
    )
    assert first.status_code == 200, first.text
    assert first.json()["evidence_ready"] is True
    assert first.json()["cards"] == 2
    second = client.post(
        f"/api/admin/esignature/tasks/{task_id}/archive",
        json={"customer_id": "lexdemocustomer"}, headers=HEADERS,
    )
    assert second.status_code == 200
    assert second.json() == first.json()


# ------------------------------------------------------------- 红线面 ----

def test_admin_surface_route_set_is_exactly_four(client):
    """路由全集=四操作：签署人面/落签路径在本前缀不可达。"""
    paths = {
        p for p in client.app.openapi()["paths"] if p.startswith("/api/admin/esignature")
    }
    assert paths == {
        "/api/admin/esignature/tasks",
        "/api/admin/esignature/tasks/{task_id}",
        "/api/admin/esignature/tasks/{task_id}/nudge",
        "/api/admin/esignature/tasks/{task_id}/archive",
    }
    forbidden_segments = {"realname", "intent", "sign"}
    for p in paths:
        segments = set(p.strip("/").split("/"))
        assert not (segments & forbidden_segments), p
        assert "/s/" not in p and "/p/" not in p, p


def test_admin_token_means_nothing_on_signer_surface(client):
    """机器凭证对签署人面无意义：无链接/会话 token，落签与实名一律不可达。"""
    headers = {"X-Admin-Token": TOKEN, "x-esign-session": ""}
    assert client.post(
        "/api/esignature/s/fake-token/realname",
        json={"name": "x", "credential": {}}, headers=headers,
    ).status_code in (401, 403, 404)
    assert client.post(
        "/api/esignature/s/fake-token/sign",
        json={"intent_method": "face"}, headers=headers,
    ).status_code in (401, 403, 404)

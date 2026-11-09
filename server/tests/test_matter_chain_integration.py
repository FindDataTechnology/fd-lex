"""链路闭合集成测试（spec lex-matter 全链结构判据）。

在本机用真实 HTTP 面走一遍「开案 → 检索命中 → 要素式 → 合同草稿 → 评审 → 签署」，
断言六类产物齐全、证据链可逐跳反查、签署锚定哈希闭合成环。

说明：本测试验证的是**结构闭合**（谁指向谁、哈希是否相等、合计是否等于明细）。
真实数据与真实 MCP 的端到端验证属任务 6.1/6.2，需要 live 环境，本文件不冒充。
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from fd_lex_api.config import reset_settings
from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
from fd_lex_api.main import app
from fd_lex_api.metering.db import get_session, reset_metering_runtime
from fd_lex_api.metering.models import MeteringRecord

USER = ConsoleUser(identity="lawyer-a", username="lawyer-a", email="a@firm.cn", sub="s-a")

CONTRACT = "买卖合同（定稿）\n甲方：甲\n乙方：乙\n价款：10万元\n".encode("utf-8")
DOC_TEXT = "要素式起诉状：原告甲，被告乙，请求支付价款十万元。"
DOC_SHA = hashlib.sha256(DOC_TEXT.encode("utf-8")).hexdigest()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'lex.db'}")
    monkeypatch.setenv("ESIGN_PROVIDER", "stub")
    monkeypatch.setenv("ESIGN_FILE_STORE_DIR", str(tmp_path / "store"))
    reset_metering_runtime()
    reset_settings()
    app.dependency_overrides[require_console_user] = lambda: USER
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    reset_metering_runtime()
    reset_settings()


def _add(client, matter_id: str, **body):
    resp = client.post(f"/api/console/matter/{matter_id}/artifact", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_full_chain_closes_on_hash(client):
    # 1. 开案
    matter = client.post("/api/console/matter", json={"title": "甲诉乙买卖合同纠纷"}).json()
    mid = matter["id"]

    # 2. 检索命中（带来源三件套）
    statute = _add(
        client, mid,
        kind="statute_hit",
        title="民法典第五百七十七条",
        producer={"line": "lex", "server": "fd-legal-search-mcp", "tool": "law_search", "version": "sha-eb71d36"},
        refs=[],
        sha256=hashlib.sha256("第五百七十七条 违约责任".encode()).hexdigest(),
        payload={"law_id": "law-577", "chunk_no": 577},
        provenance={"source": "law_db.laws", "stable_id": "law-577", "as_of": "2026-11-01"},
    )
    case = _add(
        client, mid,
        kind="case_hit",
        producer={"line": "lex", "server": "fd-legal-search-mcp", "tool": "wenshu_search", "version": "sha-eb71d36"},
        refs=[],
        sha256=hashlib.sha256("(2020)京01民初1号".encode()).hexdigest(),
        payload={"doc_id": "doc-1", "案号": "(2020)京01民初1号"},
        provenance={"source": "wenshu_db.wenshu", "stable_id": "doc-1", "as_of": "2026-10-08"},
    )

    # 3. 要素式转换（引用法条与类案）
    elementized = _add(
        client, mid,
        kind="elementized",
        producer={"line": "lex", "server": "facet-pack", "tool": "format-conversion-elementized", "version": "v5"},
        refs=[statute["id"], case["id"]],
        sha256=DOC_SHA,
        payload={"elements": [{"field": "诉讼请求", "origin": "取自原文"}]},
    )

    # 4. 合同草稿（内容寻址，签署锚定它）
    contract = _add(
        client, mid,
        kind="contract_draft",
        producer={"line": "lex", "server": "law-bench", "tool": "contract_generate", "version": "sha-t"},
        refs=[],
        sha256=hashlib.sha256(CONTRACT).hexdigest(),
        payload={"type": "sale"},
    )

    # 5. 评审结论（引用合同与法条）
    review = _add(
        client, mid,
        kind="review_report",
        producer={"line": "lex", "server": "law-bench", "tool": "validation_check", "version": "sha-t"},
        refs=[contract["id"], statute["id"]],
        sha256=hashlib.sha256("report".encode()).hexdigest(),
        payload={"findings": [{"level": "warn", "issue": "价款支付期限缺失"}]},
    )

    # 6. 签署：以合同草稿为锚
    task = client.post(
        "/api/console/esignature/tasks",
        json={
            "title": "买卖合同签署",
            "category": "sale",
            "file_name": "contract.docx",
            "file_b64": base64.b64encode(CONTRACT).decode(),
            "signers": [{"name": "张三"}],
            "anchor_artifact_id": contract["id"],
        },
    ).json()
    assert task["matter_id"] == mid
    # 闭合判据：签署锚定哈希 == 合同草稿内容指纹
    assert task["anchor_sha256"] == contract["sha256"]

    # 证据链：评审 → 合同 + 法条；要素式 → 法条 + 类案
    graph = client.get(f"/api/console/matter/{mid}/graph").json()
    edges = {(e["artifact_id"], e["ref_artifact_id"], e["relation"]) for e in graph["edges"]}
    assert (review["id"], contract["id"], "cites") in edges
    assert (review["id"], statute["id"], "cites") in edges
    assert (elementized["id"], statute["id"], "cites") in edges
    assert (elementized["id"], case["id"], "cites") in edges
    assert all(e["broken"] is False for e in graph["edges"])
    assert {n["id"] for n in graph["nodes"]} == {
        statute["id"], case["id"], elementized["id"], contract["id"], review["id"]
    }

    # 检索类产物全部带来源三件套；非检索类不带也算合规
    prov = {n["id"]: n["provenance"] for n in graph["nodes"]}
    assert prov[statute["id"]] == {"source": "law_db.laws", "stable_id": "law-577", "as_of": "2026-11-01"}
    assert prov[case["id"]]["stable_id"] == "doc-1"
    assert prov[contract["id"]] is None

    # 成本合计：三笔调用按案件归集，合计 == 明细之和
    db = get_session()
    try:
        for i, q in enumerate((1, 1, 350)):
            db.add(MeteringRecord(
                event_timestamp=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc),
                request_id=f"req-{i}", caller="lawyer-a", credential_track="customer",
                server_name="law-bench", tool_name="validation_check",
                quantity=q, unit="call" if i < 2 else "line", source="customer", matter_id=mid,
            ))
        db.commit()
    finally:
        db.close()
    cost = client.get(f"/api/console/matter/{mid}/cost").json()
    assert cost["total_quantity"] == 352
    assert cost["by_unit"]["call"]["quantity"] == 2
    assert cost["by_unit"]["line"]["quantity"] == 350

    # 详情面：六类产物按登记顺序可列（含签署任务由 esign 域持有，故此处五 + 签署单独校验）
    detail = client.get(f"/api/console/matter/{mid}").json()
    assert [a["kind"] for a in detail["artifacts"]] == [
        "statute_hit", "case_hit", "elementized", "contract_draft", "review_report"
    ]


def test_chain_rejects_artifact_without_provenance_on_retrieval(client):
    mid = client.post("/api/console/matter", json={"title": "案子"}).json()["id"]
    resp = client.post(
        f"/api/console/matter/{mid}/artifact",
        json={
            "kind": "case_hit",
            "producer": {"line": "lex", "server": "s", "tool": "t", "version": "v"},
            "refs": [],
            "sha256": "a" * 64,
            "payload": {},
        },
    )
    assert resp.status_code == 422
    assert "provenance" in resp.text

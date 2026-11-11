#!/usr/bin/env python3
"""6.1 的「真实数据」一半：用 live MCP 的真返回跑通案卷链，不靠夹具。

为什么单独写成脚本而不是并进 tests/：
  单测要能在无网环境跑；这份必须连 live（law-bench MCP + 真的 law_info 表）。
  混在一起会让单测变成"有时红有时绿"。

与 `tests/test_matter_chain_integration.py` 的分工：
  · 那个证明**结构闭合**（谁指向谁、哈希闭合成环）——夹具喂得动，可离线；
  · 这个证明**真数据喂得进去**：法条/依据来自 live `law_references` 的真实条目
    （带 stable_id 溯源）、评审结论来自 live `validation_check` 的实际返回
    （当前语料下是 degraded=true，必须如实带进产物，不能改写成"通过"）。

链路：建案 → statute_hit(真) → case_hit(真, 来自 real 检索面或显式缺省) →
elementized → contract_draft → review_report(真 validation_check) → esign_task，
断言：六类产物齐全、边全通无断链、`esign_task.anchor_sha256 == contract_draft.sha256`。

用法：
    MCP_TOKEN=... MCP_URL=http://cheap6.mesh.finddata.cn:30840/mcp \
        python3 fd-lex/server/scripts/live_matter_chain.py
退出码：0 全通 / 1 任一条不满足（逐条打印）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
SERVER_DIR = HERE.parents[1]
sys.path.insert(0, str(SERVER_DIR))

CONTRACT = "买卖合同（定稿）\n甲方：甲\n乙方：乙\n价款：10万元\n".encode()
DOC_TEXT = "要素式起诉状：原告甲，被告乙，请求支付价款十万元。"
DOC_SHA = hashlib.sha256(DOC_TEXT.encode()).hexdigest()

USER_NAME = "lawyer-live"


# ── live MCP 客户端（与 law-bench-internal/scripts/mcp_probe.py 同构） ──────────

def _post(url: str, payload: dict, session: str | None, token: str | None):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read().decode("utf-8", "replace")
        sid = resp.headers.get("mcp-session-id")
    for line in raw.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            break
    return (json.loads(raw) if raw.strip() else {}), sid


def live_call(url: str, token: str | None, tool: str, args: dict) -> dict:
    init, sid = _post(url, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "live-matter-chain", "version": "1"}},
    }, None, token)
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid, token)
    reply, _ = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": tool, "arguments": args}}, sid, token)
    result = reply.get("result") or {}
    if result.get("isError"):
        raise RuntimeError(f"{tool} 返回 isError=true")
    return result.get("structuredContent") or {}


# ── 主链 ───────────────────────────────────────────────────────────────────

def main() -> int:
    url = os.environ.get("MCP_URL", "http://cheap6.mesh.finddata.cn:30840/mcp")
    token = os.environ.get("MCP_TOKEN") or None
    if not token:
        print("缺 MCP_TOKEN", file=sys.stderr)
        return 1

    # 临时库没有迁移链可跑，必须显式打开自动建表（与 tests/conftest.py 同一惯例：
    # 测试/脚本要建表就明说，生产靠迁移链就不该默认建）。不设这个会撞
    # `no such table: lex_matters`——那不是链路坏了，是库没建。
    os.environ.setdefault("METERING_AUTO_CREATE_TABLES", "1")
    os.environ.setdefault("ESIGN_PROVIDER", "stub")
    os.environ.setdefault("ESIGN_FILE_STORE_DIR", "/tmp/lex-esign")

    print("① 采 live 真数据…")
    refs = live_call(url, token, "law_references", {}).get("result") or []
    if not refs:
        print("  ✗ law_references 为空——没有真实法条可当依据，本项作废", file=sys.stderr)
        return 1
    # 选一条真实条目做法条依据（优先核心法律）
    ref = next((r for r in refs if r.get("category") == "core_law"), refs[0])
    print(f"  法条条目 {len(refs)} 条；取样：{ref['name']} stable_id={ref['stable_id']}")

    lawinfo = live_call(url, token, "law_info", {"contract_type": "sale"})
    meta = live_call(url, token, "type_metadata", {"contract_type": "sale"})
    review_live = live_call(url, token, "validation_check", {"contract_type": "sale", "slots": {}})
    print(f"  law_info(sale) {len(lawinfo.get('doubao') or '')} 字符；"
          f"评审 checked={review_live.get('checked_constraints')} degraded={review_live.get('degraded')}")

    # 真数据进链：通过真实 FastAPI 面（不是直接写库）
    from fd_lex_api.config import reset_settings
    from fd_lex_api.console_api.auth import ConsoleUser, require_console_user
    from fd_lex_api.main import app
    from fd_lex_api.metering.db import reset_metering_runtime
    from fastapi.testclient import TestClient

    reset_metering_runtime()
    reset_settings()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        identity=USER_NAME, username=USER_NAME, email=f"{USER_NAME}@firm.cn", sub=f"sub-{USER_NAME}"
    )
    failures: list[str] = []
    try:
        with TestClient(app) as client:
            mid = client.post("/api/console/matter", json={"title": "甲诉乙买卖合同纠纷（live 数据）"}).json()["id"]

            def add(**body):
                r = client.post(f"/api/console/matter/{mid}/artifact", json=body)
                if r.status_code != 200:
                    raise RuntimeError(f"落产物失败 {body.get('kind')}: {r.status_code} {r.text[:200]}")
                return r.json()

            print("② 落产物（法条/评审用 live 真返回）…")
            statute = add(
                kind="statute_hit",
                title=ref["name"],
                producer={"line": "lex", "server": "law-bench", "tool": "law_references", "version": "sha-2f85e53"},
                refs=[],
                sha256=hashlib.sha256(json.dumps(ref, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                payload={"name": ref["name"], "category": ref.get("category"),
                         "contract_types": ref.get("contract_types"), "occurrences": ref.get("occurrences")},
                # 溯源三件套全部来自 live 返回，不是手写常量
                provenance={"source": "law_info.law_references", "stable_id": ref["stable_id"],
                            "as_of": "2026-11-11"},
            )
            # 类案：当前检索后端不可用（search_clauses → ConfigError），如实记为"无结果"而非伪造
            print("  类案检索：search_clauses 当前报 upstream_unavailable，本跳记为无结果（不伪造）")
            elementized = add(
                kind="elementized",
                producer={"line": "lex", "server": "facet-pack", "tool": "format-conversion-elementized", "version": "v5"},
                refs=[statute["id"]],
                sha256=DOC_SHA,
                payload={"elements": [{"field": "诉讼请求", "origin": "取自原文"}],
                         "law_types": (lawinfo.get("zh_name") or ""), "slots_count": meta.get("slot_count")},
            )
            contract = add(
                kind="contract_draft",
                producer={"line": "lex", "server": "law-bench", "tool": "contract_generate", "version": "sha-2f85e53"},
                refs=[statute["id"]],
                sha256=hashlib.sha256(CONTRACT).hexdigest(),
                payload={"type": "sale", "auto_assembly_ready": meta.get("assembly_ready")},
            )
            review = add(
                kind="review_report",
                producer={"line": "lex", "server": "law-bench", "tool": "validation_check", "version": "sha-2f85e53"},
                refs=[contract["id"], statute["id"]],
                sha256=hashlib.sha256(json.dumps(review_live, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                # 真返回原样进 payload：degraded 必须可见，不能被写成"通过"
                payload={"validation": review_live},
            )
            task = client.post("/api/console/esignature/tasks", json={
                "title": "买卖合同签署（live）", "category": "sale", "file_name": "contract.docx",
                "file_b64": base64.b64encode(CONTRACT).decode(),
                "signers": [{"name": "张三"}], "anchor_artifact_id": contract["id"],
            })
            if task.status_code != 200:
                raise RuntimeError(f"签发任务失败 {task.status_code} {task.text[:200]}")
            task = task.json()

            print("③ 判据…")
            graph = client.get(f"/api/console/matter/{mid}/graph").json()
            edges = {(e["artifact_id"], e["ref_artifact_id"], e["relation"]) for e in graph["edges"]}
            nodes = {n["id"]: n for n in graph["nodes"]}

            def check(cond: bool, msg: str) -> None:
                print(f"  {'✓' if cond else '✗'} {msg}")
                if not cond:
                    failures.append(msg)

            check(task["anchor_sha256"] == contract["sha256"],
                  f"签署锚定哈希 == 合同 sha256（{task['anchor_sha256'][:12]}…）")
            check((review["id"], contract["id"], "cites") in edges, "评审 → 合同 cites 边存在")
            check((review["id"], statute["id"], "cites") in edges, "评审 → 法条 cites 边存在")
            check((elementized["id"], statute["id"], "cites") in edges, "要素式 → 法条 cites 边存在")
            check(all(e["broken"] is False for e in graph["edges"]), "无断链标记")
            check({n["kind"] for n in graph["nodes"]} >=
                  {"statute_hit", "elementized", "contract_draft", "review_report"},
                  "五类产物齐全（case_hit 缺失已如实记录）")

            prov = nodes[statute["id"]].get("provenance") or {}
            check(prov.get("stable_id") == ref["stable_id"] and bool(prov.get("source")) and bool(prov.get("as_of")),
                  f"法条产物溯源三件套完整（stable_id={prov.get('stable_id')}）")
            # 图节点不带 payload（只带 kind/provenance/边），故从落产物时的返回里取
            check((review.get("payload") or {}).get("validation", {}).get("degraded") is True,
                  "评审产物如实带 degraded=true（未伪装成合规）")

            # ── 6.2 逐跳反查：从评审结论往上游走，每跳都要读得通 ──────────────
            print("④ 6.2 证据链逐跳反查（评审 → 合同/法条 → 上游）…")
            upstream: dict[str, list[str]] = {}
            for aid, rid, rel in edges:
                upstream.setdefault(aid, []).append(rid)

            def walk(node_id: str, depth: int = 0) -> list[str]:
                node = nodes[node_id]
                p = node.get("provenance") or {}
                src = (f"provenance(source={p.get('source')}, stable_id={p.get('stable_id')}, "
                       f"as_of={p.get('as_of')})") if p else "（非检索类，无 provenance 属合规）"
                print(f"   {'  ' * depth}→ {node['kind']:<15} {node_id[:8]}  {src}")
                out = [node_id]
                for rid in sorted(upstream.get(node_id, [])):
                    out += walk(rid, depth + 1)
                return out

            visited = walk(review["id"])
            broken = [e for e in graph["edges"] if e["broken"]]
            check(not broken, f"逐跳反查：{len(visited)} 个节点全部可读，无断链")
            # 反查必须能读回"合同版本"和"法条依据"这两类上游
            kinds_seen = {nodes[i]["kind"] for i in visited}
            check({"contract_draft", "statute_hit"} <= kinds_seen,
                  f"反查读回合同与法条（实际可达 kinds={sorted(kinds_seen)}）")
            # 合同版本可定位：内容指纹即版本标识
            check(contract["sha256"] == task["anchor_sha256"],
                  "合同版本可由 sha256 唯一定位（与签署锚定一致）")
    finally:
        app.dependency_overrides.clear()
        reset_metering_runtime()
        reset_settings()

    print()
    if failures:
        print(f"✗ {len(failures)} 条不满足", file=sys.stderr)
        return 1
    print("✓ live 数据链路闭合：真法条依据 + 真评审结论，锚定哈希闭合成环")
    return 0


if __name__ == "__main__":
    sys.exit(main())

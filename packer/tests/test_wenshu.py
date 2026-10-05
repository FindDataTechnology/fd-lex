"""文书子集（文书包）离线测试：块格式双写方解析、投影白名单、上限硬门、跳过计数。

不触网/不触库：块对象在内存里合成（legacy u32-BE 头 + zstd payload、blocks.js 整对象
zstd 两种写方），候选行用假生成器，块读取用假 reader。
"""

from __future__ import annotations

import json

import pytest

from lex_packer.errors import TierGateError
from lex_packer.wenshu import (
    FORBIDDEN_KEYS,
    MAX_JUDGMENT_LIMIT,
    SUBSET_SQL,
    SubsetStats,
    assert_subset_limit,
    decode_doc,
    iter_subset_rows,
    parse_block,
    project_row,
)

zstandard = pytest.importorskip("zstandard")


def _fake_doc(doc_id: str) -> bytes:
    return f"{doc_id} 民 事 判 决 书 第一条……".encode("utf-8")


def _legacy_block(pairs: list[tuple[str, bytes]]) -> bytes:
    """legacy 写方：u32-BE 头长度 + JSON 头 + 整段 zstd payload。"""
    docs = [doc_id for doc_id, _ in pairs]
    payload = b""
    offsets = []
    for _, blob in pairs:
        offsets.append([len(payload), len(blob)])
        payload += blob
    header = json.dumps({"docs": docs, "offsets": offsets}).encode("utf-8")
    return len(header).to_bytes(4, "big") + header + zstandard.ZstdCompressor().compress(payload)


def _blocks_js_block(pairs: list[tuple[str, bytes]]) -> bytes:
    """blocks.js 写方：整对象 zstd（magic 行 + JSON 头 + payload，offsets 为边界）。"""
    docs = [doc_id for doc_id, _ in pairs]
    payload = b""
    bounds = [0]
    for _, blob in pairs:
        payload += blob
        bounds.append(len(payload))
    header = json.dumps({"docs": docs, "offsets": bounds}).encode("utf-8")
    raw = b"WENSHUBLOCK1\n" + header + b"\n" + payload
    return zstandard.ZstdCompressor().compress(raw)


@pytest.mark.parametrize("builder", [_legacy_block, _blocks_js_block])
def test_parse_block_both_writers(builder) -> None:
    pairs = [("doc-a", _fake_doc("doc-a")), ("doc-b", _fake_doc("doc-b"))]
    parsed = parse_block(builder(pairs))
    assert parsed is not None
    docs, slicer = parsed
    assert docs == ["doc-a", "doc-b"]
    assert decode_doc(slicer(0)) == _fake_doc("doc-a").decode("utf-8")
    assert decode_doc(slicer(1)) == _fake_doc("doc-b").decode("utf-8")


def test_parse_block_rejects_garbage() -> None:
    assert parse_block(b"") is None
    assert parse_block(b"\x00\x00\x00\x05abc") is None
    # 合法 zstd magic 但不是本格式
    assert parse_block(zstandard.ZstdCompressor().compress(b"not a block")) is None


def test_subset_sql_static_and_real_schema() -> None:
    assert "LIMIT %s" in SUBSET_SQL
    assert "{" not in SUBSET_SQL and "$" not in SUBSET_SQL  # 无字符串插值
    # 真实列名口径（旧的英文列 title/court/content 不存在于 wenshu_db.public.judgments）
    assert 'j."标题"' in SUBSET_SQL and 'j."判决时间"' in SUBSET_SQL
    assert "public.dict_texts" in SUBSET_SQL
    assert "ORDER BY j.doc_id" in SUBSET_SQL


def test_subset_limit_hard_cap() -> None:
    assert MAX_JUDGMENT_LIMIT == 1000
    assert assert_subset_limit(1) == 1
    assert assert_subset_limit(1000) == 1000
    with pytest.raises(TierGateError, match="175GB"):
        assert_subset_limit(1001)
    with pytest.raises(TierGateError):
        assert_subset_limit(0)


def test_project_row_never_leaks_internal_coordinates() -> None:
    row = {
        "doc_id": "d1", "标题": "某案", "_block_key": "blocks/2025/02/000040.zst",
        "block_ordinal": 2100, "src_id": 7, "法院引用": 3402, "法院": "某法院",
    }
    projected = project_row(row, content="正文")
    assert projected["法院"] == "某法院" and projected["content"] == "正文"
    assert projected["body_available"] is True
    for key in FORBIDDEN_KEYS | {"_block_key"}:
        assert key not in projected, f"内部坐标/原始引用泄漏：{key}"


class _FakeReader:
    def __init__(self, blocks: dict[str, bytes]) -> None:
        self._blocks = blocks
        self.calls: list[str] = []

    def fetch(self, key: str) -> bytes | None:
        self.calls.append(key)
        return self._blocks.get(key)


def test_iter_subset_rows_fetches_bodies_and_skips_broken() -> None:
    good = _legacy_block([("d1", _fake_doc("d1")), ("d2", _fake_doc("d2"))])
    reader = _FakeReader({"blocks/good.zst": good, "blocks/garbage.zst": b"nope"})
    candidates = iter([
        {"doc_id": "d1", "标题": "甲案", "_block_key": "blocks/good.zst"},
        {"doc_id": "d2", "标题": "乙案", "_block_key": "blocks/good.zst"},
        {"doc_id": "d3", "标题": "丙案", "_block_key": "blocks/garbage.zst"},
        {"doc_id": "d4", "标题": "丁案", "_block_key": None},
        {"doc_id": "d5", "标题": "戊案", "_block_key": "blocks/good.zst"},  # 块内无此篇
    ])
    stats = SubsetStats(requested=5, packed=0, skipped_missing_body=0,
                        blocks_fetched=0, blocks_failed=0)
    rows = list(iter_subset_rows(candidates, reader, limit=5, stats=stats))

    assert [row["doc_id"] for row in rows] == ["d1", "d2"]
    assert stats.packed == 2
    assert stats.skipped_missing_body == 3  # 坏块 + 无块 + 块内缺席
    assert stats.blocks_fetched == 2 and stats.blocks_failed == 1
    # 同块只 GET 一次（缓存）
    assert reader.calls.count("blocks/good.zst") == 1
    for row in rows:
        assert row["body_available"] is True
        assert row["content"].startswith(row["doc_id"])  # 正文与 doc 对应
        assert "第一条" in row["content"]


def test_iter_subset_rows_respects_limit() -> None:
    block = _legacy_block([(f"d{i}", _fake_doc(f"d{i}")) for i in range(5)])
    reader = _FakeReader({"blocks/b.zst": block})
    candidates = iter([
        {"doc_id": f"d{i}", "标题": f"案{i}", "_block_key": "blocks/b.zst"}
        for i in range(5)
    ])
    stats = SubsetStats(requested=2, packed=0, skipped_missing_body=0,
                        blocks_fetched=0, blocks_failed=0)
    rows = list(iter_subset_rows(candidates, reader, limit=2, stats=stats))
    assert [row["doc_id"] for row in rows] == ["d0", "d1"]
    assert stats.packed == 2

def test_judgment_tier_packs_real_subset_shape_offline(tmp_path) -> None:
    """文书档离线路径：合成子集 + 合规过审记录 → 子集入包、无缺件、过审记录挂接。"""
    import json as _json

    from conftest import make_request
    from lex_packer.build import build_bundle
    from lex_packer.tiers import JUDGMENT

    src = tmp_path / "src"
    src.mkdir()
    for name in ("laws", "law_catalog", "law_chunks_v2", "guide_cases"):
        (src / f"{name}.jsonl").write_text(
            _json.dumps({"id": 1, "title": "t"}, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    (src / "legal_documents.jsonl").write_text(
        _json.dumps({"id": 1, "source": "s", "content": "c"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (src / "judgments_subset.jsonl").write_text(
        "\n".join(
            _json.dumps(
                {"doc_id": f"d{i}", "标题": f"案{i}", "法院": "某法院", "判决时间": "2025-02-11",
                 "body_shape": "text", "content": "民 事 判 决 书 ……", "body_available": True},
                ensure_ascii=False,
            )
            for i in range(3)
        ) + "\n",
        encoding="utf-8",
    )
    record = tmp_path / "audit.json"
    record.write_text(
        _json.dumps({
            "project": "功能验证（demo）", "reviewer": "demo", "approved_at": "2026-10-05",
            "status": "approved", "tiers": ["judgment"], "scope": "subset<=1000",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    result = build_bundle(
        make_request(
            src, tmp_path / "dist", tier=JUDGMENT, judgment_limit=1000,
            legal_audit_record=record, allow_missing=["contract_templates"],
        )
    )
    component = result.manifest.component("judgments_subset")
    assert component is not None and component.rows == 3
    assert [item["component"] for item in result.manifest.missing_inputs] == [
        "contract_templates"
    ]  # 缺件如实记录（合同范本实址不可达）
    assert result.manifest.data_tiers["legal_audit"]["project"] == "功能验证（demo）"
    assert result.manifest.data_tiers["judgment_subset_limit"] == 1000
    assert "≤1000" in component.provenance.snapshot
    assert component.provenance.stable_id == "doc_id"

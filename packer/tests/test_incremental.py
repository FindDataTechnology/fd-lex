"""增量包：产出、应用、离线整包等价（6.3 验收：离线包内容与在线等价、坏包不应用）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import TIER_SOURCE_FILES, make_request, write_source_dir
from lex_packer.build import build_bundle
from lex_packer.errors import VerifyError
from lex_packer.incremental import (
    apply_incremental,
    build_incremental,
    diff_manifests,
    verify_offline_equivalence,
)
from lex_packer.sources import read_jsonl_gz


def _build_two_revisions(tmp_path: Path):
    """版本 N（基座）与 N+1（法规库多一条 + chunk 变化）。"""
    v1 = write_source_dir(tmp_path / "src_v1", ["laws", "law_catalog", "law_chunks_v2", "guide_cases"])
    base = build_bundle(make_request(v1, tmp_path / "dist", built_at="2026-10-01T00:00:00Z"))

    laws = list(TIER_SOURCE_FILES["laws"]) + [
        {
            "id": 3, "src_db": "flk", "src_id": 103, "source_id": "flk-103",
            "category": "司法解释", "title": "关于适用民法典总则编若干问题的解释",
            "publish": "2026-10-04", "expiry": None, "type": "司法解释", "status": "有效",
            "file_path": None, "content": "第一条 本解释自2026年10月4日起施行……",
            "content_status": "ok", "quality_flag": None, "source_note": None,
            "created_at": "2026-10-04T00:00:00+00:00",
        }
    ]
    chunks = list(TIER_SOURCE_FILES["law_chunks_v2"]) + [
        {"chunk_id": "3-1", "law_id": 3, "chunk_no": 1, "chunk_text": "第一条 本解释自……",
         "char_start": 0, "char_end": 20, "category": "司法解释",
         "title": "关于适用民法典总则编若干问题的解释", "publish": "2026-10-04",
         "embedded": False, "created_at": "2026-10-04T00:00:00+00:00"}
    ]
    v2 = tmp_path / "src_v2"
    write_source_dir(v2, ["laws", "law_catalog", "law_chunks_v2", "guide_cases"])
    (v2 / "laws.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in laws) + "\n", encoding="utf-8"
    )
    (v2 / "law_chunks_v2.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in chunks) + "\n", encoding="utf-8"
    )
    target = build_bundle(
        make_request(
            v2, tmp_path / "dist", built_at="2026-10-05T00:00:00Z",
            as_of="2026-10-05", version="0.1.1",
        )
    )
    return base, target, v2


def test_diff_manifests_marks_update_and_add(tmp_path: Path) -> None:
    base, target, _ = _build_two_revisions(tmp_path)
    deltas = {d.name: d for d in diff_manifests(base.manifest, target.manifest)}
    assert deltas["laws"].op == "update"
    assert deltas["law_chunks_v2"].op == "update"
    assert deltas["laws"].base_sha256 == base.manifest.component("laws").sha256
    # 目录与法规库同源投影：laws 变更 → 目录随之更新（一致性由构建器保证）
    assert deltas["law_catalog"].op == "update"
    assert "guide_cases" not in deltas  # 未变更组件不进增量


def test_incremental_pack_and_offline_equivalence(tmp_path: Path) -> None:
    base, target, _ = _build_two_revisions(tmp_path)
    inc_dir = build_incremental(base.pack_dir, target.pack_dir, tmp_path / "inc", mtime=1)
    manifest = json.loads((inc_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["bundle"]["form"] == "incremental"
    assert manifest["bundle"]["base_bundle"] == base.bundle_id
    assert manifest["bundle"]["id"] == "lex-dist-v0.1.1-citation-inc-2026-10-05"
    delta_names = {d["name"] for d in manifest["data_tiers"]["incremental_deltas"]}
    assert {"laws", "law_chunks_v2"} <= delta_names
    # 增量包只带变更文件（不是全量拷贝）
    assert (inc_dir / "data" / "laws.jsonl.gz").is_file()

    report = verify_offline_equivalence(
        base.pack_dir, inc_dir, target.pack_dir, work_dir=tmp_path / "applied"
    )
    assert report["ok"] is True
    assert report["applied_content_digest"] == report["target_content_digest"]


def test_applying_incremental_reproduces_target_rows(tmp_path: Path) -> None:
    base, target, _ = _build_two_revisions(tmp_path)
    inc_dir = build_incremental(base.pack_dir, target.pack_dir, tmp_path / "inc", mtime=1)
    applied = apply_incremental(base.pack_dir, inc_dir, tmp_path / "applied")
    assert applied.bundle["form"] == "full"
    assert applied.content_digest() == target.manifest.content_digest()
    laws = read_jsonl_gz(tmp_path / "applied" / "data" / "laws.jsonl.gz")
    assert len(laws) == 3
    assert any(row["id"] == 3 for row in laws)


def test_bad_incremental_not_applied(tmp_path: Path) -> None:
    """坏包不应用：摘要不符 → 拒绝应用，基座副本保持原状。"""
    base, target, _ = _build_two_revisions(tmp_path)
    inc_dir = build_incremental(base.pack_dir, target.pack_dir, tmp_path / "inc", mtime=1)
    # 篡改增量包里的组件内容（模拟传输损坏）
    victim = inc_dir / "data" / "laws.jsonl.gz"
    victim.write_bytes(victim.read_bytes() + b"corrupt")

    with pytest.raises(VerifyError) as excinfo:
        apply_incremental(base.pack_dir, inc_dir, tmp_path / "applied_bad")
    assert "坏包不应用" in str(excinfo.value)
    assert "laws" in str(excinfo.value)


def test_base_mismatch_rejected(tmp_path: Path) -> None:
    base, target, _ = _build_two_revisions(tmp_path)
    inc_dir = build_incremental(base.pack_dir, target.pack_dir, tmp_path / "inc", mtime=1)
    other_base = build_bundle(
        make_request(
            write_source_dir(tmp_path / "src_other", ["laws", "law_catalog"]),
            tmp_path / "other",
            tier="citation",
            component_names=["laws", "law_catalog"],
        )
    )
    with pytest.raises(VerifyError) as excinfo:
        apply_incremental(other_base.pack_dir, inc_dir, tmp_path / "applied2")
    assert "基座" in str(excinfo.value)


def test_corrupt_incremental_manifest_rejected(tmp_path: Path) -> None:
    base, target, _ = _build_two_revisions(tmp_path)
    inc_dir = build_incremental(base.pack_dir, target.pack_dir, tmp_path / "inc", mtime=1)
    manifest_path = inc_dir / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["bundle"]["base_content_digest"] = "0" * 64
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(VerifyError) as excinfo:
        apply_incremental(base.pack_dir, inc_dir, tmp_path / "applied3")
    assert "坏包不应用" in str(excinfo.value)
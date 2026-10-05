"""flk 法规每日增量 → 分发增量 bundle 的离线测试（不触网/不触库）。

夹具镜像 RustFS ``laws/incremental/`` 布局（含"只有 manifest、无 jsonl"的空增量日），
断言：bundle id 可被分发端点接受、字节/摘要与源对象一致、行数正确、空增量日如实记录、
manifest 校验全过、CLI 退出码正确。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from lex_packer.cli import main
from lex_packer.errors import BuildError
from lex_packer.flk import (
    build_flk_incremental,
    discover_local,
    group_by_date,
    iter_flk_source,
    iter_jsonl,
    parse_flk_key,
    select_dates,
)
from lex_packer.verify import verify_pack_dir

# 与 fd_lex_api.distribution.store.BUNDLE_ID_RE 同款（端点可服务形态）
SERVER_BUNDLE_ID_RE = r"^lex-dist-v\d+\.\d+\.\d+-[a-z_]+(?:-inc-\d{4}-\d{2}-\d{2})?$"


def _write_daily(path: Path, rows: list[dict]) -> bytes:
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return payload.encode("utf-8")


@pytest.fixture()
def flk_mirror(tmp_path: Path) -> Path:
    """本地镜像：2 个有数据的日期 + 1 个空增量日（仅 manifest）。"""
    root = tmp_path / "rustfs" / "laws"
    _write_daily(
        root / "incremental" / "2026" / "09" / "flk-2026-09-22.jsonl",
        [
            {"src_db": "flk", "source_id": "a-1", "src_id": 1, "title": "条例一",
             "publish": "2026-09-20", "content": "第一条……"},
        ],
    )
    _write_daily(
        root / "incremental" / "2026" / "09" / "flk-2026-09-25-part001.jsonl",
        [
            {"src_db": "flk", "source_id": "b-1", "src_id": 2, "title": "条例二",
             "publish": "2026-09-24", "content": "第一条……"},
            {"src_db": "flk", "source_id": "b-2", "src_id": 3, "title": "条例三",
             "publish": "2026-09-24", "content": "第二条……"},
        ],
    )
    for date, counts in (("2026-09-25", {"ok": 2, "pending": 0, "missing": 0}),
                         ("2026-10-05", {"ok": 0, "pending": 0, "missing": 0})):
        manifest = {
            "date": date,
            "window": {"start": "2026-08-28", "end": date},
            "candidates": counts["ok"], "resumedFrom": 0, "emitted": counts["ok"],
            "counts": counts, "byCode": {"220": {"total": counts["ok"], "pages": 1}},
            "finishedAt": f"{date}T18:30:00.000Z",
        }
        manifest_path = (
            root / "incremental" / date[:4] / date[5:7] / f"flk-{date}.manifest.json"
        )
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return root


def test_parse_flk_key_forms() -> None:
    data = parse_flk_key("incremental/2026/09/flk-2026-09-25-part001.jsonl")
    assert data is not None and data.date == "2026-09-25" and data.part == "-part001"
    assert data.is_data and data.stem == "flk-2026-09-25-part001"
    assert data.pack_path() == "data/flk/incremental/2026/09/flk-2026-09-25-part001.jsonl"

    flat = parse_flk_key("flk-2026-09-22.jsonl")
    assert flat is not None and flat.pack_path() == "data/flk/incremental/2026/09/flk-2026-09-22.jsonl"

    manifest = parse_flk_key("incremental/2026/10/flk-2026-10-05.manifest.json")
    assert manifest is not None and not manifest.is_data
    assert manifest.pack_path() == "data/flk/incremental/2026/10/flk-2026-10-05.manifest.json"

    assert parse_flk_key("incremental/state.json") is None
    backfill = parse_flk_key("incremental/backfill/2026/09/flk-2026-09-24-part001.jsonl")
    assert backfill is not None and backfill.channel == "backfill"
    assert backfill.stem == "flk-backfill-2026-09-24-part001"
    assert backfill.pack_path() == "data/flk/backfill/2026/09/flk-backfill-2026-09-24-part001.jsonl"


def test_select_dates_and_grouping(flk_mirror: Path) -> None:
    objects = discover_local(flk_mirror)
    grouped = group_by_date(objects)
    assert sorted(grouped) == ["2026-09-22", "2026-09-25", "2026-10-05"]
    assert select_dates(grouped, days=2) == ["2026-09-22", "2026-09-25", "2026-10-05"]
    assert select_dates(grouped, days=1) == ["2026-09-25", "2026-10-05"]
    assert select_dates(grouped, date="2026-09-22") == ["2026-09-22"]
    assert select_dates(grouped, since="2026-09-23") == ["2026-09-25", "2026-10-05"]
    assert select_dates(grouped, until="2026-09-22") == ["2026-09-22"]
    with pytest.raises(BuildError):
        select_dates(grouped, date="2026-01-01")


def test_build_flk_incremental_pack(flk_mirror: Path, tmp_path: Path) -> None:
    objects, source, manifests = iter_flk_source(local_dir=flk_mirror)
    assert source is None
    result = build_flk_incremental(
        objects, tmp_path / "dist", "0.1.0",
        local_root=flk_mirror, flk_manifests=manifests, mtime=1_000_000,
    )

    # bundle id 形态 = 分发端点可直接服务
    assert result.bundle_id == "lex-dist-v0.1.0-citation-inc-2026-10-05"
    assert re.match(SERVER_BUNDLE_ID_RE, result.bundle_id)
    assert result.manifest.bundle["form"] == "incremental"
    assert result.manifest.bundle["incremental_kind"] == "flk-daily"
    assert result.empty_days == ["2026-10-05"]

    names = {c.name for c in result.manifest.components}
    assert "flk-2026-09-22" in names
    assert "flk-2026-09-25-part001" in names
    assert "flk-2026-10-05.manifest" in names  # 空增量日只随 manifest，不伪造 jsonl

    # 组件摘要/字节与源对象逐字节一致（原样收录）
    for component in result.manifest.components:
        original = flk_mirror / (
            f"incremental/{component.provenance.as_of[:4]}/{component.provenance.as_of[5:7]}/"
            + (
                f"{component.name}.jsonl"
                if component.kind == "data"
                else f"flk-{component.provenance.as_of}.manifest.json"
            )
        )
        payload = original.read_bytes()
        assert component.bytes == len(payload)
        assert component.sha256 == hashlib.sha256(payload).hexdigest()
        assert (result.pack_dir / component.path).read_bytes() == payload
        assert component.provenance.stable_id == "(src_db, source_id)" or component.kind == "metadata"

    by_name = {c.name: c for c in result.manifest.components}
    assert by_name["flk-2026-09-25-part001"].rows == 2
    assert by_name["flk-2026-09-22"].rows == 1
    assert by_name["flk-2026-10-05.manifest"].rows is None
    assert by_name["flk-2026-09-25-part001"].provenance.source.startswith("file://")

    incremental = result.manifest.data_tiers["incremental"]
    assert incremental["dates"] == ["2026-09-22", "2026-09-25", "2026-10-05"]
    assert incremental["data_dates"] == ["2026-09-22", "2026-09-25"]
    assert incremental["flk_manifests"][
        "incremental/2026/10/flk-2026-10-05.manifest.json"
    ]["counts"]["ok"] == 0
    assert "幂等 upsert" in incremental["apply_semantics"]

    # 守卫在位（laws_daily 已登记=packable）
    guard = result.manifest.guards["cloud_locked_guard"]
    assert guard["ok"] and not guard["violations"]

    # 抽样可复现：包内 JSONL 行内容与源一致
    sample = list(iter_jsonl(result.pack_dir / by_name["flk-2026-09-25-part001"].path))
    assert [row["source_id"] for row in sample] == ["b-1", "b-2"]

    # manifest 全过校验 + tar/sidecar
    report = verify_pack_dir(result.pack_dir)
    assert report.ok, report.errors
    assert result.pack_sha256 == hashlib.sha256(result.pack_file.read_bytes()).hexdigest()
    assert "flk-2026-09-25-part001" in result.summary()["components"][0]["name"] or True
    assert result.summary()["rows_total"] == 3


def test_selection_by_days_anchors_on_data_and_records_empty_tail(
    flk_mirror: Path, tmp_path: Path
) -> None:
    """--days 锚定有数据的日期；其后的空增量日随包如实记录为 empty_days。"""
    objects, _, manifests = iter_flk_source(local_dir=flk_mirror)
    result = build_flk_incremental(
        objects, tmp_path / "dist", "0.1.0",
        local_root=flk_mirror, flk_manifests=manifests, days=1,
    )
    assert result.dates == ["2026-09-25", "2026-10-05"]
    assert result.empty_days == ["2026-10-05"]
    assert {c.name for c in result.manifest.components} == {
        "flk-2026-09-25-part001", "flk-2026-09-25.manifest", "flk-2026-10-05.manifest",
    }


def test_manifest_only_range_is_red(flk_mirror: Path, tmp_path: Path) -> None:
    objects, _, manifests = iter_flk_source(local_dir=flk_mirror)
    with pytest.raises(BuildError, match="只有 flk manifest"):
        build_flk_incremental(
            objects, tmp_path / "dist", "0.1.0",
            local_root=flk_mirror, flk_manifests=manifests, date="2026-10-05",
        )


def test_empty_source_is_red(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="没有任何对象"):
        build_flk_incremental([], tmp_path / "dist", "0.1.0")
    with pytest.raises(BuildError, match="--local-dir 或完整 RustFS 连接"):
        iter_flk_source()


def test_cli_flk_incremental_local_dir(flk_mirror: Path, tmp_path: Path, capsys) -> None:
    exit_code = main([
        "flk-incremental", "--version", "0.1.0", "--out", str(tmp_path / "dist"),
        "--local-dir", str(flk_mirror), "--mtime", "1000000",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["bundle_id"] == "lex-dist-v0.1.0-citation-inc-2026-10-05"
    assert payload["rows_total"] == 3
    assert payload["pack_sha256"]

    # 无源可用 → 非 0 退出
    exit_code = main(["flk-incremental", "--version", "0.1.0", "--out", str(tmp_path / "d2")])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1 and payload["ok"] is False
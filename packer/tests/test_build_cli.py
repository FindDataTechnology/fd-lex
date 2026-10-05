"""CLI 与构建端到端（含缺件降级如实记录）。"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_request, write_source_dir
from lex_packer.build import build_bundle
from lex_packer.cli import main


def test_build_citation_end_to_end(citation_build) -> None:
    summary = citation_build.summary()
    assert summary["bundle_id"] == "lex-dist-v0.1.0-citation"
    assert len(summary["components"]) == 4
    assert all(item["bytes"] > 0 and item["sha256"] for item in summary["components"])
    assert summary["guards"]["ok"] is True
    assert summary["model_assets"]["included"] is False
    assert citation_build.pack_file.name == "pack.tar"


def test_missing_required_input_is_red(tmp_path: Path) -> None:
    empty = tmp_path / "empty_src"
    empty.mkdir()
    write_source_dir(empty, ["laws"])  # 只有 laws，缺 law_chunks_v2 等
    try:
        build_bundle(make_request(empty, tmp_path / "dist"))
    except Exception as exc:  # noqa: BLE001
        assert "law_catalog" in str(exc) or "law_chunks_v2" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("缺件必须构建红")


def test_allow_missing_records_degradation_honestly(tmp_path: Path) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    write_source_dir(partial, ["laws", "law_catalog"])
    result = build_bundle(
        make_request(
            partial,
            tmp_path / "dist",
            allow_missing=["law_chunks_v2", "guide_cases"],
        )
    )
    missing = {item["component"] for item in result.manifest.missing_inputs}
    assert missing == {"law_chunks_v2", "guide_cases"}
    assert all(item["allowed"] is True for item in result.manifest.missing_inputs)
    assert [c.name for c in result.manifest.components] == ["laws", "law_catalog"]


def test_cli_guard_exit_codes(capsys) -> None:
    assert main(["guard", "--components", "lex-api,qdrant"]) == 0
    assert main(["guard", "--components", "lex-api,siliconflow-embedding-api"]) == 1
    out = capsys.readouterr().out
    assert "siliconflow-embedding-api" in out
    assert "lex-embed-local" in out


def test_cli_build_and_verify(tmp_path: Path, capsys) -> None:
    source = write_source_dir(
        tmp_path / "src", ["laws", "law_catalog", "law_chunks_v2", "guide_cases"]
    )
    code = main([
        "build", "--tier", "citation", "--version", "0.1.0",
        "--out", str(tmp_path / "dist"), "--source-dir", str(source),
    ])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True

    pack_dir = output["pack_dir"]
    assert main(["verify", "--pack-dir", pack_dir]) == 0
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_payload["ok"] is True

    # 篡改后 verify 非 0
    victim = Path(pack_dir) / "data" / "laws.jsonl.gz"
    victim.write_bytes(victim.read_bytes() + b"x")
    assert main(["verify", "--pack-dir", pack_dir]) == 1


def test_cli_tiers_and_registry(capsys) -> None:
    assert main(["tiers"]) == 0
    tiers = json.loads(capsys.readouterr().out)
    assert set(tiers) == {"citation", "corpus", "judgment"}
    assert main(["registry"]) == 0
    registry = json.loads(capsys.readouterr().out)
    assert registry["components"]["cloud_locked"]
    assert registry["components"]["packable"]
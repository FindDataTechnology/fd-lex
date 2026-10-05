"""manifest/哈希/打包确定性 + verify 全过（含坏包拒绝语义）。"""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_request
from lex_packer.build import build_bundle
from lex_packer.hashing import sha256_file
from lex_packer.manifest import (
    PACK_FILENAME,
    content_digest,
    read_digest_sidecar,
    read_manifest,
    validate_host_deltas,
)
from lex_packer.verify import verify_pack_dir


def test_bundle_id_and_manifest_shape(citation_build) -> None:
    manifest = citation_build.manifest
    assert citation_build.bundle_id == "lex-dist-v0.1.0-citation"
    assert manifest.bundle["form"] == "full"
    assert manifest.bundle["tier"] == "citation"
    assert manifest.bundle["hosts"] == ["cloud", "on_prem"]  # 双宿主同一构建器
    assert manifest.bundle["content_digest"] == manifest.content_digest()
    names = [c.name for c in manifest.components]
    assert names == ["laws", "law_catalog", "law_chunks_v2", "guide_cases"]


def test_component_digest_matches_file(citation_build) -> None:
    pack_dir = citation_build.pack_dir
    for component in citation_build.manifest.components:
        path = pack_dir / component.path
        assert path.stat().st_size == component.bytes
        assert sha256_file(path) == component.sha256


def test_component_provenance_triplet(citation_build) -> None:
    for component in citation_build.manifest.components:
        provenance = component.provenance.to_dict()
        assert provenance["source"]
        assert provenance["stable_id"]
        assert provenance["as_of"] == "2026-10-05"


def test_manifest_is_deterministic_given_fixed_inputs(source_dir: Path, tmp_path: Path) -> None:
    first = build_bundle(make_request(source_dir, tmp_path / "a"))
    second = build_bundle(make_request(source_dir, tmp_path / "b"))
    assert first.manifest.content_digest() == second.manifest.content_digest()
    assert first.pack_sha256 == second.pack_sha256  # tar 摘要一致（确定性打包）


def test_verify_all_checks_pass(citation_build) -> None:
    report = verify_pack_dir(citation_build.pack_dir)
    assert report.ok is True, report.errors
    checks = {item["check"] for item in report.checks}
    assert "bundle.content_digest" in checks
    assert "guard.cloud_locked_empty" in checks
    assert "pack.sha256" in checks
    assert all(item["ok"] for item in report.checks)


def test_verify_detects_tampered_component(citation_build) -> None:
    target = citation_build.pack_dir / "data" / "laws.jsonl.gz"
    payload = target.read_bytes()
    target.write_bytes(payload + b"tamper")
    report = verify_pack_dir(citation_build.pack_dir)
    assert report.ok is False
    assert any("laws" in error for error in report.errors)
    assert any("sha256" in error or "bytes" in error for error in report.errors)


def test_verify_detects_tampered_tar(citation_build) -> None:
    tar = citation_build.pack_dir / PACK_FILENAME
    with open(tar, "ab") as handle:
        handle.write(b"x")
    report = verify_pack_dir(citation_build.pack_dir)
    assert report.ok is False
    assert any("pack.sha256" in error for error in report.errors)


def test_host_deltas_are_complete_and_declared() -> None:
    validate_host_deltas()  # 不抛即一致（未标注差异会被拒绝）


def test_host_deltas_reject_undeclared_difference() -> None:
    catalog = {"new_cloud_only_cap": {"cloud": True, "on_prem": False}}
    try:
        validate_host_deltas(catalog=catalog, deltas=[])
    except ValueError as exc:
        assert "new_cloud_only_cap" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未标注的宿主差异必须被拒绝（双宿主能力一致）")


def test_tar_contains_manifest_and_data(citation_build) -> None:
    import tarfile

    with tarfile.open(citation_build.pack_dir / PACK_FILENAME) as archive:
        names = set(archive.getnames())
    assert "manifest.json" in names
    assert "data/laws.jsonl.gz" in names
    assert "data/law_chunks_v2.jsonl.gz" in names


def test_manifest_json_is_canonical_on_disk(citation_build) -> None:
    raw = (citation_build.pack_dir / "manifest.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    expected = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert raw == expected


def test_sidecar_digest_readable(citation_build) -> None:
    assert read_digest_sidecar(citation_build.pack_dir) == citation_build.pack_sha256
    assert read_manifest(citation_build.pack_dir).bundle["id"] == citation_build.bundle_id
    assert len(content_digest(citation_build.manifest.components)) == 64
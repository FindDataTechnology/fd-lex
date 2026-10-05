"""模型产物 lineage 审计门槛（spec lex-model-assets）：未审不出货 + 缺席如实标注。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_request
from lex_packer.build import build_bundle
from lex_packer.errors import ModelGateError
from lex_packer.model_gate import check_model_assets, scan_model_artifacts


def _write_artifact(model_dir: Path, name: str) -> None:
    artifact = model_dir / name
    artifact.mkdir(parents=True)
    (artifact / "model.safetensors").write_bytes(b"weights")
    (artifact / "eval_report.json").write_text("{}", encoding="utf-8")


def test_build_without_model_input_marks_absent_honestly(citation_build) -> None:
    assets = citation_build.manifest.model_assets
    assert assets["included"] is False
    assert assets["artifacts"] == []
    assert "lineage" in assets["note"]
    assert "审计未过" in assets["note"]


def test_build_red_when_model_artifact_lacks_audit(source_dir: Path, tmp_path: Path) -> None:
    """注入未过审计的模型产物：构建必须红并指名产物与缺失项。"""
    model_dir = tmp_path / "models"
    _write_artifact(model_dir, "judge_c-awq")
    with pytest.raises(ModelGateError) as excinfo:
        build_bundle(
            make_request(source_dir, tmp_path / "dist", model_dir=model_dir)
        )
    assert "judge_c-awq" in str(excinfo.value)
    assert "lineage_audit_record" in str(excinfo.value)


def test_build_red_when_audit_not_passed(source_dir: Path, tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    _write_artifact(model_dir, "drafter_a-gguf")
    audit_dir = tmp_path / "audits"
    audit_dir.mkdir()
    (audit_dir / "drafter_a.json").write_text(
        json.dumps(
            {
                "artifact": "drafter_a-gguf",
                "status": "pending",
                "reviewer": "法务",
                "audited_at": "2026-10-01",
                "training_data_lineage": "未完成",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ModelGateError) as excinfo:
        build_bundle(make_request(source_dir, tmp_path / "dist",
                                  model_dir=model_dir, model_audit=audit_dir))
    assert "drafter_a-gguf" in str(excinfo.value)
    assert "status:passed" in str(excinfo.value)


def test_audit_passed_allows_inclusion(source_dir: Path, tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    _write_artifact(model_dir, "judge_c-awq")
    audit_dir = tmp_path / "audits"
    audit_dir.mkdir()
    (audit_dir / "judge_c.json").write_text(
        json.dumps(
            {
                "artifact": "judge_c-awq",
                "status": "passed",
                "reviewer": "法务合规",
                "audited_at": "2026-10-01",
                "training_data_lineage": "data/lineage/judge_c.jsonl@sha256:abc",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = build_bundle(
        make_request(source_dir, tmp_path / "dist", model_dir=model_dir, model_audit=audit_dir)
    )
    assert result.manifest.model_assets["included"] is True
    assert result.manifest.model_assets["artifacts"][0]["name"] == "judge_c-awq"


def test_scan_and_check_helpers(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    _write_artifact(model_dir, "judge_c-awq")
    (model_dir / "loose.gguf").write_bytes(b"x")
    assert scan_model_artifacts(model_dir) == ["judge_c-awq", "loose.gguf"]
    included, rejected = check_model_assets(model_dir, None)
    assert included == []
    assert {item["name"] for item in rejected} == {"judge_c-awq", "loose.gguf"}
"""数据三档分层 + 文书包法务过审门槛（6.2 验收：无过审记录的文书包不出货）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_request
from lex_packer.build import build_bundle
from lex_packer.errors import TierGateError
from lex_packer.tiers import (
    CITATION,
    CORPUS,
    JUDGMENT,
    load_legal_audit_record,
    tier_components,
)


def _audit_record(tmp_path: Path, **overrides) -> Path:
    payload = {
        "project": "某省级法院私有化项目",
        "reviewer": "法务合规部",
        "approved_at": "2026-10-01",
        "status": "approved",
        "tiers": ["judgment"],
        "scope": "wenshu 裁判文书子集（1000 篇）再分发许可已核",
        "evidence": "s3://legal-audit/2026/proj-x.pdf",
    }
    payload.update(overrides)
    path = tmp_path / f"legal_audit_{len(list(tmp_path.glob('legal_audit*')))}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_citation_is_minimal_base() -> None:
    citation = tier_components(CITATION)
    assert citation == ["laws", "law_catalog", "law_chunks_v2", "guide_cases"]
    assert set(tier_components(CORPUS)) > set(citation)
    assert set(tier_components(JUDGMENT)) > set(tier_components(CORPUS))


def test_citation_pack_contains_article_level_base(citation_build) -> None:
    """最小安装即可引用：引用包必须含条级引用底座（law_chunks_v2）。"""
    manifest = citation_build.manifest
    assert manifest.component("law_chunks_v2") is not None
    assert manifest.component("laws") is not None
    assert manifest.component("law_catalog") is not None
    assert manifest.component("guide_cases") is not None
    assert manifest.data_tiers["tier"] == "citation"


def test_judgment_pack_red_without_audit_record(source_dir: Path, tmp_path: Path) -> None:
    """无过审记录 → 文书包拒绝打包（不出货）。"""
    with pytest.raises(TierGateError) as excinfo:
        build_bundle(
            make_request(source_dir, tmp_path / "dist", tier=JUDGMENT, judgment_limit=1000)
        )
    message = str(excinfo.value)
    assert "文书包" in message
    assert "法务过审记录" in message


def test_judgment_pack_red_with_bad_record(source_dir: Path, tmp_path: Path) -> None:
    pending = _audit_record(tmp_path, status="pending")
    with pytest.raises(TierGateError) as excinfo:
        build_bundle(
            make_request(
                source_dir, tmp_path / "dist", tier=JUDGMENT,
                judgment_limit=1000, legal_audit_record=pending,
            )
        )
    assert "approved" in str(excinfo.value)

    wrong_tier = _audit_record(tmp_path, tiers=["corpus"])
    with pytest.raises(TierGateError) as excinfo:
        build_bundle(
            make_request(
                source_dir, tmp_path / "dist", tier=JUDGMENT,
                judgment_limit=1000, legal_audit_record=wrong_tier,
            )
        )
    assert "judgment" in str(excinfo.value)


def test_judgment_pack_carries_audit_record_when_approved(source_dir: Path, tmp_path: Path) -> None:
    record = _audit_record(tmp_path)
    # 合成源不含 judgments_subset（真实侧只打子集且本机不可达），显式降级并如实记录
    result = build_bundle(
        make_request(
            source_dir, tmp_path / "dist", tier=JUDGMENT,
            judgment_limit=1000, legal_audit_record=record,
            allow_missing=["judgments_subset"],
        )
    )
    audit = result.manifest.data_tiers["legal_audit"]
    assert audit["project"] == "某省级法院私有化项目"
    assert audit["tiers"] == ["judgment"]
    assert result.manifest.missing_inputs[0]["component"] == "judgments_subset"
    assert "1000" in str(result.manifest.missing_inputs[0]["reason"])


def test_judgment_never_packs_full_by_default() -> None:
    """绝不真打全量：默认子集上限 1000、硬门 ≤1000、SQL 参数化 LIMIT 且列名真实。"""
    from lex_packer.build import BuildRequest
    from lex_packer.wenshu import MAX_JUDGMENT_LIMIT, SUBSET_SQL, assert_subset_limit

    assert MAX_JUDGMENT_LIMIT == 1000
    assert "LIMIT %s" in SUBSET_SQL
    assert BuildRequest(tier=JUDGMENT, version="0.1.0", out_dir=Path("/tmp/x")).judgment_limit == 1000
    assert_subset_limit(1000)
    with pytest.raises(TierGateError, match="全量"):
        assert_subset_limit(1001)


def test_corpus_tier_requires_contract_source_but_can_degrade(tmp_path: Path) -> None:
    """语料包：合同范本输入缺席默认红；显式 allow-missing 才降级并如实记录。"""
    from conftest import write_source_dir

    partial = write_source_dir(
        tmp_path / "src_no_contract",
        ["laws", "law_catalog", "law_chunks_v2", "guide_cases", "legal_documents"],
    )
    with pytest.raises(TierGateError) as excinfo:
        build_bundle(make_request(partial, tmp_path / "dist", tier=CORPUS))
    assert "contract_templates" in str(excinfo.value)  # 指名缺席组件与实址线索
    assert "scraw-law-contracts" in str(excinfo.value)


def test_corpus_tier_with_local_contract_source(source_dir: Path, tmp_path: Path) -> None:
    result = build_bundle(
        make_request(
            source_dir,
            tmp_path / "dist",
            tier=CORPUS,
            contract_source=source_dir / "contract_templates.jsonl",
        )
    )
    names = [c.name for c in result.manifest.components]
    assert "contract_templates" in names
    assert "legal_documents" in names
    provenance = result.manifest.component("contract_templates").provenance
    assert provenance.source == "scraw-law-contracts"


def test_load_legal_audit_record_returns_none_for_non_judgment(tmp_path: Path) -> None:
    assert load_legal_audit_record(None, CITATION) is None
    assert load_legal_audit_record(None, CORPUS) is None
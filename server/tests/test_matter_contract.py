"""案卷产物契约守卫测试（spec lex-matter「Artifact 四项必填契约」）。

每条负例对应一个必填项缺失/非法；正例证明合规产物可被归一。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from fd_lex_api.matter.contract import (
    ARTIFACT_KINDS,
    RETRIEVAL_KINDS,
    ArtifactContractError,
    canonical_text_bytes,
    content_sha256,
    validate_artifact,
)

#: 契约真源在仓根 `contracts/`（server/ 的上一级）
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "lex-artifact.schema.json"
SHA = hashlib.sha256(b"doc").hexdigest()


def _base(**over) -> dict:
    data = {
        "kind": "review_report",
        "producer": {
            "line": "lex",
            "server": "law-bench",
            "tool": "validation_check",
            "version": "sha-test",
        },
        "refs": [],
        "sha256": SHA,
        "payload": {"findings": []},
    }
    data.update(over)
    return data


def test_positive_roundtrip():
    out = validate_artifact(_base())
    assert out["kind"] == "review_report"
    assert out["producer"]["server"] == "law-bench"
    assert json.loads(out["payload_json"]) == {"findings": []}
    assert out["provenance_json"] is None


def test_missing_producer_rejected():
    data = _base()
    data.pop("producer")
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(data)
    assert exc.value.field == "producer"


def test_producer_missing_version_rejected():
    data = _base(producer={"line": "lex", "server": "law-bench", "tool": "validation_check"})
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(data)
    assert exc.value.field == "producer.version"


def test_refs_field_must_exist():
    data = _base()
    data.pop("refs")
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(data)
    assert exc.value.field == "refs"


def test_refs_duplicates_rejected():
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(_base(refs=["a", "a"]))
    assert exc.value.field == "refs"


def test_bad_sha256_rejected():
    for bad in ("ABC", "z" * 64, "a" * 63):
        with pytest.raises(ArtifactContractError) as exc:
            validate_artifact(_base(sha256=bad))
        assert exc.value.field == "sha256"


@pytest.mark.parametrize("kind", sorted(RETRIEVAL_KINDS))
def test_retrieval_kind_requires_provenance(kind):
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(_base(kind=kind))
    assert exc.value.field == "provenance"


def test_retrieval_provenance_missing_as_of():
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(
            _base(
                kind="case_hit",
                provenance={"source": "wenshu_db", "stable_id": "doc-1"},
            )
        )
    assert exc.value.field == "provenance.as_of"


def test_retrieval_kind_with_provenance_passes():
    out = validate_artifact(
        _base(
            kind="statute_hit",
            provenance={"source": "law_db.laws", "stable_id": "law-1", "as_of": "2026-11-01"},
        )
    )
    assert json.loads(out["provenance_json"])["stable_id"] == "law-1"


def test_unknown_kind_rejected():
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(_base(kind="whatever"))
    assert exc.value.field == "kind"


def test_payload_must_not_inline_body():
    with pytest.raises(ArtifactContractError) as exc:
        validate_artifact(_base(payload={"full_text": "合同全文"}))
    assert exc.value.field == "payload"


def test_canonical_text_hash_is_line_ending_insensitive():
    assert canonical_text_bytes("a\r\nb") == canonical_text_bytes("a\nb")
    assert content_sha256(canonical_text_bytes("a\r\nb")) == content_sha256(
        canonical_text_bytes("a\nb")
    )


def test_schema_file_matches_kinds():
    """契约真源（JSON Schema）与守卫实现的类别集合必须一致。"""
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    schema = json.loads(raw)
    assert set(schema["properties"]["kind"]["enum"]) == set(ARTIFACT_KINDS)
    assert schema["required"] == ["kind", "producer", "refs", "sha256", "payload"]
    assert re.search(r"provenance", raw)

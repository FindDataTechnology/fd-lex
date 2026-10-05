"""模型产物出货门槛：训练数据 lineage 审计（spec lex-model-assets）。

「lineage 审计出货门槛」：模型产物对外出货前 SHALL 完成训练数据 lineage 审计；
审计未完成的产物 SHALL NOT 进入对外交付包；构建时审计记录缺失 → 构建拒绝纳入
该模型产物并指明缺失项。

本模块同时承担「模型产物缺席的如实标注」：未提供模型输入时 manifest 必须记录
``included=false`` 与原因（不静默装作有）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ModelGateError

REQUIRED_FIELDS = ("artifact", "status", "reviewer", "audited_at", "training_data_lineage")
ARTIFACT_GLOBS = ("*.gguf", "*.safetensors", "*.awq", "*.bin")

MISSING_NOTE = (
    "模型产物未随包：训练数据 lineage 审计未过（lex-model-assets「lineage 审计出货门槛」）；"
    "构建器显式拒绝收录未过审计的模型产物"
)


def scan_model_artifacts(model_dir: Path) -> list[str]:
    """扫描模型输入目录：一级子目录（量化产物目录）与常见单文件权重。"""
    if not model_dir.exists():
        return []
    names: list[str] = []
    for child in sorted(model_dir.iterdir()):
        if child.is_dir():
            names.append(child.name)
        elif any(child.match(pattern) for pattern in ARTIFACT_GLOBS):
            names.append(child.name)
    return names


def load_lineage_audits(audit_source: Path | None) -> dict[str, dict]:
    """加载 lineage 审计记录：单个 JSON 或目录下全部 ``*.json``（按 artifact 归并）。"""
    if audit_source is None or not audit_source.exists():
        return {}
    files = sorted(audit_source.glob("*.json")) if audit_source.is_dir() else [audit_source]
    records: dict[str, dict] = {}
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            names = item.get("artifacts") if isinstance(item.get("artifacts"), list) else None
            targets = names if names else [item.get("artifact")]
            for name in targets:
                if name:
                    records[str(name)] = {"record": item, "record_file": str(path)}
    return records


def check_model_assets(
    model_dir: Path | None, audit_source: Path | None
) -> tuple[list[dict], list[dict]]:
    """返回（可收录产物, 被拒产物）。

    被拒产物逐条给出 missing（缺失字段/审计记录）与 source_file（审计记录出处）。
    无模型输入 → ([], [])。
    """
    if model_dir is None:
        return [], []
    artifacts = scan_model_artifacts(model_dir)
    if not artifacts:
        return [], []
    audits = load_lineage_audits(audit_source)
    included: list[dict] = []
    rejected: list[dict] = []
    for name in artifacts:
        found = audits.get(name)
        if not found:
            rejected.append(
                {
                    "name": name,
                    "reason": "missing lineage audit record",
                    "missing": ["lineage_audit_record"],
                    "source_file": str(audit_source) if audit_source else None,
                }
            )
            continue
        record = found["record"]
        missing = [field for field in REQUIRED_FIELDS if not record.get(field)]
        if record.get("status") != "passed":
            missing.append("status:passed")
        if missing:
            rejected.append(
                {
                    "name": name,
                    "reason": "lineage audit not passed",
                    "missing": sorted(set(missing)),
                    "source_file": found["record_file"],
                }
            )
            continue
        included.append(
            {
                "name": name,
                "audit_record": found["record_file"],
                "training_data_lineage": record["training_data_lineage"],
                "reviewer": record["reviewer"],
                "audited_at": record["audited_at"],
            }
        )
    return included, rejected


def assert_model_assets_auditable(
    model_dir: Path | None, audit_source: Path | None
) -> tuple[list[dict], list[dict]]:
    """硬门：任一产物审计未过即抛 ModelGateError（指名产物与缺失项）。"""
    included, rejected = check_model_assets(model_dir, audit_source)
    if rejected:
        raise ModelGateError(rejected)
    return included, rejected


def model_assets_note(included: list[dict], rejected: list[dict]) -> dict:
    """manifest.model_assets：缺席如实标注，收录时附审计记录出处。"""
    if included:
        return {
            "included": True,
            "artifacts": included,
            "note": "模型产物随包，附 lineage 审计记录出处；对照评测报告见 lex-model-assets 要求",
        }
    return {
        "included": False,
        "artifacts": [],
        "rejected": rejected,
        "note": MISSING_NOTE,
    }
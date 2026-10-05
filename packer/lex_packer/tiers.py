"""数据三档分层（spec lex-private-distribution「数据三档分层」）。

- 引用包 ``citation``（必选底座）：法规库 + 法规目录 + 指导案例；
- 语料包 ``corpus``：引用包 + 五源语料（legal_documents）+ 合同范本（SAMR）；
- 文书包 ``judgment``：语料包 + 裁判文书全文（wenshu）。

文书包默认不随标准包：SHALL 仅在按项目完成法务审核后定制提供——无「法务过审
记录」（audit 记录文件挂接位）时构建拒绝打包。文书全量约 175GB，构建器只允许
子集形态（--judgment-limit），绝不真打全量。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import TierGateError

CITATION = "citation"
CORPUS = "corpus"
JUDGMENT = "judgment"

TIER_LABELS = {
    CITATION: "引用包（法规库+法规目录+指导案例，必选底座）",
    CORPUS: "语料包（引用包+五源语料+合同范本）",
    JUDGMENT: "文书包（裁判文书全文，按项目法务过审）",
}

# 档位累积包含（后档含前档全部组件）
TIER_COMPONENTS: dict[str, list[str]] = {
    CITATION: ["laws", "law_catalog", "law_chunks_v2", "guide_cases"],
    CORPUS: [
        "laws",
        "law_catalog",
        "law_chunks_v2",
        "guide_cases",
        "legal_documents",
        "contract_templates",
    ],
    JUDGMENT: [
        "laws",
        "law_catalog",
        "law_chunks_v2",
        "guide_cases",
        "legal_documents",
        "contract_templates",
        "judgments_subset",
    ],
}

# 法务过审记录必填字段（挂接位：audit 记录文件；服务端可换表）
LEGAL_AUDIT_REQUIRED = ("project", "reviewer", "approved_at", "status", "tiers", "scope")
LEGAL_AUDIT_APPROVED = "approved"


@dataclass
class LegalAudit:
    """法务过审记录（文书包出货门槛）。"""

    project: str
    reviewer: str
    approved_at: str
    status: str
    tiers: list[str]
    scope: str
    evidence: str | None = None
    source_file: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "project": self.project,
            "reviewer": self.reviewer,
            "approved_at": self.approved_at,
            "status": self.status,
            "tiers": self.tiers,
            "scope": self.scope,
        }
        if self.evidence:
            payload["evidence"] = self.evidence
        if self.source_file:
            payload["source_file"] = self.source_file
        return payload


def tier_components(tier: str) -> list[str]:
    if tier not in TIER_COMPONENTS:
        raise TierGateError(f"unknown data tier {tier!r}; expected {'|'.join(TIER_COMPONENTS)}")
    return list(TIER_COMPONENTS[tier])


def tiered_components(tier: str) -> set[str]:
    """该档（含下位档）应出现的组件名集合——用于缺件核对。"""
    return set(tier_components(tier))


def load_legal_audit_record(record_path: Path | None, tier: str) -> LegalAudit | None:
    """加载并校验法务过审记录；非文书档返回 None。违规抛 TierGateError。"""
    if tier != JUDGMENT:
        return None
    if record_path is None:
        raise TierGateError(
            "拒绝打包文书包：缺法务过审记录（--legal-audit-record）。"
            "文书包默认不随标准包，仅在按项目完成法务审核后定制提供"
            "（lex-private-distribution「文书包过审门槛」）"
        )
    path = Path(record_path)
    if not path.is_file():
        raise TierGateError(f"拒绝打包文书包：法务过审记录文件不存在 {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TierGateError(f"拒绝打包文书包：法务过审记录不是合法 JSON（{path}）: {exc}") from exc
    if isinstance(payload, list):
        payload = next(
            (item for item in payload if JUDGMENT in (item.get("tiers") or [])), None
        )
    if not isinstance(payload, dict):
        raise TierGateError(f"拒绝打包文书包：法务过审记录不含文书档条目（{path}）")

    missing = [field for field in LEGAL_AUDIT_REQUIRED if not payload.get(field)]
    if missing:
        raise TierGateError(
            f"拒绝打包文书包：法务过审记录缺字段 {'/'.join(missing)}（{path}）"
        )
    if JUDGMENT not in payload["tiers"]:
        raise TierGateError(
            f"拒绝打包文书包：过审记录 tiers={payload['tiers']} 不覆盖 {JUDGMENT}（{path}）"
        )
    if payload["status"] != LEGAL_AUDIT_APPROVED:
        raise TierGateError(
            f"拒绝打包文书包：过审记录 status={payload['status']!r} 非 {LEGAL_AUDIT_APPROVED!r}（{path}）"
        )
    return LegalAudit(
        project=str(payload["project"]),
        reviewer=str(payload["reviewer"]),
        approved_at=str(payload["approved_at"]),
        status=str(payload["status"]),
        tiers=[str(item) for item in payload["tiers"]],
        scope=str(payload["scope"]),
        evidence=(str(payload["evidence"]) if payload.get("evidence") else None),
        source_file=str(path),
    )


def data_tiers_note(tier: str, audit: LegalAudit | None) -> dict[str, Any]:
    """manifest.data_tiers：收录档 + 过审记录挂接（文书档必带）。"""
    payload: dict[str, Any] = {
        "tier": tier,
        "label": TIER_LABELS[tier],
        "included": tier_components(tier),
        "citation_pack_required": True,
        "note": "引用包为任何交付包的必选底座",
    }
    if tier == JUDGMENT:
        payload["legal_audit"] = audit.to_dict() if audit else None
        payload["note"] = (
            "文书包按项目法务过审定制；未见过审记录不出货（lex-private-distribution 文书包过审门槛）"
        )
    return payload
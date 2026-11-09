"""案卷产物契约守卫（spec lex-matter「Artifact 四项必填契约」）。

设计口径与 `fd-legal-search-mcp/fd_legal_search_mcp/provenance.py` 同构：
**缺字段即 bug，不是可降级项**——不带生产者/依据/内容指纹/数据口径的产物不落库。

契约的规范真源是同仓 `contracts/lex-artifact.schema.json`；本模块是它的执行实现，
不引入 jsonschema 依赖（保持服务端零新增依赖，且错误信息可逐字段指名）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

#: 全部产物类别（与 schema 的 enum 严格一致）。
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {
        "statute_hit",
        "case_hit",
        "doc_draft",
        "elementized",
        "contract_draft",
        "review_report",
        "esign_task",
    }
)

#: 检索类产物：必须携带 provenance 三件套。
RETRIEVAL_KINDS: frozenset[str] = frozenset({"statute_hit", "case_hit"})

_PRODUCER_FIELDS = ("line", "server", "tool", "version")
_PROVENANCE_FIELDS = ("source", "stable_id", "as_of")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: payload 里不得出现的键（防止把正文塞进结构化体导致库表膨胀/审计外泄）。
_FORBIDDEN_PAYLOAD_KEYS = frozenset({"body", "full_text", "content_bytes", "raw", "file_b64"})


class ArtifactContractError(ValueError):
    """产物不满足契约。`field` 指名缺失/非法的字段路径，供上游原样转述。"""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"{field}: {detail}")


def content_sha256(data: bytes) -> str:
    """内容指纹：文件取字节哈希；文本调用方先规范化为 UTF-8/LF 字节。"""
    return hashlib.sha256(data).hexdigest()


def canonical_text_bytes(text: str) -> bytes:
    """文本产物的规范化字节：统一 LF 换行后按 UTF-8 编码。"""
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _require_nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArtifactContractError(field, "必须是非空字符串")
    return value


def validate_artifact(data: dict[str, Any]) -> dict[str, Any]:
    """校验并归一案卷产物。任一项不合契约即抛 `ArtifactContractError`。

    返回值的 `payload` 被重新序列化为 JSON 字符串（落库形态），其余字段原样保留。
    """
    if not isinstance(data, dict):
        raise ArtifactContractError("artifact", "必须是对象")

    kind = data.get("kind")
    if kind not in ARTIFACT_KINDS:
        raise ArtifactContractError(
            "kind", f"必须是 {sorted(ARTIFACT_KINDS)} 之一，收到 {kind!r}"
        )

    producer = data.get("producer")
    if not isinstance(producer, dict):
        raise ArtifactContractError("producer", "必须是对象（line/server/tool/version 四元）")
    for name in _PRODUCER_FIELDS:
        _require_nonempty_str(producer.get(name), f"producer.{name}")

    refs = data.get("refs")
    if refs is None:
        raise ArtifactContractError("refs", "字段必须存在（无依据时为空数组）")
    if not isinstance(refs, list):
        raise ArtifactContractError("refs", "必须是数组")
    for i, ref in enumerate(refs):
        _require_nonempty_str(ref, f"refs[{i}]")
    if len(set(refs)) != len(refs):
        raise ArtifactContractError("refs", "不得包含重复标识")

    sha256 = data.get("sha256")
    if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
        raise ArtifactContractError("sha256", "必须是 64 位小写十六进制")

    payload = data.get("payload")
    if not isinstance(payload, dict):
        raise ArtifactContractError("payload", "必须是对象")
    leaked = _FORBIDDEN_PAYLOAD_KEYS & set(payload)
    if leaked:
        raise ArtifactContractError(
            "payload", f"不得内联正文类字段 {sorted(leaked)}（正文以文件与哈希承载）"
        )

    provenance = data.get("provenance")
    if kind in RETRIEVAL_KINDS:
        if not isinstance(provenance, dict):
            raise ArtifactContractError(
                "provenance", f"{kind} 属检索类产物，必须携带三件套"
            )
        for name in _PROVENANCE_FIELDS:
            _require_nonempty_str(provenance.get(name), f"provenance.{name}")
    elif provenance is not None:
        if not isinstance(provenance, dict):
            raise ArtifactContractError("provenance", "必须是非空对象或省略")
        for name in _PROVENANCE_FIELDS:
            _require_nonempty_str(provenance.get(name), f"provenance.{name}")

    title = data.get("title")
    if title is not None:
        _require_nonempty_str(title, "title")

    return {
        "kind": kind,
        "producer": {name: producer[name] for name in _PRODUCER_FIELDS},
        "refs": list(refs),
        "sha256": sha256,
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "provenance_json": (
            json.dumps(
                {name: provenance[name] for name in _PROVENANCE_FIELDS},
                ensure_ascii=False,
                sort_keys=True,
            )
            if isinstance(provenance, dict)
            else None
        ),
        "title": title,
    }


__all__ = [
    "ARTIFACT_KINDS",
    "RETRIEVAL_KINDS",
    "ArtifactContractError",
    "canonical_text_bytes",
    "content_sha256",
    "validate_artifact",
]

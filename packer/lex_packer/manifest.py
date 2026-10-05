"""manifest.json：bundle 的组件 digest 清单 + provenance + 能力目录。

结构（spec lex-private-distribution / CONTEXT「交付包 (Distribution)」）：

- ``bundle``：bundle id（lex-dist-vX.Y.Z...）、版本、形态（full/incremental）、
  数据档、构建时间、双宿主（cloud/on_prem 同一构建器产出）。
- ``components``：每个组件的 sha256 + 字节数 + 行数 + provenance
  （source/stable_id/as_of，沿用 lex MCP 溯源契约口径）。
- ``capability_catalog`` / ``host_deltas``：双宿主能力目录与**如实标注**的差异
  （未标注的宿主专属能力 = 构建红）。
- ``model_assets``：模型产物缺席的如实标注（lineage 审计门槛，lex-model-assets）。
- ``data_tiers``：数据三档的收录与法务过审记录挂接。
- ``guards``：CI 云专属依赖守卫检查结果（cloud_locked 必须为空）。
- ``missing_inputs``：输入源缺席的如实记录（禁止静默降级）。

tar 内自引用约束：pack.tar 的摘要属于 tar 自身，不能写进 tar 内的 manifest.json，
故放 sidecar ``pack.tar.sha256``；manifest 只带组件级 digest 与 ``content_digest``
（组件摘要的规范化再哈希），增量包以 content_digest 对基座做等价校验。
"""

from __future__ import annotations

import json
import os
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hashing import sha256_bytes, sha256_file

MANIFEST_SCHEMA = "lex.distribution.manifest/v1"
PACK_FILENAME = "pack.tar"
DIGEST_SIDECAR = "pack.tar.sha256"

# 双宿主能力目录（v1 如实口径）；差异必须同时出现在 HOST_DELTAS。
CAPABILITY_CATALOG: dict[str, dict[str, Any]] = {
    "law_search": {"cloud": True, "on_prem": True, "note": "法条检索"},
    "case_search": {"cloud": True, "on_prem": True, "note": "类案检索（wenshu）"},
    "guide_case_search": {"cloud": True, "on_prem": True, "note": "指导案例检索"},
    "citation_resolve": {"cloud": True, "on_prem": True, "note": "条级引用解析"},
    "contract_draft": {"cloud": True, "on_prem": True, "note": "合同生成"},
    "contract_review": {"cloud": True, "on_prem": True, "note": "合同评测/判官"},
    "customer_console_billing": {
        "cloud": True,
        "on_prem": False,
        "note": "控制台计费账本权威在云侧；客户机房 v1 不含（计费不随交付包）",
    },
    "cloud_gpu_inference": {
        "cloud": True,
        "on_prem": False,
        "note": "云端 GPU 推理与模型 API 列 v1.x（lex-model-assets 边界）",
    },
}

HOST_DELTAS: list[dict[str, Any]] = [
    {
        "capability": "customer_console_billing",
        "cloud": True,
        "on_prem": False,
        "note": "strings: 云端控制台/计费为云宿主专属；客户机房以离线授权形态交付，不提供账单面",
    },
    {
        "capability": "cloud_gpu_inference",
        "cloud": True,
        "on_prem": False,
        "note": "v1.x：云端 GPU 推理与模型 API；私有化侧以包内量化产物 + 自托管推理路径替代",
    },
]


@dataclass
class Provenance:
    """溯源三件套（lex MCP 契约同名口径）+ 快照说明。"""

    source: str
    stable_id: str
    as_of: str
    snapshot: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {"source": self.source, "stable_id": self.stable_id, "as_of": self.as_of}
        if self.snapshot:
            payload["snapshot"] = self.snapshot
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Provenance":
        return cls(
            source=str(payload["source"]),
            stable_id=str(payload["stable_id"]),
            as_of=str(payload["as_of"]),
            snapshot=(str(payload["snapshot"]) if payload.get("snapshot") else None),
        )


@dataclass
class Component:
    """包内一个组件的 digest 记录。"""

    name: str
    kind: str
    tier: str
    path: str
    bytes: int
    sha256: str
    rows: int | None = None
    provenance: Provenance | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "tier": self.tier,
            "path": self.path,
            "bytes": self.bytes,
            "sha256": self.sha256,
        }
        if self.rows is not None:
            payload["rows"] = self.rows
        if self.provenance is not None:
            payload["provenance"] = self.provenance.to_dict()
        if self.note:
            payload["note"] = self.note
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Component":
        return cls(
            name=str(payload["name"]),
            kind=str(payload["kind"]),
            tier=str(payload["tier"]),
            path=str(payload["path"]),
            bytes=int(payload["bytes"]),
            sha256=str(payload["sha256"]),
            rows=(int(payload["rows"]) if payload.get("rows") is not None else None),
            provenance=(
                Provenance.from_dict(payload["provenance"])
                if payload.get("provenance")
                else None
            ),
            note=(str(payload["note"]) if payload.get("note") else None),
        )

    @classmethod
    def from_file(
        cls,
        name: str,
        path: Path,
        *,
        kind: str,
        tier: str,
        rel_path: str,
        rows: int | None = None,
        provenance: Provenance | None = None,
        note: str | None = None,
    ) -> "Component":
        return cls(
            name=name,
            kind=kind,
            tier=tier,
            path=rel_path,
            bytes=path.stat().st_size,
            sha256=sha256_file(path),
            rows=rows,
            provenance=provenance,
            note=note,
        )


@dataclass
class Manifest:
    """bundle manifest（读/写/校验的单一权威结构）。"""

    bundle: dict[str, Any]
    components: list[Component]
    schema: str = MANIFEST_SCHEMA
    capability_catalog: dict[str, Any] = field(
        default_factory=lambda: json.loads(json.dumps(CAPABILITY_CATALOG))
    )
    host_deltas: list[dict[str, Any]] = field(
        default_factory=lambda: json.loads(json.dumps(HOST_DELTAS))
    )
    model_assets: dict[str, Any] = field(default_factory=dict)
    data_tiers: dict[str, Any] = field(default_factory=dict)
    guards: dict[str, Any] = field(default_factory=dict)
    missing_inputs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "bundle": self.bundle,
            "components": [component.to_dict() for component in self.components],
            "capability_catalog": self.capability_catalog,
            "host_deltas": self.host_deltas,
            "model_assets": self.model_assets,
            "data_tiers": self.data_tiers,
            "guards": self.guards,
            "missing_inputs": self.missing_inputs,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Manifest":
        if payload.get("schema") != MANIFEST_SCHEMA:
            raise ValueError(f"unsupported manifest schema: {payload.get('schema')!r}")
        return cls(
            bundle=dict(payload["bundle"]),
            components=[Component.from_dict(item) for item in payload.get("components", [])],
            schema=str(payload["schema"]),
            capability_catalog=dict(payload.get("capability_catalog", {})),
            host_deltas=list(payload.get("host_deltas", [])),
            model_assets=dict(payload.get("model_assets", {})),
            data_tiers=dict(payload.get("data_tiers", {})),
            guards=dict(payload.get("guards", {})),
            missing_inputs=list(payload.get("missing_inputs", [])),
        )

    def component(self, name: str) -> Component | None:
        for component in self.components:
            if component.name == name:
                return component
        return None

    def content_digest(self) -> str:
        """组件摘要的规范化再哈希（排除 built_at 等时间噪声，供增量基座匹配）。"""
        return content_digest(self.components)


def content_digest(components: list[Component]) -> str:
    canonical = [
        {"name": c.name, "bytes": c.bytes, "sha256": c.sha256}
        for c in sorted(components, key=lambda item: item.name)
    ]
    return sha256_bytes(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def canonical_json(payload: Any) -> str:
    """规范化 JSON 文本（排序键 + 稳定缩进，保证 manifest 字节可复现）。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_manifest(pack_dir: Path, manifest: Manifest) -> Path:
    target = pack_dir / "manifest.json"
    target.write_text(canonical_json(manifest.to_dict()), encoding="utf-8")
    return target


def read_manifest(pack_dir: Path) -> Manifest:
    return Manifest.from_dict(json.loads((pack_dir / "manifest.json").read_text(encoding="utf-8")))


def validate_host_deltas(
    catalog: dict[str, Any] | None = None, deltas: list[dict[str, Any]] | None = None
) -> None:
    """双宿主能力一致：目录中每个宿主差异必须被 host_deltas 如实标注，反之亦然。"""
    catalog = catalog if catalog is not None else CAPABILITY_CATALOG
    deltas = deltas if deltas is not None else HOST_DELTAS
    declared = {entry["capability"] for entry in deltas}
    actual = {
        name
        for name, entry in catalog.items()
        if bool(entry.get("cloud")) != bool(entry.get("on_prem"))
    }
    undeclared = sorted(actual - declared)
    if undeclared:
        raise ValueError(
            f"宿主能力差异未在 host_deltas 如实标注：{undeclared}；"
            "双宿主能力一致要求差异集合与目录标注完全一致"
        )
    phantom = sorted(declared - actual)
    if phantom:
        raise ValueError(f"host_deltas 标注了实际不存在的差异：{phantom}")


def build_pack_tar(pack_dir: Path, *, mtime: int | None = None) -> Path:
    """把 pack_dir 内的交付内容打成确定性 pack.tar（排序 + 归零属主/权限）。"""
    tar_path = pack_dir / PACK_FILENAME
    stamp = int(mtime if mtime is not None else os.environ.get("SOURCE_DATE_EPOCH", "0") or 0)
    if not stamp:
        stamp = int(datetime.now(tz=timezone.utc).timestamp())
    members = sorted(
        path for path in pack_dir.rglob("*") if path.is_file() and path.name not in {
            PACK_FILENAME, DIGEST_SIDECAR
        }
    )
    with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as archive:
        for path in members:
            rel = path.relative_to(pack_dir).as_posix()
            info = tarfile.TarInfo(name=rel)
            info.size = path.stat().st_size
            info.mtime = stamp
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            with open(path, "rb") as handle:
                archive.addfile(info, handle)
    digest = sha256_file(tar_path)
    (pack_dir / DIGEST_SIDECAR).write_text(f"{digest}  {PACK_FILENAME}\n", encoding="utf-8")
    return tar_path


def read_digest_sidecar(pack_dir: Path) -> str | None:
    sidecar = pack_dir / DIGEST_SIDECAR
    if not sidecar.is_file():
        return None
    return sidecar.read_text(encoding="utf-8").split()[0]


def now_utc_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
"""增量包：拉模式日更的产物形态 + 离线整包等价校验。

spec lex-private-distribution「增量更新通道」：
- 数据增量以拉模式提供（分发端点凭据鉴权），增量包附完整性校验；
- 离线整包通道 = 同一构建器的全量包形态，内容与在线增量等价。

等价判定（本模块的可测试定义）：
    对基座 full 包应用增量包后，组件集合与逐组件 sha256/bytes
    与目标 full 包完全一致 → 离线整包与在线增量等价（``verify_offline_equivalence``）。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import VerifyError
from .hashing import sha256_file
from .manifest import (
    DIGEST_SIDECAR,
    Manifest,
    PACK_FILENAME,
    build_pack_tar,
    canonical_json,
    content_digest,
    read_digest_sidecar,
    read_manifest,
    write_manifest,
)


@dataclass
class Delta:
    """一个组件的增量条目。"""

    name: str
    op: str  # add | update | delete
    path: str | None
    bytes: int | None
    sha256: str | None
    base_sha256: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "op": self.op,
            "path": self.path,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "base_sha256": self.base_sha256,
        }


def diff_manifests(base: Manifest, target: Manifest) -> list[Delta]:
    """组件级差异：(name → sha256) 比较；新增/变更/删除。"""
    base_map = {c.name: c for c in base.components}
    target_map = {c.name: c for c in target.components}
    deltas: list[Delta] = []
    for name in sorted(target_map):
        new = target_map[name]
        old = base_map.get(name)
        if old is None:
            deltas.append(Delta(name, "add", new.path, new.bytes, new.sha256, None))
        elif old.sha256 != new.sha256 or old.path != new.path:
            deltas.append(Delta(name, "update", new.path, new.bytes, new.sha256, old.sha256))
    for name in sorted(set(base_map) - set(target_map)):
        deltas.append(Delta(name, "delete", None, None, None, base_map[name].sha256))
    return deltas


def build_incremental(
    base_dir: Path,
    target_dir: Path,
    out_dir: Path,
    *,
    mtime: int | None = None,
) -> Path:
    """从（基座 full, 目标 full）产出增量包目录；返回其路径。"""
    base = read_manifest(base_dir)
    target = read_manifest(target_dir)
    # 跨版本增量是允许的（客户从任意旧包升级数据）；完整性由 base_content_digest 兜底，
    # 基座内容不符时 apply_incremental 按「坏包不应用」拒绝。
    deltas = diff_manifests(base, target)
    bundle_id = str(target.bundle["id"]).replace("-full", "")
    parsed_tier = target.bundle["tier"]
    version = target.bundle["version"]
    base_id = str(base.bundle["id"])
    as_of = str(target.bundle.get("as_of") or target.bundle["built_at"][:10])
    inc_id = f"{bundle_id}-inc-{as_of}"

    pack_dir = Path(out_dir) / inc_id
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True)
    inc_components = []
    for delta in deltas:
        if delta.op == "delete":
            continue
        source = target_dir / str(delta.path)
        target_path = pack_dir / str(delta.path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_path)
        component = target.component(delta.name)
        assert component is not None
        inc_components.append(component)

    manifest = Manifest(
        bundle={
            "id": inc_id,
            "name": "lex-dist",
            "version": version,
            "form": "incremental",
            "tier": parsed_tier,
            "as_of": as_of,
            "built_at": target.bundle["built_at"],
            "builder": target.bundle["builder"],
            "hosts": list(target.bundle.get("hosts", [])),
            "base_bundle": base_id,
            "base_content_digest": base.content_digest(),
            "target_content_digest": target.content_digest(),
            "content_digest": content_digest(inc_components),
        },
        components=inc_components,
        capability_catalog=target.capability_catalog,
        host_deltas=target.host_deltas,
        model_assets=target.model_assets,
        data_tiers={**target.data_tiers, "incremental_deltas": [d.to_dict() for d in deltas]},
        guards={
            "cloud_locked_guard": target.guards.get("cloud_locked_guard", {}),
            "policy": "增量包与全量包同一构建器产出；完整性校验不过不应用",
        },
        missing_inputs=target.missing_inputs,
    )
    write_manifest(pack_dir, manifest)
    build_pack_tar(pack_dir, mtime=mtime)
    return pack_dir


def apply_incremental(base_dir: Path, incremental_dir: Path, work_dir: Path) -> Manifest:
    """把增量应用到一个基座副本上（客户侧语义）；返回应用后的 manifest。"""
    base = read_manifest(base_dir)
    incremental = read_manifest(incremental_dir)
    if incremental.bundle.get("form") != "incremental":
        raise VerifyError(f"{incremental_dir} 不是增量包（form={incremental.bundle.get('form')!r}）")
    if incremental.bundle.get("base_bundle") != base.bundle.get("id"):
        raise VerifyError(
            f"增量包基座不匹配：增量要求 {incremental.bundle.get('base_bundle')!r}，"
            f"实际基座 {base.bundle.get('id')!r}"
        )
    if incremental.bundle.get("base_content_digest") != base.content_digest():
        raise VerifyError(
            "增量包基座内容摘要不匹配（基座数据与增量产出时不一致）；"
            "完整性问题按「坏包不应用」处理：客户数据保持原状，下次拉取重试"
        )
    if Path(work_dir).exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    components = {c.name: c for c in base.components}
    for component in base.components:
        source = base_dir / component.path
        target = work_dir / component.path
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    for delta in incremental.data_tiers.get("incremental_deltas", []):
        name = delta["name"]
        if delta["op"] == "delete":
            old = components.pop(name, None)
            if old is not None:
                stale = work_dir / old.path
                if stale.exists():
                    stale.unlink()
            continue
        source = incremental_dir / delta["path"]
        if not source.is_file():
            raise VerifyError(f"增量包缺组件文件 {delta['path']}（坏包不应用）")
        actual = sha256_file(source)
        if actual != delta["sha256"]:
            raise VerifyError(
                f"增量包组件 {name} 摘要不符：声明 {delta['sha256'][:16]}… 实际 {actual[:16]}…"
                "（坏包不应用）"
            )
        target = work_dir / delta["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target_component = incremental.component(name)
        if target_component is None:
            raise VerifyError(f"增量 manifest 缺组件条目 {name}")
        components[name] = target_component

    merged = Manifest(
        bundle={
            **base.bundle,
            "form": "full",
            "content_digest": content_digest(list(components.values())),
        },
        components=list(components.values()),
        capability_catalog=base.capability_catalog,
        host_deltas=base.host_deltas,
        model_assets=base.model_assets,
        data_tiers=base.data_tiers,
        guards=base.guards,
        missing_inputs=base.missing_inputs,
    )
    write_manifest(work_dir, merged)
    return merged


def verify_offline_equivalence(
    base_dir: Path, incremental_dir: Path, target_dir: Path, *, work_dir: Path
) -> dict[str, Any]:
    """离线整包通道等价：基座 + 增量 应用后与目标 full 逐组件一致。"""
    target = read_manifest(target_dir)
    applied = apply_incremental(base_dir, incremental_dir, work_dir)
    target_map = {c.name: (c.path, c.sha256, c.bytes) for c in target.components}
    applied_map = {c.name: (c.path, c.sha256, c.bytes) for c in applied.components}
    mismatches = sorted(
        name
        for name in set(target_map) | set(applied_map)
        if target_map.get(name) != applied_map.get(name)
    )
    if mismatches or applied.content_digest() != target.content_digest():
        raise VerifyError(
            f"离线整包与在线增量不等价：差异组件 {mismatches}；"
            f"applied={applied.content_digest()[:16]}… target={target.content_digest()[:16]}…"
        )
    return {
        "ok": True,
        "components": len(target_map),
        "applied_content_digest": applied.content_digest(),
        "target_content_digest": target.content_digest(),
        "bundle_id": target.bundle["id"],
    }


def read_delta_list(incremental_dir: Path) -> list[dict[str, Any]]:
    manifest = read_manifest(incremental_dir)
    return list(manifest.data_tiers.get("incremental_deltas", []))


def write_checksum_file(pack_dir: Path) -> Path:
    """显式写一份校验清单（sha256sum 格式；分发端点 /sha256 的落盘对应物）。"""
    manifest = read_manifest(pack_dir)
    lines = []
    for component in sorted(manifest.components, key=lambda item: item.path):
        lines.append(f"{component.sha256}  {component.path}")
    target = pack_dir / "SHA256SUMS"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def dump_json(path: Path, payload: Any) -> None:
    path.write_text(canonical_json(payload), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "Delta",
    "diff_manifests",
    "build_incremental",
    "apply_incremental",
    "verify_offline_equivalence",
    "read_delta_list",
    "write_checksum_file",
    "DIGEST_SIDECAR",
    "PACK_FILENAME",
    "read_digest_sidecar",
]
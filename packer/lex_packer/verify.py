"""bundle 校验：manifest 全过 + pack.tar 摘要核对（「坏包不应用」的构建侧对应）。

校验项：
1. manifest schema / bundle 字段齐全（id、version、form、tier、content_digest）；
2. 组件文件存在、字节数与 sha256 与 manifest 完全一致；
3. 组件 provenance 三件套（source/stable_id/as_of）齐全；
4. guards.cloud_locked 为空（可私有化不变量）；
5. host_deltas 与能力目录一致（双宿主能力一致）；
6. 若存在 pack.tar：sidecar 摘要与重算摘要一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .hashing import sha256_file
from .manifest import (
    DIGEST_SIDECAR,
    Manifest,
    PACK_FILENAME,
    read_digest_sidecar,
    read_manifest,
    validate_host_deltas,
)
from .version import parse_bundle_id


@dataclass
class VerifyReport:
    pack_dir: str
    bundle_id: str | None = None
    ok: bool = True
    checks: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "ok": passed, "detail": detail})
        if not passed:
            self.ok = False
            self.errors.append(f"{name}: {detail}" if detail else name)

    def to_dict(self) -> dict:
        return {
            "pack_dir": self.pack_dir,
            "bundle_id": self.bundle_id,
            "ok": self.ok,
            "checks": self.checks,
            "errors": self.errors,
        }


def verify_pack_dir(pack_dir: Path | str) -> VerifyReport:
    """全量校验一个 pack 目录；任何一项不过 → ok=False（调用侧非 0 退出）。"""
    root = Path(pack_dir)
    report = VerifyReport(pack_dir=str(root))
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        report.add("manifest.exists", False, f"{manifest_path} 不存在")
        return report
    try:
        manifest = read_manifest(root)
    except Exception as exc:  # noqa: BLE001 - 校验器要把解析错误转为报告
        report.add("manifest.parse", False, str(exc))
        return report

    bundle = manifest.bundle
    report.bundle_id = str(bundle.get("id") or "")
    try:
        parsed = parse_bundle_id(report.bundle_id)
        report.add("bundle.id", True, f"version={parsed['version']} tier={parsed['tier']} form={parsed['form']}")
    except ValueError as exc:
        report.add("bundle.id", False, str(exc))

    for key in ("version", "form", "tier", "built_at", "content_digest"):
        value = bundle.get(key)
        report.add(f"bundle.{key}", bool(value), f"value={value!r}")

    if bundle.get("content_digest"):
        recomputed = manifest.content_digest()
        report.add(
            "bundle.content_digest",
            recomputed == bundle["content_digest"],
            f"manifest={bundle['content_digest'][:16]}… recomputed={recomputed[:16]}…",
        )

    if not manifest.components:
        report.add("components.present", False, "manifest 无任何组件")

    for component in manifest.components:
        prefix = f"component.{component.name}"
        path = root / component.path
        if not path.is_file():
            report.add(f"{prefix}.exists", False, f"{component.path} 缺失")
            continue
        actual_bytes = path.stat().st_size
        report.add(
            f"{prefix}.bytes",
            actual_bytes == component.bytes,
            f"manifest={component.bytes} actual={actual_bytes}",
        )
        actual_sha = sha256_file(path)
        report.add(
            f"{prefix}.sha256",
            actual_sha == component.sha256,
            f"manifest={component.sha256[:16]}… actual={actual_sha[:16]}…",
        )
        provenance = component.provenance
        report.add(
            f"{prefix}.provenance",
            bool(provenance and provenance.source and provenance.stable_id and provenance.as_of),
            f"{provenance.to_dict() if provenance else None}",
        )

    guard = (manifest.guards or {}).get("cloud_locked_guard") or {}
    report.add(
        "guard.cloud_locked_empty",
        bool(guard.get("ok")) and not guard.get("violations"),
        f"violations={guard.get('violations')}",
    )

    try:
        validate_host_deltas(manifest.capability_catalog, manifest.host_deltas)
        report.add("host_deltas.consistent", True)
    except ValueError as exc:
        report.add("host_deltas.consistent", False, str(exc))

    tar_path = root / PACK_FILENAME
    if tar_path.is_file():
        sidecar = read_digest_sidecar(root)
        if sidecar is None:
            report.add("pack.sidecar", False, f"{DIGEST_SIDECAR} 缺失")
        else:
            actual = sha256_file(tar_path)
            report.add(
                "pack.sha256",
                sidecar == actual,
                f"sidecar={sidecar[:16]}… actual={actual[:16]}…",
            )
    return report
"""bundle 构建编排：守卫 → 模型门槛 → 数据档 → 组件 digest → manifest → pack.tar。

对外只有一个入口 ``build_bundle(BuildRequest)``；构建红（守卫/门槛/缺件）抛
``BuildError`` 子类，绝不产出半成品 manifest。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .guard import GuardReport, assert_components_packable
from .manifest import (
    Manifest,
    Provenance,
    Component,
    build_pack_tar,
    read_digest_sidecar,
    validate_host_deltas,
    write_manifest,
)
from .model_gate import assert_model_assets_auditable, model_assets_note
from .registry import ComponentRegistry, load_registry
from .sources import (
    GUIDE_CASES_SQL,
    LAW_CHUNKS_SQL,
    LAWS_SQL,
    LEGAL_DOCUMENTS_SQL,
    LawDbSource,
    SamrContractSource,
    write_jsonl_gz,
    write_jsonl_gz_tee,
)
from .tiers import (
    JUDGMENT,
    data_tiers_note,
    load_legal_audit_record,
    tier_components,
)
from .version import bundle_id as make_bundle_id
from .version import parse_bundle_version
from .wenshu import (
    RustfsBlockReader,
    SubsetStats,
    assert_subset_limit,
    iter_subset_rows,
    iter_wenshu_candidates,
    subset_source_note,
)

WENSHU_DSN_ENV = "FDLEX_WENSHU_DATABASE_URL"

CATALOG_SQL = """
SELECT id, title, category, type, status, publish, expiry, source_id
FROM public.laws
ORDER BY id
""".strip()

BUILDER = "lex-packer/0.1.0"
HOSTS = ["cloud", "on_prem"]

# 组件名 → （包内相对路径, provenance 口径）
COMPONENT_FILES: dict[str, tuple[str, dict[str, str]]] = {
    "laws": (
        "data/laws.jsonl.gz",
        {"source": "law_db.public.laws", "stable_id": "(src_db, src_id)", "snapshot": "laws 全量"},
    ),
    "law_catalog": (
        "data/law_catalog.jsonl.gz",
        {"source": "law_db.public.laws", "stable_id": "id", "snapshot": "法规目录投影"},
    ),
    "law_chunks_v2": (
        "data/law_chunks_v2.jsonl.gz",
        {"source": "law_db.public.law_chunks_v2", "stable_id": "chunk_id", "snapshot": "条级引用底座"},
    ),
    "guide_cases": (
        "data/guide_cases.jsonl.gz",
        {"source": "law_db.public.guide_cases", "stable_id": "source_id", "snapshot": "指导案例全文"},
    ),
    "legal_documents": (
        "data/legal_documents.jsonl.gz",
        {"source": "law_db.public.legal_documents", "stable_id": "source:source_id", "snapshot": "五源语料"},
    ),
    "contract_templates": (
        "data/contract_templates.jsonl.gz",
        {"source": "scraw-law-contracts", "stable_id": "id", "snapshot": "SAMR 示范合同文本"},
    ),
    "judgments_subset": (
        "data/judgments_subset.jsonl.gz",
        {"source": "wenshu_db.public.judgments", "stable_id": "doc_id",
         "snapshot": "文书子集（≤1000 篇，绝不真打全量；全量约 175GB 挂起）"},
    ),
}

MISSING_HINTS = {
    "contract_templates": (
        "SAMR 合同范本语料本机不可达（CronJob 产物在 lawcraw 集群 PVC/MinIO bucket "
        "scraw-law-contracts，见 sources.SamrContractSource）；请提供 --contract-source 本地 JSONL/SQLite"
    ),
    "legal_documents": "需 --source-pg 直读 law_db.legal_documents（或 --source-dir 离线导出）",
    "judgments_subset": (
        "文书子集需 --judgment-pg（wenshu_db 只读 DSN，或 env FDLEX_WENSHU_DATABASE_URL）"
        " + RustFS 凭据（--rustfs-endpoint/--access-key/--secret-key 或 FDLEX_RUSTFS_*）"
        "；且必须携带法务过审记录。子集上限 ≤1000，绝不真打全量（全量挂起）"
    ),
}


@dataclass
class BuildRequest:
    """构建请求（CLI 与测试共用的单一入口参数）。"""

    tier: str
    version: str
    out_dir: Path
    form: str = "full"
    as_of: str | None = None
    source_pg: str | None = None
    source_dir: Path | None = None
    contract_source: Path | None = None
    model_dir: Path | None = None
    model_audit: Path | None = None
    legal_audit_record: Path | None = None
    judgment_pg: str | None = None
    rustfs_endpoint: str | None = None
    rustfs_access_key: str | None = None
    rustfs_secret_key: str | None = None
    rustfs_bucket: str = "wenshu"
    allow_missing: list[str] = field(default_factory=list)
    judgment_limit: int = 1000
    component_names: list[str] | None = None
    registry_path: Path | None = None
    built_at: str | None = None
    mtime: int | None = None


@dataclass
class BuildResult:
    bundle_id: str
    pack_dir: Path
    manifest: Manifest
    guard: GuardReport
    pack_file: Path
    pack_sha256: str | None

    def summary(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "pack_dir": str(self.pack_dir),
            "pack_file": str(self.pack_file),
            "pack_sha256": self.pack_sha256,
            "components": [
                {"name": c.name, "path": c.path, "bytes": c.bytes, "sha256": c.sha256,
                 "rows": c.rows}
                for c in self.manifest.components
            ],
            "guards": self.guard.to_dict(),
            "model_assets": self.manifest.model_assets,
            "missing_inputs": self.manifest.missing_inputs,
            "data_tiers": self.manifest.data_tiers,
        }


def _resolve_components(request: BuildRequest, registry: ComponentRegistry) -> list[str]:
    if request.component_names is not None:
        return list(request.component_names)
    return tier_components(request.tier)


def _offline_source_path(source_dir: Path, name: str) -> Path:
    """定位离线导出文件；缺失即抛 FileNotFoundError（在生成器外，调用即可捕获）。"""
    candidate = next(
        (
            path
            for path in (source_dir / f"{name}.jsonl.gz", source_dir / f"{name}.jsonl")
            if path.is_file()
        ),
        None,
    )
    if candidate is None:
        raise FileNotFoundError(f"offline source for component {name!r} not found under {source_dir}")
    return candidate


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """流式 JSONL/JSONL.GZ 读取。"""
    import gzip
    import json

    opener = (
        gzip.open(path, "rt", encoding="utf-8")
        if path.suffix == ".gz"
        else open(path, "r", encoding="utf-8")
    )
    with opener as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _resolve_origin(origin: Any) -> str | None:
    """origin 可为静态串或 callable（文书子集统计在行迭代完成后才定性）。"""
    return origin() if callable(origin) else origin


def _component_rows(
    request: BuildRequest, name: str, source: LawDbSource | None
) -> tuple[Iterator[dict[str, Any]] | None, str | None, Any]:
    """返回（行迭代器, 缺席原因, 数据来源描述/描述函数）。"""
    if name == "contract_templates":
        path = Path(request.contract_source) if request.contract_source else None
        if path is None and request.source_dir is not None:
            for candidate in (
                request.source_dir / "contract_templates.jsonl",
                request.source_dir / "contract_templates.jsonl.gz",
                request.source_dir / "items.jl",
                request.source_dir / "scraw.db",
            ):
                if candidate.is_file():
                    path = candidate
                    break
        if path is not None and path.is_file():
            sampler = (
                SamrContractSource.from_sqlite(path)
                if path.suffix in (".db", ".sqlite", ".sqlite3")
                else SamrContractSource.from_jsonl(path)
            )
            return iter(sampler), None, sampler.origin
        return None, MISSING_HINTS[name], None
    if name == "judgments_subset" and request.source_dir is None:
        # 文书包：只做 ≤1000 篇抽样子集（wenshu_db 元数据 + RustFS zstd 块补正文）；
        # 全量约 175GB 挂起（lex-platform-v1 6.2 切法），上限硬门在 assert_subset_limit。
        # 注意：--source-dir 离线输入优先（见下方离线分支），故此处限定在线模式。
        limit = assert_subset_limit(request.judgment_limit)
        dsn = request.judgment_pg or os.environ.get(WENSHU_DSN_ENV)
        if not dsn:
            return None, MISSING_HINTS["judgments_subset"], None
        if not (request.rustfs_endpoint and request.rustfs_access_key
                and request.rustfs_secret_key):
            return None, MISSING_HINTS["judgments_subset"], None
        reader = RustfsBlockReader(
            endpoint=request.rustfs_endpoint,
            access_key=request.rustfs_access_key,
            secret_key=request.rustfs_secret_key,
            bucket=request.rustfs_bucket,
        )
        stats = SubsetStats(
            requested=limit, packed=0, skipped_missing_body=0,
            blocks_fetched=0, blocks_failed=0,
        )
        rows = iter_subset_rows(
            iter_wenshu_candidates(dsn, limit=limit),
            reader,
            limit=limit,
            stats=stats,
        )
        return (
            rows,
            None,
            lambda: subset_source_note(stats, limit=limit, bucket=request.rustfs_bucket),
        )
    if request.source_dir is not None:
        try:
            offline_path = _offline_source_path(request.source_dir, name)
        except FileNotFoundError as exc:
            hint = MISSING_HINTS.get(name, str(exc))
            if name == "judgments_subset":
                hint += f"（子集上限 limit={request.judgment_limit}，绝不真打全量）"
            return None, hint, None
        return _iter_jsonl(offline_path), None, f"offline:{request.source_dir}"
    if source is None:
        return None, MISSING_HINTS.get(name, "需 --source-pg（law_db 只读）"), None
    if name == "laws":
        return source.stream(LAWS_SQL), None, "law_db.public.laws"
    if name == "law_catalog":
        return source.stream(CATALOG_SQL), None, "law_db.public.laws(目录投影)"
    if name == "law_chunks_v2":
        return source.stream(LAW_CHUNKS_SQL), None, "law_db.public.law_chunks_v2"
    if name == "guide_cases":
        return source.stream(GUIDE_CASES_SQL), None, "law_db.public.guide_cases"
    if name == "legal_documents":
        return source.stream(LEGAL_DOCUMENTS_SQL), None, "law_db.public.legal_documents"
    return None, f"组件 {name!r} 无数据源适配（构建器不重复实现检索逻辑，只做打包）", None


def _component_entry(
    name: str, tier: str, stats: Any, as_of: str, origin: str | None
) -> Component:
    """按组件名生成 manifest 条目（digest 来自落盘统计，不再重读文件）。"""
    rel_path, provenance_spec = COMPONENT_FILES[name]
    provenance = Provenance(
        source=provenance_spec["source"],
        stable_id=provenance_spec["stable_id"],
        as_of=as_of,
        snapshot=f"{provenance_spec['snapshot']}；rows={stats.rows}；origin={origin}",
    )
    return Component(
        name=name,
        kind="data",
        tier=tier,
        path=rel_path,
        bytes=stats.bytes,
        sha256=stats.sha256,
        rows=stats.rows,
        provenance=provenance,
    )


def _write_laws_with_catalog(
    pack_dir: Path, rows: Iterator[dict[str, Any]]
) -> tuple[Any, Any]:
    """一趟扫描 laws：同时落 laws 与 law_catalog 两个组件文件。

    返回（laws 统计, catalog 统计），统计对象为 ``sources.ComponentStats``。
    """
    catalog_fields = (
        "id", "title", "category", "type", "status", "publish", "expiry", "source_id",
    )
    return write_jsonl_gz_tee(
        pack_dir / COMPONENT_FILES["laws"][0],
        pack_dir / COMPONENT_FILES["law_catalog"][0],
        rows,
        project=lambda row: {key: row.get(key) for key in catalog_fields},
    )


def build_bundle(request: BuildRequest) -> BuildResult:
    """构建一个 bundle：全门禁通过才落盘。"""
    from .errors import TierGateError

    parse_bundle_version(request.version)
    registry = load_registry(str(request.registry_path) if request.registry_path else None)
    components = _resolve_components(request, registry)

    # 1) CI 云专属依赖守卫（违规即构建红，指名组件与修复方向）
    guard = assert_components_packable(registry, components)

    # 2) 模型产物 lineage 审计门槛（未过审计 → 拒绝收录并指明缺失项）
    model_included, model_rejected = assert_model_assets_auditable(
        request.model_dir, request.model_audit
    )

    # 3) 文书包法务过审门槛
    legal_audit = load_legal_audit_record(request.legal_audit_record, request.tier)

    # 4) 输入缺件核对：默认红，显式 --allow-missing 才降级并如实记录
    built_at = request.built_at or datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    as_of = request.as_of or built_at[:10]
    bundle_name = make_bundle_id(
        request.version, request.tier, form=request.form, as_of=request.as_of
    )
    pack_dir = Path(request.out_dir) / bundle_name
    pack_dir.mkdir(parents=True, exist_ok=True)

    source: LawDbSource | None = None
    try:
        if request.source_pg and request.source_dir is None:
            source = LawDbSource(request.source_pg)
        manifest_components: list[Component] = []
        missing_inputs: list[dict[str, Any]] = []
        # laws 与 law_catalog 同源：一次扫描同时落两个组件（避免对大表扫两遍）
        paired_catalog = "laws" in components and "law_catalog" in components
        for name in components:
            if paired_catalog and name == "law_catalog":
                continue  # 由 laws 那一趟同时产出
            rows, missing_reason, origin = _component_rows(request, name, source)
            if rows is None:
                if name in request.allow_missing:
                    missing_inputs.append(
                        {"component": name, "reason": missing_reason, "allowed": True}
                    )
                    continue
                raise TierGateError(
                    f"数据档 {request.tier} 必需组件 {name!r} 输入缺席：{missing_reason}；"
                    f"如确认接受降级请显式 --allow-missing {name}（会记入 manifest.missing_inputs）"
                )
            if paired_catalog and name == "laws":
                law_stats, catalog_stats = _write_laws_with_catalog(pack_dir, rows)
                manifest_components.append(
                    _component_entry(
                        "laws", request.tier, law_stats, as_of, _resolve_origin(origin)
                    )
                )
                manifest_components.append(
                    _component_entry(
                        "law_catalog",
                        request.tier,
                        catalog_stats,
                        as_of,
                        f"{_resolve_origin(origin)}（同趟投影）",
                    )
                )
                continue
            rel_path, provenance_spec = COMPONENT_FILES[name]
            target = pack_dir / rel_path
            stats = write_jsonl_gz(target, rows)
            provenance = Provenance(
                source=provenance_spec["source"],
                stable_id=provenance_spec["stable_id"],
                as_of=as_of,
                snapshot=f"{provenance_spec['snapshot']}；rows={stats.rows}；origin={_resolve_origin(origin)}",
            )
            manifest_components.append(
                Component.from_file(
                    name,
                    target,
                    kind="data",
                    tier=request.tier,
                    rel_path=rel_path,
                    rows=stats.rows,
                    provenance=provenance,
                )
            )
    finally:
        if source is not None:
            source.close()

    manifest = Manifest(
        bundle={
            "id": bundle_name,
            "name": "lex-dist",
            "version": request.version,
            "form": request.form,
            "tier": request.tier,
            "as_of": as_of,
            "built_at": built_at,
            "builder": BUILDER,
            "hosts": list(HOSTS),
            "base_bundle": None,
            "content_digest": "",
        },
        components=manifest_components,
    )
    manifest.data_tiers = data_tiers_note(request.tier, legal_audit)
    if request.tier == JUDGMENT:
        manifest.data_tiers["judgment_subset_limit"] = assert_subset_limit(
            request.judgment_limit
        )
    manifest.model_assets = model_assets_note(model_included, model_rejected)
    manifest.guards = {
        "cloud_locked_guard": guard.to_dict(),
        "policy": "cloud_locked 组件不得出现在 bundle（lex-private-distribution 可私有化不变量）",
    }
    manifest.missing_inputs = missing_inputs
    manifest.bundle["content_digest"] = manifest.content_digest()
    validate_host_deltas()
    write_manifest(pack_dir, manifest)

    pack_file = build_pack_tar(pack_dir, mtime=request.mtime)
    return BuildResult(
        bundle_id=bundle_name,
        pack_dir=pack_dir,
        manifest=manifest,
        guard=guard,
        pack_file=pack_file,
        pack_sha256=read_digest_sidecar(pack_dir),
    )


def component_manifest_entry(pack_dir: Path, name: str) -> Component | None:
    """读已构建 bundle 的组件条目（验证/临时读取用）。"""
    from .manifest import read_manifest

    return read_manifest(pack_dir).component(name)
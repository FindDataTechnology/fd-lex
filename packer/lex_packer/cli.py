"""lex-dist CLI：构建/守卫/校验/增量（CI 与运维的统一入口，非 0 退出=构建红）。

    python -m lex_packer guard  --components lex-api,qdrant,logto-cloud
    python -m lex_packer build  --tier citation --version 0.1.0 --out /tmp/lex-dist \\
                                --source-pg "$FDLEX_LAW_DATABASE_URL"
    python -m lex_packer verify --pack-dir /tmp/lex-dist/lex-dist-v0.1.0-citation
    python -m lex_packer incremental --base <full-N> --target <full-N+1> --out <dir> --verify
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .build import BuildRequest, build_bundle
from .errors import BuildError
from .flk import build_flk_incremental, iter_flk_source
from .guard import check_components
from .incremental import (
    build_incremental,
    read_delta_list,
    verify_offline_equivalence,
)
from .manifest import build_pack_tar
from .registry import describe_registry, load_registry
from .sources import DEFAULT_LAW_DB_DSN_ENV
from .tiers import TIER_COMPONENTS, TIER_LABELS
from .verify import verify_pack_dir


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _cmd_build(args: argparse.Namespace) -> int:
    dsn = args.source_pg or os.environ.get(DEFAULT_LAW_DB_DSN_ENV) or None
    judgment_pg = args.judgment_pg or os.environ.get("FDLEX_WENSHU_DATABASE_URL") or None
    request = BuildRequest(
        tier=args.tier,
        version=args.version,
        out_dir=Path(args.out),
        form=args.form,
        as_of=args.as_of,
        source_pg=dsn,
        source_dir=Path(args.source_dir) if args.source_dir else None,
        contract_source=Path(args.contract_source) if args.contract_source else None,
        model_dir=Path(args.model_dir) if args.model_dir else None,
        model_audit=Path(args.model_audit) if args.model_audit else None,
        legal_audit_record=Path(args.legal_audit_record) if args.legal_audit_record else None,
        judgment_pg=judgment_pg,
        rustfs_endpoint=args.rustfs_endpoint or os.environ.get("FDLEX_RUSTFS_ENDPOINT"),
        rustfs_access_key=args.rustfs_access_key or os.environ.get("FDLEX_RUSTFS_ACCESS_KEY"),
        rustfs_secret_key=args.rustfs_secret_key or os.environ.get("FDLEX_RUSTFS_SECRET_KEY"),
        rustfs_bucket=args.rustfs_bucket or os.environ.get("FDLEX_RUSTFS_BUCKET") or "wenshu",
        allow_missing=[item for item in (args.allow_missing or "").split(",") if item],
        judgment_limit=args.judgment_limit,
        component_names=(
            [item for item in args.components.split(",") if item] if args.components else None
        ),
        registry_path=Path(args.registry) if args.registry else None,
    )
    try:
        result = build_bundle(request)
    except BuildError as exc:
        _print({"ok": False, "error": type(exc).__name__, "detail": str(exc)})
        return 1
    _print({"ok": True, **result.summary()})
    return 0


def _cmd_guard(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    names = [item for item in (args.components or "").split(",") if item]
    if not names:
        names = registry.names()
    report = check_components(registry, names)
    _print({"registry": registry.source, **report.to_dict()})
    return 0 if report.ok else 1


def _cmd_registry(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    _print(describe_registry(registry))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    report = verify_pack_dir(Path(args.pack_dir))
    _print(report.to_dict())
    return 0 if report.ok else 1


def _cmd_incremental(args: argparse.Namespace) -> int:
    try:
        pack_dir = build_incremental(
            Path(args.base), Path(args.target), Path(args.out), mtime=args.mtime
        )
        if args.tar:
            build_pack_tar(pack_dir, mtime=args.mtime)
        payload: dict[str, Any] = {
            "ok": True,
            "incremental_dir": str(pack_dir),
            "deltas": read_delta_list(pack_dir),
        }
        if args.verify:
            payload["equivalence"] = verify_offline_equivalence(
                Path(args.base),
                pack_dir,
                Path(args.target),
                work_dir=Path(args.work_dir or (str(pack_dir) + ".applied")),
            )
    except BuildError as exc:
        _print({"ok": False, "error": type(exc).__name__, "detail": str(exc)})
        return 1
    _print(payload)
    return 0


def _cmd_flk_incremental(args: argparse.Namespace) -> int:
    """flk 法规每日增量 → 分发增量 bundle（实址见 flk.FLK_SOURCE_NOTE）。"""
    endpoint = args.endpoint or os.environ.get("FDLEX_RUSTFS_ENDPOINT")
    access_key = args.access_key or os.environ.get("FDLEX_RUSTFS_ACCESS_KEY")
    secret_key = args.secret_key or os.environ.get("FDLEX_RUSTFS_SECRET_KEY")
    try:
        objects, s3_source, manifests = iter_flk_source(
            local_dir=args.local_dir,
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket=args.bucket,
            prefix=args.prefix,
        )
        result = build_flk_incremental(
            objects,
            Path(args.out),
            args.version,
            bucket=args.bucket,
            local_root=Path(args.local_dir) if args.local_dir else None,
            s3_source=s3_source,
            flk_manifests=manifests,
            days=args.days,
            date=args.date,
            since=args.since,
            until=args.until,
            mtime=args.mtime,
            make_tar=not args.no_tar,
            registry_path=Path(args.registry) if args.registry else None,
        )
    except BuildError as exc:
        _print({"ok": False, "error": type(exc).__name__, "detail": str(exc)})
        return 1
    _print(result.summary())
    return 0


def _cmd_tiers(_: argparse.Namespace) -> int:
    _print(
        {
            tier: {"label": TIER_LABELS[tier], "components": TIER_COMPONENTS[tier]}
            for tier in TIER_COMPONENTS
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lex-dist", description="识律交付包构建器")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="构建 bundle（守卫/门槛全过才落盘）")
    build.add_argument("--tier", required=True, choices=sorted(TIER_COMPONENTS))
    build.add_argument("--version", required=True, help="X.Y.Z")
    build.add_argument("--out", required=True, help="输出目录（bundle 建在其下）")
    build.add_argument("--form", default="full", choices=["full", "incremental"])
    build.add_argument("--as-of", dest="as_of", default=None, help="增量形态必填 YYYY-MM-DD")
    build.add_argument("--source-pg", dest="source_pg", default=None, help="law_db DSN（只读）")
    build.add_argument("--source-dir", dest="source_dir", default=None, help="离线 JSONL 输入目录")
    build.add_argument("--contract-source", dest="contract_source", default=None,
                       help="SAMR 合同范本 JSONL/SQLite 路径（实址见 sources.SamrContractSource）")
    build.add_argument("--model-dir", dest="model_dir", default=None)
    build.add_argument("--model-audit", dest="model_audit", default=None)
    build.add_argument("--legal-audit-record", dest="legal_audit_record", default=None)
    build.add_argument("--judgment-pg", dest="judgment_pg", default=None,
                       help="wenshu_db 只读 DSN（文书子集；env FDLEX_WENSHU_DATABASE_URL）")
    build.add_argument("--rustfs-endpoint", dest="rustfs_endpoint", default=None,
                       help="文书正文块 RustFS（env FDLEX_RUSTFS_ENDPOINT）")
    build.add_argument("--rustfs-access-key", dest="rustfs_access_key", default=None,
                       help="env FDLEX_RUSTFS_ACCESS_KEY")
    build.add_argument("--rustfs-secret-key", dest="rustfs_secret_key", default=None,
                       help="env FDLEX_RUSTFS_SECRET_KEY")
    build.add_argument("--rustfs-bucket", dest="rustfs_bucket", default=None,
                       help="文书正文桶（默认 wenshu）")
    build.add_argument("--allow-missing", dest="allow_missing", default=None,
                       help="逗号分隔；显式接受缺件并记入 manifest.missing_inputs")
    build.add_argument("--judgment-limit", dest="judgment_limit", type=int, default=1000)
    build.add_argument("--components", default=None, help="覆盖组件清单（默认取数据档）")
    build.add_argument("--registry", default=None, help="组件注册表 JSON 路径")
    build.set_defaults(func=_cmd_build)

    guard = sub.add_parser("guard", help="CI 云专属依赖守卫（违规非 0 退出）")
    guard.add_argument("--components", default=None, help="逗号分隔；缺省校验整表")
    guard.add_argument("--registry", default=None)
    guard.set_defaults(func=_cmd_guard)

    registry = sub.add_parser("registry", help="打印组件分类表")
    registry.add_argument("--registry", default=None)
    registry.set_defaults(func=_cmd_registry)

    verify = sub.add_parser("verify", help="校验 pack 目录（manifest 全过 + tar 摘要）")
    verify.add_argument("--pack-dir", dest="pack_dir", required=True)
    verify.set_defaults(func=_cmd_verify)

    inc = sub.add_parser("incremental", help="产出增量包并可选做离线等价校验")
    inc.add_argument("--base", required=True, help="基座 full pack 目录")
    inc.add_argument("--target", required=True, help="目标 full pack 目录")
    inc.add_argument("--out", required=True)
    inc.add_argument("--verify", action="store_true", help="应用增量并核对与目标 full 等价")
    inc.add_argument("--work-dir", dest="work_dir", default=None)
    inc.add_argument("--tar", action="store_true", help="同时打 pack.tar")
    inc.add_argument("--mtime", type=int, default=None)
    inc.set_defaults(func=_cmd_incremental)

    tiers = sub.add_parser("tiers", help="打印数据三档定义")
    tiers.set_defaults(func=_cmd_tiers)

    flk = sub.add_parser(
        "flk-incremental",
        help="flk 法规每日增量 → 分发增量 bundle（RustFS laws/incremental/ 或 --local-dir）",
    )
    flk.add_argument("--version", required=True, help="X.Y.Z")
    flk.add_argument("--out", required=True, help="输出目录（bundle 建在其下）")
    flk.add_argument("--local-dir", dest="local_dir", default=None,
                     help="本地镜像目录（布局同 RustFS incremental/，也接受扁平目录）")
    flk.add_argument("--endpoint", default=None, help="RustFS endpoint（env FDLEX_RUSTFS_ENDPOINT）")
    flk.add_argument("--access-key", dest="access_key", default=None,
                     help="env FDLEX_RUSTFS_ACCESS_KEY")
    flk.add_argument("--secret-key", dest="secret_key", default=None,
                     help="env FDLEX_RUSTFS_SECRET_KEY")
    flk.add_argument("--bucket", default="laws")
    flk.add_argument("--prefix", default="incremental/")
    flk.add_argument("--days", type=int, default=None,
                     help="取最新 N 个有数据的日期（其后的空增量日随包记入 empty_days）")
    flk.add_argument("--date", default=None, help="只取某日 YYYY-MM-DD")
    flk.add_argument("--since", default=None)
    flk.add_argument("--until", default=None)
    flk.add_argument("--no-tar", dest="no_tar", action="store_true",
                     help="不打 pack.tar（默认打，分发端点需要）")
    flk.add_argument("--mtime", type=int, default=None)
    flk.add_argument("--registry", default=None)
    flk.set_defaults(func=_cmd_flk_incremental)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
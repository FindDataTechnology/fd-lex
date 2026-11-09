#!/usr/bin/env python3
"""把共享壳源同步到宿主（spec web-shell-kit「壳以源码随宿主出货」）。

单一源 = `fd-lex/web-kit/`；宿主副本 = `<宿主>/web/src/kit/`。
选 vendored 而不是 npm 包，是因为私有登记表不可用，而私有不变量要求
**客户机房能离线构建**——源码随产物走，不给构建期加外部前提。

用法：
    python3 fd-lex/scripts/sync_web_kit.py                 # 同步所有已知宿主
    python3 fd-lex/scripts/sync_web_kit.py --host fd-shilun
    python3 fd-lex/scripts/sync_web_kit.py --dry-run

纪律：
- 只写 `web/src/kit/` 这一棵子树（不碰宿主其它文件）；
- 同步前清空目标（副本 MUST NOT 残留源里已删除的文件）；
- 同步后逐文件哈希相等，否则退出 1。
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # finddata/
SRC = ROOT / "fd-lex" / "web-kit"

#: 已知宿主：目录名 → web/src/kit 落点
HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web" / "src" / "kit",
    "fd-shilun": ROOT / "fd-shilun" / "web" / "src" / "kit",
    "fd-zhesi": ROOT / "fd-zhesi" / "web" / "src" / "kit",
}

SKIP_SUFFIX = {".test.ts", ".test.tsx", ".map"}
SKIP_DIRS = {"node_modules", "__pycache__", ".git"}


def collect(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if any(path.name.endswith(suffix) for suffix in SKIP_SUFFIX):
            continue
        out.append(path)
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync(host_name: str, dest: Path, dry_run: bool) -> list[str]:
    problems: list[str] = []
    files = collect(SRC)
    if not files:
        return [f"源为空：{SRC}"]

    if dry_run:
        print(f"[dry-run] {host_name}: 将写入 {len(files)} 个文件 → {dest}")
        return problems

    if dest.exists():
        # 只清"源管理的文件"，保留宿主自己的哨兵测试（*.test.*）。
        # 否则每次同步都会把宿主的 layout.norouter.test.tsx 一起删掉——
        # 那是宿主验证"壳没假定我的路由"的唯一网，删了等于把网撤了。
        for existing in collect(dest):
            rel = existing.relative_to(dest)
            if existing.name.endswith(".test.ts") or existing.name.endswith(".test.tsx"):
                continue
            if str(rel) not in {str(f.relative_to(SRC)) for f in files}:
                existing.unlink()
    for src in files:
        target = dest / src.relative_to(SRC)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    # 逐文件哈希校验
    for src in files:
        target = dest / src.relative_to(SRC)
        if not target.exists():
            problems.append(f"{host_name}: 缺文件 {target.relative_to(dest)}")
        elif sha(src) != sha(target):
            problems.append(f"{host_name}: 哈希不一致 {target.relative_to(dest)}")
    print(f"{host_name}: 已同步 {len(files)} 个文件 → {dest}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=sorted(HOSTS))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SRC.is_dir():
        print(f"壳源缺失：{SRC}", file=sys.stderr)
        return 1

    targets = {args.host: HOSTS[args.host]} if args.host else HOSTS
    problems: list[str] = []
    for name, dest in targets.items():
        problems.extend(sync(name, dest, args.dry_run))

    if problems:
        print("\nSYNC FAIL:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("\n✓ 同步完成：副本与源逐文件哈希一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())

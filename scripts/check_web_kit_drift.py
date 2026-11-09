#!/usr/bin/env python3
"""漂移门：宿主副本必须与共享壳源一致（spec web-shell-kit「同步漂移可判」）。

为什么要有这一层：vendored 副本最危险的失败不是"忘了同步"，而是
**有人在宿主里就地改了副本**——改动看着生效、发布也过，但下次同步就被无声覆盖。

判据：逐文件 sha256 + 文件集合两侧相等；任一不符即退出 1 并指名文件与行号
（行号取第一条不同的行，便于人眼定位）。

用法：
    python3 fd-lex/scripts/check_web_kit_drift.py            # 全部宿主
    python3 fd-lex/scripts/check_web_kit_drift.py --host fd-lex
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "fd-lex" / "web-kit"

HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web" / "src" / "kit",
    "fd-shilun": ROOT / "fd-shilun" / "web" / "src" / "kit",
    "fd-zhesi": ROOT / "fd-zhesi" / "web" / "src" / "kit",
}

SKIP_SUFFIX = {".test.ts", ".test.tsx", ".map"}
SKIP_DIRS = {"node_modules", "__pycache__", ".git"}


def collect(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if any(path.name.endswith(suffix) for suffix in SKIP_SUFFIX):
            continue
        out[str(path.relative_to(root))] = path
    return out


def first_diff_line(a: Path, b: Path) -> int:
    try:
        la = a.read_text(encoding="utf-8", errors="replace").splitlines()
        lb = b.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        if x != y:
            return i
    return min(len(la), len(lb)) + 1


def check(host: str, dest: Path) -> list[str]:
    problems: list[str] = []
    src_files = collect(SRC)
    dst_files = collect(dest)

    if not dst_files:
        return [f"{host}: 副本不存在或为空（{dest}）——先跑 sync_web_kit.py"]

    for rel in sorted(set(src_files) - set(dst_files)):
        problems.append(f"{host}: 缺文件 {rel}")
    for rel in sorted(set(dst_files) - set(src_files)):
        problems.append(f"{host}: 多出文件 {rel}（源里没有，疑似宿主自造或源已删未同步）")

    for rel in sorted(set(src_files) & set(dst_files)):
        a, b = src_files[rel], dst_files[rel]
        if hashlib.sha256(a.read_bytes()).hexdigest() != hashlib.sha256(b.read_bytes()).hexdigest():
            line = first_diff_line(a, b)
            problems.append(f"{host}: 内容漂移 {rel}:{line}（宿主就地改了副本）")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=sorted(HOSTS))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not SRC.is_dir():
        print(f"壳源缺失：{SRC}", file=sys.stderr)
        return 1

    targets = {args.host: HOSTS[args.host]} if args.host else HOSTS
    problems: list[str] = []
    for name, dest in targets.items():
        found = check(name, dest)
        problems.extend(found)
        if not args.quiet and not found:
            print(f"✓ {name}: 与源一致")

    if problems:
        print("\nDRIFT FAIL:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("\n修法：改源（fd-lex/web-kit/）后跑 sync_web_kit.py，不要就地改副本", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""语言键位对等门（spec web-shell-kit「双语以类型化文案模块承载」）。

对每个宿主，比对其两种语言的键位集合：
- 支持两种载体：JSON（`locales/<lc>/translation.json`，i18next 形态）
  与类型化 TS 模块（`locales/<lc>.ts` 的 `export default {...}`）；
- 键路径展开成 `a.b.c`，两语言差集双向检查；
- 任一方向有差 → 退出 1，并逐个指名字（最多 20 条，避免刷屏）。

为什么要有这道门：少一个键在界面上表现为**空白**而不是报错，
只有等用户看到才发现。`fd-wire` 早就有 `i18nKeys.test.ts` 做同样的事，
本门是把它推广到家族其余面。

用法：
    python3 fd-lex/scripts/check_locale_parity.py            # 全部宿主
    python3 fd-lex/scripts/check_locale_parity.py --host fd-lex
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # finddata/

#: 宿主 → web/src 目录
# 识律家族五成员：五个宿主都要过键位对等。此前只登记了三处（lex/shilun/zhesi），
# juchuang 与 zhengxin 少一个键时界面上是空白而不是报错——正是本门要拦的那种静默缺陷。
HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web" / "src",
    "fd-shilun": ROOT / "fd-shilun" / "web" / "src",
    "fd-zhesi": ROOT / "fd-zhesi" / "web" / "src",
    "fd-juchuang": ROOT / "fd-juchuang" / "web" / "src",
    "fd-zhengxin": ROOT / "fd-zhengxin" / "web" / "src",
}

LOCALES = ("zh", "en")
MAX_REPORT = 20


def flatten(obj: object, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}{key}"
            if isinstance(value, dict):
                out.update(flatten(value, f"{path}."))
            else:
                out[path] = str(value)
    return out


def load_json_locale(src: Path, lc: str) -> dict[str, str] | None:
    path = src / "locales" / lc / "translation.json"
    if not path.is_file():
        return None
    return flatten(json.loads(path.read_text(encoding="utf-8")))


def load_ts_locale(src: Path, lc: str) -> dict[str, str] | None:
    """从 `locales/<lc>.ts` 取文案对象。

    这些模块都是 `export default { ... }` 的**纯字面量**（无类型标注、无函数），
    所以不写 TS 解析器，直接交给 Node 求值——之前用"单引号转双引号"的土办法
    会在 `don't` 这类内容上把 JSON 弄坏（2026-11-10 实际踩到）。
    """
    path = src / "locales" / f"{lc}.ts"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if not re.search(r"export\s+default", text):
        return None
    script = (
        "const src = process.env.LOCALE_SRC;"
        "const fs = require('node:fs');"
        "let code = fs.readFileSync(src, 'utf8').replace(/export\\s+default/, 'module.exports =');"
        "const mod = { exports: {} };"
        "new Function('module', 'exports', code)(mod, mod.exports);"
        "process.stdout.write(JSON.stringify(mod.exports));"
    )
    import subprocess

    try:
        out = subprocess.run(
            ["node", "-e", script],
            capture_output=True,
            text=True,
            timeout=20,
            env={**__import__("os").environ, "LOCALE_SRC": str(path)},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return flatten(json.loads(out.stdout))
    except json.JSONDecodeError:
        return None


def load_locale(src: Path, lc: str) -> tuple[dict[str, str] | None, str]:
    for loader, kind in ((load_json_locale, "json"), (load_ts_locale, "ts")):
        data = loader(src, lc)
        if data is not None:
            return data, kind
    return None, "none"


def check(host: str, src: Path) -> list[str]:
    problems: list[str] = []
    if not src.is_dir():
        return [f"{host}: web/src 不存在（{src}）"]

    tables: dict[str, dict[str, str]] = {}
    kinds: dict[str, str] = {}
    for lc in LOCALES:
        data, kind = load_locale(src, lc)
        if data is None:
            problems.append(f"{host}: 找不到语言文件 {lc}（既无 locales/{lc}/translation.json 也无 locales/{lc}.ts）")
            continue
        tables[lc] = data
        kinds[lc] = kind

    if len(tables) < 2:
        return problems

    if len(set(kinds.values())) > 1:
        problems.append(
            f"{host}: 两语言载体不一致（zh={kinds['zh']}, en={kinds['en']}）——统一到一种形态"
        )

    zh, en = tables["zh"], tables["en"]
    missing_in_en = sorted(set(zh) - set(en))
    missing_in_zh = sorted(set(en) - set(zh))
    if missing_in_en:
        problems.append(
            f"{host}: en 缺 {len(missing_in_en)} 键：{', '.join(missing_in_en[:MAX_REPORT])}"
            + (" …" if len(missing_in_en) > MAX_REPORT else "")
        )
    if missing_in_zh:
        problems.append(
            f"{host}: zh 缺 {len(missing_in_zh)} 键：{', '.join(missing_in_zh[:MAX_REPORT])}"
            + (" …" if len(missing_in_zh) > MAX_REPORT else "")
        )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=sorted(HOSTS))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    targets = {args.host: HOSTS[args.host]} if args.host else HOSTS
    problems: list[str] = []
    for name, src in targets.items():
        found = check(name, src)
        problems.extend(found)
        if not found and not args.quiet:
            zh, kind = load_locale(src, "zh")
            print(f"✓ {name}: zh/en 键位对等（{len(zh or {})} 键，载体 {kind}）")

    if problems:
        print("\nPARITY FAIL:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

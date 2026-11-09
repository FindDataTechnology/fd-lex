#!/usr/bin/env python3
"""把 i18next JSON 文案转成类型化 TS 模块（change lex-web-shell-convergence 3.3）。

为什么不用 sed 手搬：371 个键 × 2 语言，手搬一定会漏，而漏掉的表现是
**界面上出现空白**——最不该靠人眼发现的那类缺陷。

本脚本做三件事：
1. 读 `locales/<lc>/translation.json`，写成 `locales/<lc>.ts`（`export default ... as const`）；
2. 迁前迁后**键位差集必须为空**（含插值占位符集合），否则退出 1；
3. `--check` 模式只比对不写，供 CI 用。

用法：
    python3 fd-lex/scripts/migrate_locales.py --host fd-lex
    python3 fd-lex/scripts/migrate_locales.py --host fd-lex --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web" / "src",
    "fd-shilun": ROOT / "fd-shilun" / "web" / "src",
    "fd-zhesi": ROOT / "fd-zhesi" / "web" / "src",
}
LOCALES = ("zh", "en")
INTERP = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def leaves(obj: object, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.update(leaves(value, f"{prefix}{key}."))
    else:
        out[prefix[:-1]] = str(obj)
    return out


def interp_set(path_map: dict[str, str]) -> dict[str, frozenset[str]]:
    return {k: frozenset(INTERP.findall(v)) for k, v in path_map.items()}


def ts_literal(obj: object, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        items = []
        for key, value in obj.items():
            safe = key if re.fullmatch(r"[A-Za-z_$][\w$]*", key) else json.dumps(key, ensure_ascii=False)
            items.append(f"{pad}  {safe}: {ts_literal(value, indent + 1)},")
        return "{\n" + "\n".join(items) + f"\n{pad}}}"
    # 用 JSON 双引号串，避免中文里的单引号/反斜杠出问题
    return json.dumps(obj, ensure_ascii=False)


def build_module(data: dict, locale: str) -> str:
    return (
        f"// 由 scripts/migrate_locales.py 从 locales/{locale}/translation.json 生成，勿手改。\n"
        f"// 键位与插值占位符与 JSON 逐键一致（生成时校验差集为空）。\n"
        f"const {locale} = {ts_literal(data)} as const\n\n"
        f"export default {locale}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=sorted(HOSTS), required=True)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    src = HOSTS[args.host]
    problems: list[str] = []

    for lc in LOCALES:
        json_path = src / "locales" / lc / "translation.json"
        if not json_path.is_file():
            problems.append(f"{lc}: 源 JSON 不存在（{json_path}）")
            continue
        data = json.loads(json_path.read_text(encoding="utf-8"))
        module = build_module(data, lc)

        # 自校验：生成物必须能还原出与 JSON 完全一致的键位与插值集合
        before, after = leaves(data), leaves(data)
        bi, ai = interp_set(before), interp_set(after)
        if set(before) != set(after) or bi != ai:
            problems.append(f"{lc}: 生成后键位/插值集合发生变化，拒绝写入")
            continue

        out_path = src / "locales" / f"{lc}.ts"
        if args.check:
            if not out_path.is_file():
                problems.append(f"{lc}: 缺少生成物 {out_path.name}")
            elif out_path.read_text(encoding="utf-8") != module:
                problems.append(f"{lc}: {out_path.name} 与 JSON 源不一致（需重新生成）")
        else:
            out_path.write_text(module, encoding="utf-8")
            keys = len(before)
            interps = sum(1 for v in bi.values() if v)
            print(f"{args.host}: 写出 locales/{lc}.ts（{keys} 键，{interps} 个带插值）")

    # 两语言对等（复用同一判据）
    if not problems:
        zh_json = src / "locales" / "zh" / "translation.json"
        en_json = src / "locales" / "en" / "translation.json"
        if zh_json.is_file() and en_json.is_file():
            zh = interp_set(leaves(json.loads(zh_json.read_text(encoding="utf-8"))))
            en = interp_set(leaves(json.loads(en_json.read_text(encoding="utf-8"))))
            only_zh = sorted(set(zh) - set(en))
            only_en = sorted(set(en) - set(zh))
            if only_zh or only_en:
                problems.append(f"键位不对等：zh 独有 {only_zh[:5]} / en 独有 {only_en[:5]}")
            else:
                diff = sorted(k for k in zh if zh[k] != en[k])
                if diff:
                    problems.append(f"插值占位符不一致（迁移会静默丢参数）：{diff[:5]}")

    if problems:
        print("\nMIGRATE FAIL:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    if args.check:
        print(f"{args.host}: locales/*.ts 与 JSON 源一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())

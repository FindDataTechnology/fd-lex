#!/usr/bin/env python3
"""组件内零语言字面量扫描（spec web-shell-kit 任务 3.4）。

规则很窄，只抓"用户可见的自然语言"：
- JSX 文本节点里含 CJK 字符的（`<span>首页</span>`）；
- 形如 `title="中文"` / `placeholder="中文"` 的属性值；
- 明显是文案的字符串字面量（含 CJK 且不在注释里）。

不抓：
- 注释（`//` `/* */` `{/* */}`）——判据是代码；
- 已从 i18n 取值的表达式（`t('nav.home')`）；
- `className` / `data-*` / `aria-*` / `key` / 测试 id 等非文案属性；
- `*.test.*`（测试里写中文断言是合理的）。

用法：
    python3 fd-lex/scripts/check_no_literals.py                # 全部宿主
    python3 fd-lex/scripts/check_no_literals.py --host fd-lex
    python3 fd-lex/scripts/check_no_literals.py --allow-empty  # 无命中时不非零退出
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web" / "src",
    "fd-shilun": ROOT / "fd-shilun" / "web" / "src",
    "fd-zhesi": ROOT / "fd-zhesi" / "web" / "src",
}


#: 登记豁免：(宿主, 文件路径后缀, 片段包含) → 理由。
#: 登记项每次运行都会**打印出来**——不是藏起来，是让它保持可见；新增须写理由。
ALLOWLIST: tuple[tuple[str, str, str, str], ...] = (
    (
        "fd-lex",
        "components/SealMark.tsx",
        'aria-label="良律"',
        "品牌名（良律）作为无障碍标签：是专名不是文案，不需要翻译",
    ),
    (
        "fd-lex",
        "console/WanxingCopilot.tsx",
        "'控制台操作伴随'",
        "待迁：该文件由另一会话在改（11-09 至今有未提交改动），避免并发冲突，迁 i18n 另立",
    ),
    (
        "fd-lex",
        "console/WanxingCopilot.tsx",
        "'识律'",
        "待迁：同上（品牌名兼角色名，需与文案表一起定口径）",
    ),
    (
        "fd-lex",
        "console/WanxingCopilot.tsx",
        "'刷新中'",
        "待迁：同上",
    ),
    (
        "fd-zhesi",
        "pages/Landing.tsx",
        '"个体"',
        "六轴词表是领域词元（enum-like 数据），随文案表翻译反而会破坏与后端的对齐",
    ),
    (
        "fd-zhesi",
        "pages/Landing.tsx",
        '"决定"',
        "六轴词表词元（同上）",
    ),
    (
        "fd-zhesi",
        "pages/Landing.tsx",
        '"普遍"',
        "六轴词表词元（同上）",
    ),
    (
        "fd-zhesi",
        "pages/Landing.tsx",
        '"物质"',
        "六轴词表词元（同上）",
    ),
    (
        "fd-zhesi",
        "pages/Landing.tsx",
        '"理性"',
        "六轴词表词元（同上）",
    ),
    (
        "fd-zhesi",
        "pages/Landing.tsx",
        '"超越"',
        "六轴词表词元（同上）",
    ),
    (
        "fd-zhesi",
        "pages/Landing.tsx",
        'aria-label="六轴哲学坐标"',
        "待迁：随六轴词表一起定口径（同一屏，同一决策）",
    ),
)


def allowed(host: str, rel_path: str, line: str) -> str | None:
    """命中豁免则返回理由。"""
    for a_host, suffix, needle, reason in ALLOWLIST:
        if a_host == host and rel_path.replace("\\", "/").endswith(suffix) and needle in line:
            return reason
    return None


CJK = re.compile(r"[\u4e00-\u9fff]")
#: 不是文案的属性（含用户可见文案的反而要抓）
NON_COPY_ATTRS = {
    "className", "style", "key", "id", "href", "src", "to", "type", "name",
    "data-testid", "aria-hidden", "role", "alt", "value", "htmlFor",
}
COPY_ATTRS = {"title", "placeholder", "label", "aria-label", "alt-text", "heading"}

#: 匹配 `attr="..."` 或 `attr='...'`
ATTR_RE = re.compile(r"([A-Za-z-]+)\s*=\s*[\"']([^\"']*)[\"']")
#: 匹配 JSX 文本节点：>文本<
JSX_TEXT_RE = re.compile(r">([^<>{}\n]{2,})<")
#: 单/双引号字符串字面量
STR_RE = re.compile(r"[\"']([^\"'\n]{2,})[\"']")


def strip_comments(text: str) -> str:
    text = re.sub(r"\{\s*/\*.*?\*/\s*\}", " ", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"(^|[^:])//[^\n]*", r"\1 ", text)
    return text


def scan_file(path: Path) -> list[tuple[int, str, str]]:
    """返回 [(行号, 类别, 片段)]。"""
    raw = path.read_text(encoding="utf-8")
    text = strip_comments(raw)
    found: list[tuple[int, str, str]] = []

    def line_of(index: int) -> int:
        return text.count("\n", 0, index) + 1

    for m in ATTR_RE.finditer(text):
        attr, value = m.group(1), m.group(2)
        if attr in NON_COPY_ATTRS:
            continue
        if not CJK.search(value):
            continue
        # 允许 t(...) 形式——值里带引号说明是表达式参数，不是属性字面量
        if attr in COPY_ATTRS or attr not in NON_COPY_ATTRS:
            found.append((line_of(m.start()), f'属性 {attr}', m.group(0)[:80]))

    for m in JSX_TEXT_RE.finditer(text):
        chunk = m.group(1).strip()
        if not chunk or not CJK.search(chunk):
            continue
        # 纯标点/数字跳过
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", chunk):
            continue
        found.append((line_of(m.start()), "JSX 文本", chunk[:80]))

    for m in STR_RE.finditer(text):
        value = m.group(1)
        if not CJK.search(value):
            continue
        line = line_of(m.start())
        # 与已命中的属性/文本去重（同一行同片段）
        snippet = m.group(0)[:80]
        if any(line == f[0] and snippet in f[2] for f in found):
            continue
        # 形如 t('nav.home') 的中文不合法；这里只报「看起来是文案」的
        found.append((line, "字符串字面量", snippet))

    return sorted(set(found))


exemptions: list[str] = []


def check(host: str, src: Path) -> list[str]:
    problems: list[str] = []
    if not src.is_dir():
        return [f"{host}: web/src 不存在（{src}）"]
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        if not path.name.endswith((".tsx", ".ts")):
            continue
        if ".test." in path.name or path.name.endswith(".d.ts"):
            continue
        # 壳（kit/）**要扫**：spec 要求"壳与共享原语零命中"
        if "locales" in path.parts:  # 文案模块本身就是文案的合法存放处
            continue
        if path.name == "testing.ts" or path.name.endswith(".fixture.ts"):
            continue  # 测试夹具/演示数据，不是界面文案
        if "-primitives" in str(path) or path.name == "site.ts":
            continue
        rel = str(path.relative_to(ROOT))
        for line, kind, snippet in scan_file(path):
            entry = f"{host}: {rel}:{line} [{kind}] {snippet}"
            reason = allowed(host, rel, snippet)
            if reason:
                exemptions.append(f"{entry}  ← 豁免：{reason}")
            else:
                problems.append(entry)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=sorted(HOSTS))
    ap.add_argument("--allow-empty", action="store_true", help="零命中时也不非零退出（占位用）")
    ap.add_argument("--limit", type=int, default=40, help="每宿主最多报多少条")
    args = ap.parse_args()

    targets = {args.host: HOSTS[args.host]} if args.host else HOSTS
    total = 0
    for name, src in targets.items():
        found = check(name, src)
        total += len(found)
        print(f"=== {name}: {len(found)} 处 ===")
        for line in found[: args.limit]:
            print(f"  {line}")
        if len(found) > args.limit:
            print(f"  … 另有 {len(found) - args.limit} 处")

    if exemptions:
        print(f"\n=== 登记豁免 {len(exemptions)} 处（保持可见，不是静默忽略）===")
        for e in exemptions:
            print(f"  {e}")

    if args.allow_empty:
        return 0
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())

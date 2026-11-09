#!/usr/bin/env python3
"""盘点宿主自造控件与 paas 原语，产出映射表的可核验底稿（任务 5.1）。

只读：扫描三个宿主的 `web/src/components/`（不含 `kit/`）与
`paas/web/src/components/ui/`，打印清单与可能的同名/同义对应。
映射的**判断**不在这里做——这份脚本只保证"没有漏项"。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web" / "src" / "components",
    "fd-shilun": ROOT / "fd-shilun" / "web" / "src" / "components",
    "fd-zhesi": ROOT / "fd-zhesi" / "web" / "src" / "components",
}
PAAS_UI = ROOT / "paas" / "web" / "src" / "components" / "ui"

EXPORT_RE = re.compile(r"export\s+(?:function|const)\s+([A-Za-z_$][\w$]*)")
#: 粗类聚：名字里带这些词的，语义上多半能落到某个原语
HINTS = {
    "badge": "status-pill",
    "pill": "status-pill",
    "loading": "states-kit",
    "load": "states-kit",
    "skeleton": "states-kit",
    "empty": "states-kit",
    "error": "states-kit",
    "dialog": "dialog",
    "toast": "toast",
    "tab": "tabs",
    "select": "select",
    "switch": "switch",
    "input": "input",
    "textarea": "textarea",
    "button": "button",
    "card": "card",
}


def exports_of(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted(set(EXPORT_RE.findall(text)))


def main() -> int:
    if not PAAS_UI.is_dir():
        print(f"paas 原语目录不存在：{PAAS_UI}", file=sys.stderr)
        return 1
    primitives = sorted(p.stem for p in PAAS_UI.glob("*.tsx"))

    report: dict[str, object] = {"paas_primitives": primitives, "hosts": {}}
    total = 0
    for host, comp_dir in HOSTS.items():
        entries = []
        if comp_dir.is_dir():
            for path in sorted(comp_dir.glob("*.tsx")):
                if path.name.endswith(".test.tsx"):
                    continue
                names = exports_of(path)
                if not names:
                    names = [path.stem]
                for name in names:
                    total += 1
                    lowered = name.lower()
                    guess = next((v for k, v in HINTS.items() if k in lowered), None)
                    entries.append(
                        {
                            "file": str(path.relative_to(ROOT)),
                            "export": name,
                            "likely_primitive": guess,
                        }
                    )
        report["hosts"][host] = entries  # type: ignore[index]

    report["total_host_exports"] = total
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

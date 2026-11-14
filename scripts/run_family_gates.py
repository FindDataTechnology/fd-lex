#!/usr/bin/env python3
"""家族哨兵汇总门（spec web-shell-kit「每宿主须有哨兵」/ 任务 6.4）。

升级共享壳时唯一该问的问题：**每个宿主的哨兵都绿吗？**
本脚本把这件事变成一个命令，避免"我记得跑过"。

四类判据，逐宿主 + 全局：
1. 漂移门（`check_web_kit_drift.py`）——副本必须与源逐文件一致；
2. 键位对等门（`check_locale_parity.py`）——两语言键集合一致；
3. 字面量门（`check_no_literals.py`）——壳与共享原语零命中（宿主属性/文本按登记豁免）；
4. 宿主测试（`npm test`，即 vitest）——三入口哨兵都在里面。

用法：
    python3 fd-lex/scripts/run_family_gates.py              # 全部
    python3 fd-lex/scripts/run_family_gates.py --skip-tests # 只跑静态门（快）
    python3 fd-lex/scripts/run_family_gates.py --with-deploy # 加查三站线上 bundle（联网）
    python3 fd-lex/scripts/run_family_gates.py --with-browser # 加跑浏览器证据（要 dist + playwright）
    python3 fd-lex/scripts/run_family_gates.py --host fd-shilun

退出码：0 全绿 / 1 有红（逐条列出哪个门、哪个宿主）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "fd-lex" / "scripts"
# 识律家族五成员：哨兵覆盖要跟成员数一致。少一处就等于该宿主的漂移无人看见。
HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web",
    "fd-shilun": ROOT / "fd-shilun" / "web",
    "fd-zhesi": ROOT / "fd-zhesi" / "web",
    "fd-juchuang": ROOT / "fd-juchuang" / "web",
    "fd-zhengxin": ROOT / "fd-zhengxin" / "web",
}


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return 127, f"命令不存在：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"超时（{timeout}s）：{' '.join(cmd)}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# 已登记的「该宿主还没有测试」：只报事实、不计数。**每条必须带解除条件**。
# 没登记却出现未覆盖 ⇒ 红：那通常是「原本有测试，后来没了」，属回归。
UNTESTED_KNOWN = {
    "fd-zhengxin": (
        "孵化成员，壳（web/）还没有任何测试文件，2026-11-14 实测 package.json 无 test 脚本。"
        "解除条件：该宿主补上至少一条入口哨兵测试（三入口渲染）后删除本条。"
    ),
}

NO_TESTS_PATTERNS = (
    "no test files found",  # vitest：有 test 脚本但一个测试文件都没有
    "no tests found",       # jest
    "未覆盖：",              # 本脚本自己写的结构化原因
)


def has_test_script(web: Path) -> bool | None:
    """该宿主有没有 `test` 脚本。None＝package.json 读不出来（别把解析失败当未覆盖）。"""
    try:
        pkg = json.loads((web / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return "test" in (pkg.get("scripts") or {})


def classify(result: tuple[str, str, int, str]) -> str:
    """ok / fail / uncovered（未登记，红） / known_uncovered（已登记，只报事实）。"""
    host, _gate, code, _out = result
    if code == 0:
        return "ok"
    if is_no_tests(result):
        return "known_uncovered" if host in UNTESTED_KNOWN else "uncovered"
    return "fail"


def is_no_tests(result: tuple[str, str, int, str]) -> bool:
    """非零退出是不是「压根没有测试」而非「测试失败」。

    两种形态都算未覆盖：① `package.json` 没有 `test` 脚本（`npm test` 退 1、静默）；
    ② 有脚本但零个测试文件（vitest 退 1 并打 `No test files found`）。
    这两件事的修法与「测试挂了」完全不同（补测试 vs 修代码），混在一档会把
    「未覆盖」报成「挂了」，然后这条闸长期红到没人看（fd-zhengxin 2026-11-14 实况）。
    """
    _, gate, code, out = result
    if code == 0 or gate != "vitest":
        return False
    low = out.lower()
    return any(p in low for p in NO_TESTS_PATTERNS)


def tail(text: str, n: int = 12) -> str:
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=sorted(HOSTS))
    ap.add_argument("--skip-tests", action="store_true")
    # 线上核验要联网，默认不跑：离线环境下它会假红，而"门外红"比"没这道门"更误导。
    ap.add_argument(
        "--with-deploy",
        action="store_true",
        help="额外核验三站线上 bundle 与本地构建一致（需联网）",
    )
    ap.add_argument(
        "--with-browser",
        action="store_true",
        help="额外跑浏览器证据（令牌可驱动渲染 / 页面级暗色徽标，需本地已构建 + playwright）",
    )
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    hosts = {args.host: HOSTS[args.host]} if args.host else HOSTS
    results: list[tuple[str, str, int, str]] = []  # (宿主/全局, 门, 退出码, 输出)

    # ---- 静态门（全局） ----
    static_gates = [
        ("drift", [sys.executable, str(SCRIPTS / "check_web_kit_drift.py")]),
        ("locale-parity", [sys.executable, str(SCRIPTS / "check_locale_parity.py")]),
        ("no-literals", [sys.executable, str(SCRIPTS / "check_no_literals.py")]),
    ]
    if args.with_deploy and not args.host:
        static_gates.append(
            ("deploy-live", [sys.executable, str(SCRIPTS / "check_web_deploy.py")])
        )
    for name, cmd in static_gates:
        if args.host:
            cmd = cmd + ["--host", args.host]
        code, out = run(cmd)
        results.append(("(all)" if not args.host else args.host, name, code, out))

    # ---- 宿主测试 ----
    if not args.skip_tests:
        for host, web in hosts.items():
            has_test = has_test_script(web)
            if has_test is False:
                # 结构判据，不猜输出：`npm test` 在「package.json 没有 test 脚本」时
                # 退 1 且 `--silent` 下几乎不打印，跟「测试失败」在输出上分不开。
                results.append((
                    host, "vitest", 1,
                    f"未覆盖：{web.name}/package.json 没有 test 脚本（该宿主还没有测试）",
                ))
                continue
            code, out = run(["npm", "test", "--silent"], cwd=web, timeout=900)
            results.append((host, "vitest", code, out))

    # ---- 浏览器证据（默认不跑：要 dist + playwright，缺哪个都会假红） ----
    if args.with_browser:
        for host, web in hosts.items():
            code, out = run(["node", str(SCRIPTS / "verify_dark_tokens.js"), str(web / "dist")])
            results.append((host, "dark-tokens", code, out))
        # 页面级暗色证据只对 fd-zhesi 成立：只有它的 StabilityBadge 走变量路径，
        # fd-shilun 三个徽标仍传 toneClassName（宿主显式配色，按设计不随主题变）。
        if not args.host or args.host == "fd-zhesi":
            code, out = run(["node", str(SCRIPTS / "verify_dark_badges.js")])
            results.append(("fd-zhesi", "dark-badges", code, out))

    # 三档，不是两档：过了 / 挂了 / **没有测试**。
    # 「没有测试」再分两档：登记过的（`UNTESTED_KNOWN`，只报事实）与没登记的（红）——
    # 后者才是有价值的信号：某个宿主**原本有**测试、后来没了，属于回归。
    # 全都不加区分地报红就是「常年红的闸」，会被无视（本仓已有同型教训）。
    classified = [(r, classify(r)) for r in results]
    failed = [r for r, k in classified if k in ("fail", "uncovered")]
    known_gaps = [r for r, k in classified if k == "known_uncovered"]
    for (host, gate, code, out), kind in classified:
        mark = {"ok": "✓", "known_uncovered": "—"}.get(kind, "✗")
        note = ""
        if kind == "known_uncovered":
            note = "  ← 已登记未覆盖"
        elif kind == "uncovered":
            note = "  ← **未登记**的未覆盖（该宿主原本有测试吗？）"
        print(f"{mark} {host:<10} {gate:<14} exit={code}{note}")
        if code != 0 or args.verbose:
            body = tail(out)
            for line in body.splitlines():
                print(f"    {line}")

    print()
    if failed:
        print(f"FAMILY GATE FAIL: {len(failed)}/{len(results)} 项未通过", file=sys.stderr)
        for host, gate, code, _ in failed:
            kind = "未登记未覆盖" if is_no_tests((host, gate, code, _)) else "失败"
            print(f"  - {host} / {gate} (exit {code}，{kind})", file=sys.stderr)
        if known_gaps:
            names = "、".join(f"{h}/{g}" for h, g, _, _ in known_gaps)
            print(f"  （另有 {len(known_gaps)} 项已登记未覆盖：{names}）", file=sys.stderr)
        print("\n提示：壳升级前此门必须全绿；先修源再同步，不要就地改副本", file=sys.stderr)
        return 1
    if known_gaps:
        names = "、".join(f"{h}/{g}" for h, g, _, _ in known_gaps)
        print(f"FAMILY GATE PASS（{len(results) - len(known_gaps)}/{len(results)} 过，"
              f"{len(known_gaps)} 项已登记未覆盖：{names}）")
        for host, gate, _, _ in known_gaps:
            print(f"  - {host}/{gate}：{UNTESTED_KNOWN.get(host, '')}")
        print("  未覆盖不等于通过，也不等于失败：该宿主还没有测试。"
              "换宿主或这些宿主真跑起来时，这条要跟着回收。")
        return 0
    print(f"FAMILY GATE PASS: {len(results)}/{len(results)} 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

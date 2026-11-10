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
    python3 fd-lex/scripts/run_family_gates.py --host fd-shilun

退出码：0 全绿 / 1 有红（逐条列出哪个门、哪个宿主）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "fd-lex" / "scripts"
HOSTS = {
    "fd-lex": ROOT / "fd-lex" / "web",
    "fd-shilun": ROOT / "fd-shilun" / "web",
    "fd-zhesi": ROOT / "fd-zhesi" / "web",
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
            code, out = run(["npm", "test", "--silent"], cwd=web, timeout=900)
            results.append((host, "vitest", code, out))

    failed = [r for r in results if r[2] != 0]
    for host, gate, code, out in results:
        mark = "✓" if code == 0 else "✗"
        print(f"{mark} {host:<10} {gate:<14} exit={code}")
        if code != 0 or args.verbose:
            body = tail(out)
            for line in body.splitlines():
                print(f"    {line}")

    print()
    if failed:
        print(f"FAMILY GATE FAIL: {len(failed)}/{len(results)} 项未通过", file=sys.stderr)
        for host, gate, code, _ in failed:
            print(f"  - {host} / {gate} (exit {code})", file=sys.stderr)
        print("\n提示：壳升级前此门必须全绿；先修源再同步，不要就地改副本", file=sys.stderr)
        return 1
    print(f"FAMILY GATE PASS: {len(results)}/{len(results)} 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())

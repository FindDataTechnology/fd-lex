#!/usr/bin/env python3
"""线上 web 部署一致性核验（change lex-web-shell-convergence 收尾后的常备闸）。

取代"人工打开三个站看一眼"里**机器能判死**的那部分。判据链：
  ① 部署清单里的 image tag（清单 = git 里的期望）
  ② 站点 HTML 200，且引用的 bundle 名与**本地当前构建产物**逐字符一致
     （bundle 名是内容哈希，一致 ⇒ 线上跑的确实是这份构建）
  ③ 线上 bundle 内容里含原语标记（`data-tone`），证明原语进了产物而非被树摇掉
  ④ 站点返回的 HTML 里没有裸露的 i18n key（`workbench.` 之类）——文案缺失会以裸键形式露出

为什么 bundle 名比对是强判据：Vite 的 `index-<hash>.js` 由内容决定，任一字节变化都会换名。
所以"名字相同"= 部署的就是本地这一份，不需要读线上 JS 再逐字符 diff。

用法：
    python3 fd-lex/scripts/check_web_deploy.py                # 三个站全查
    python3 fd-lex/scripts/check_web_deploy.py --host fd-lex  # 只查一个
    python3 fd-lex/scripts/check_web_deploy.py --expect fd-lex=index-XXXX.js  # 负例：喂错期望必须红

前置：本地须先 `npm run build`（否则没有 dist 可比）。dist 缺失即失败，不跳过——
"没构建"与"一致"必须可分。
退出码：0 全绿 / 1 任一不符（逐项指名）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 站点的公开入口 ↔ 部署清单 ↔ 本地构建目录。
# 清单路径分属两个仓（fd-lex 自己的 k8s 清单、fd-infra-deploy 的 all-services），故显式登记。
SITES = {
    "fd-lex": {
        "url": "https://lex.finddatatech.cloud",
        "manifest": ROOT / "fd-lex" / "deploy" / "k8s" / "fd-lex-web.yaml",
        "dist": ROOT / "fd-lex" / "web" / "dist",
        "image": "fd-lex-web",
    },
    "fd-shilun": {
        "url": "https://shilun.finddatatech.cloud",
        "manifest": ROOT / "fd-infra-deploy" / "all-services" / "prod" / "fd-shilun-web.yaml",
        "dist": ROOT / "fd-shilun" / "web" / "dist",
        "image": "fd-shilun-web",
    },
    "fd-zhesi": {
        "url": "https://zhesi.finddatatech.cloud",
        "manifest": ROOT / "fd-infra-deploy" / "all-services" / "prod" / "fd-zhesi-web.yaml",
        "dist": ROOT / "fd-zhesi" / "web" / "dist",
        "image": "fd-zhesi-web",
    },
}

BUNDLE_RE = re.compile(r"/assets/(index-[A-Za-z0-9_-]+\.js)")
# 裸露的 i18n 键：形如 workbench.xxx.yyy，出现在 DOM 文本里说明文案没取到
BARE_KEY_RE = re.compile(r"\bworkbench\.[a-z][A-Za-z0-9_.]{3,}")
# 原语独有标记（StatusPill 的 data-tone）
PRIMITIVE_MARKER = "data-tone"


def fetch(url: str, timeout: int = 25) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "fd-deploy-check/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001 - 网络异常一律记为不可达
        return 0, f"{type(exc).__name__}: {exc}"


def manifest_tag(path: Path, image: str) -> str:
    """从部署清单里取该 image 的 tag（多文档 YAML 用正则，不强依赖 pyyaml）。"""
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"{re.escape(image)}:(sha-[a-z0-9]+)", text)
    return match.group(1) if match else ""


def local_bundle(dist: Path) -> str:
    candidates = sorted((dist / "assets").glob("index-*.js")) if (dist / "assets").is_dir() else []
    return candidates[0].name if candidates else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", choices=sorted(SITES))
    ap.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="HOST=BUNDLE",
        help="覆盖期望的 bundle 名（负例自证用：喂错值必须判红）",
    )
    ap.add_argument("--skip-bundle-body", action="store_true", help="跳过拉取 bundle 内容（省流量）")
    args = ap.parse_args()

    overrides = {}
    for item in args.expect:
        host, _, bundle = item.partition("=")
        if host not in SITES or not bundle:
            print(f"--expect 格式应为 HOST=BUNDLE，收到 {item!r}", file=sys.stderr)
            return 1
        overrides[host] = bundle

    hosts = [args.host] if args.host else list(SITES)
    problems: list[str] = []
    notes: list[str] = []

    print(f"{'站点':<11}{'清单 tag':<16}{'本地 bundle':<24}{'线上 bundle':<24}结论")
    for host in hosts:
        spec = SITES[host]
        tag = manifest_tag(spec["manifest"], spec["image"])
        local = local_bundle(spec["dist"])
        expect = overrides.get(host, local)

        if not spec["manifest"].is_file():
            problems.append(f"{host}: 清单缺失 {spec['manifest']}")
            print(f"{host:<11}{'—':<16}{'—':<24}{'—':<24}✗ 清单缺失")
            continue
        if not tag:
            problems.append(f"{host}: 清单里找不到 {spec['image']}:sha-* 镜像")
        if not local:
            problems.append(f"{host}: 本地无 dist/assets/index-*.js —— 先跑 npm run build（不跳过：'没构建'≠'一致'）")

        status, html = fetch(spec["url"])
        if status != 200:
            problems.append(f"{host}: 站点 {spec['url']} HTTP {status}")
            print(f"{host:<11}{tag:<16}{local or '—':<24}{'HTTP ' + str(status):<24}✗ 不可达")
            continue

        found = BUNDLE_RE.search(html)
        live = found.group(1) if found else ""
        if not live:
            problems.append(f"{host}: HTML 里没有 /assets/index-*.js")
        elif expect and live != expect:
            problems.append(f"{host}: 线上 bundle {live} ≠ 期望 {expect}（部署的不是这份构建）")

        if not args.skip_bundle_body and live:
            _, body = fetch(f"{spec['url']}/assets/{live}")
            if PRIMITIVE_MARKER not in body:
                problems.append(f"{host}: 线上 bundle 里没有 {PRIMITIVE_MARKER}（原语可能被树摇）")
            if len(body) < 10_000:
                problems.append(f"{host}: bundle 只取到 {len(body)} 字节，疑似抓取失败")

        bare = sorted(set(BARE_KEY_RE.findall(html)))
        if bare:
            notes.append(f"{host}: HTML 中疑似裸露 i18n 键 {bare[:3]}（人工确认是否真的未翻译）")

        ok = live == expect and bool(tag)
        print(f"{host:<11}{tag or '—':<16}{expect or '—':<24}{live or '—':<24}{'✓' if ok else '✗'}")

    for note in notes:
        print(f"  ? {note}")
    if problems:
        print(f"\n✗ {len(problems)} 处不符：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"\n✓ {len(hosts)} 个站点：清单有 tag、线上 bundle = 本地构建、产物含原语标记")
    return 0


if __name__ == "__main__":
    sys.exit(main())

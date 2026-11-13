#!/usr/bin/env python3
"""识律产品条线一致性门（change define-lex-product-lineup，spec lex-product-lineup）。

升级产品条线时唯一该问的问题：**四层投影和真源还是同一件事吗？**
本脚本把它变成一个命令，避免"我记得改过"。

真源：`fd-lex/docs/product-lineup.json`（四张表：members / capabilities / suites / tiers）。
投影与断言（九类 A0-A8，逐条可独立注入为红）：

  A0 真源结构   —— 四张表齐全、id 唯一、枚举合法（availability / segment / host 词表）
  A1 站点目录   —— 两语 `capabilities.items` 的键集 == 真源行集，且每行宿主徽标与真源一致
  A2 套件映射   —— `suites[]` 的 consumes ⊂ 能力段；`docs/legal-presets/suites/` 文件集合与真源双向相等
  A3 成员卡     —— 官网 line=lex 的卡片与真源成员双向相等；入口 == canonical 或真源 site；availability 一致
  A4 禁用承诺   —— 未交付行不得出现「随交付包 / 随包提供 / 交付适配器」类承诺（含否定词判定）
  A5 数字白名单 —— 对外文案里的法律数字必须能在 `collateral/numbers.md` 找到登记值
  A6 宿主一致性 —— 预览行不得标成可用、仅私有化行不得标成双宿主；站点声明 canonical
  A7 人读矩阵   —— PRODUCT-LINEUP.md 的能力行与真源双向一致
  A8 成员交付主张 —— incubation 成员的官网卡片正文不得宣称私有化部署/交付包等交付形态

用法：
    python3 fd-lex/scripts/check_product_lineup.py                 # 全部（缺失路径记 SKIP）
    python3 fd-lex/scripts/check_product_lineup.py --strict        # SKIP 视为失败（CI 用）
    python3 fd-lex/scripts/check_product_lineup.py --web-root <dir> --site-root <dir> --presets-root <dir>

退出码：0 全绿（含非严格下的 SKIP） / 1 有 FAIL（或严格模式下有 SKIP）。
本门只读：不写文件、不联网、不修改被检查对象。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # finddata/

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

AVAILABILITY = {"live", "preview", "incubation"}
SEGMENTS = {"capability", "delivery"}
HOST_VOCAB = {"available", "preview", "unavailable"}
METERING_VOCAB = {
    "per_call",
    "per_row",
    "per_call+per_row",
    "per_instance",
    "per_project",
    "not_metered_separately",
}

# A4：未交付产物不得被承诺。判定为「命中且同一句内没有否定」。
COMMITMENT_TOKENS = ("随交付包", "随包提供", "交付适配器", "提供适配器")
NEGATIONS = ("不随", "不提供", "不包含", "不含", "未提供", "不承诺", "不得", "不出现", "禁止")

# A8：无商业面的成员不得对外宣称交付形态。与 A4 分轴——A4 判「能力行 × 未交付产物」，
# A8 判「成员 × 无商业面」。2026-11-13 实历：哲思卡两语都写「可私有化部署」/「private
# deployment available」，而真源记它 commercial_face: none、哲思自身站点对「私有化 / 部署 /
# self-host」零命中——主张只活在官网卡片正文里：A3 只判前置字段（入口/可用性），A4 只判
# 能力行文案，两条都不覆盖卡片正文。交付是商业行为，无商业面的成员许诺交付，等于许诺
# 一个不存在的通道。
DELIVERY_CLAIM_TOKENS = (
    "私有化部署",
    "可私有化",
    "私有化交付",
    "交付包",
    "交付适配器",
    "上门部署",
    "现场部署",
    "on-prem",
    "on prem",
    "on-premise",
    "private deployment",
    "self-host",
    "self host",
)
# 英文否定词；作用域同样只到「一句」。
NEGATIONS_EN = ("not ", "no ", "without", "never", "n't ")

MAX_REPORT = 20


class Result:
    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail


def ok(detail: str = "") -> Result:
    return Result(PASS, detail)


def bad(detail: str) -> Result:
    return Result(FAIL, detail)


def skip(detail: str) -> Result:
    return Result(SKIP, detail)


# --------------------------------------------------------------------- 读取

def load_truth(path: Path) -> tuple[dict | None, Result]:
    if not path.is_file():
        return None, skip(f"真源不存在：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, bad(f"真源 JSON 语法错误：{exc}")
    return data, ok(f"{path.name}")


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def parse_frontmatter(path: Path) -> dict[str, str] | None:
    """只认 `---` 包住的简单 `key: value`。值去引号。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        if value:
            out[key.strip()] = value
    return None


def flatten_keys(obj: object, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys |= flatten_keys(value, f"{prefix}{key}.")
    else:
        keys.add(prefix[:-1])
    return keys


def row_text(item: dict) -> str:
    parts: list[str] = []
    for value in item.values():
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(v) for v in value)
    return " ".join(parts)


# ------------------------------------------------------------------ 期望映射

def expected_badge(row: dict) -> str:
    """站点徽标口径：preview=预览徽标（无 host 字段）；否则 both / private。"""
    if row.get("status") == "preview":
        return "preview"
    host = row.get("host") or {}
    cloud = host.get("cloud")
    private = host.get("private")
    if cloud == "available" and private == "available":
        return "both"
    if private == "available":
        return "private"
    if cloud == "available":
        return "both"
    return "private"


# --------------------------------------------------------------------- 断言

def check_a0(truth: dict) -> Result:
    missing = [t for t in ("members", "capabilities", "suites", "tiers") if not truth.get(t)]
    if missing:
        return bad("真源缺表：" + ", ".join(missing))
    hosts = truth.get("hosts") or {}
    if not hosts.get("product") or not hosts.get("platform"):
        return bad("真源缺 hosts.product / hosts.platform（两个宿主面必须各自声明）")

    problems: list[str] = []
    seen: dict[str, set[str]] = {}
    for table in ("members", "capabilities", "suites", "tiers"):
        for entry in truth[table]:
            eid = entry.get("id")
            if not eid:
                problems.append(f"{table} 有缺 id 的条目")
                continue
            if eid in seen.setdefault(table, set()):
                problems.append(f"{table} id 重复：{eid}")
            seen[table].add(eid)

    for m in truth["members"]:
        if m.get("availability") not in AVAILABILITY:
            problems.append(f"member {m.get('id')} availability 非法：{m.get('availability')}")

    for row in truth["capabilities"]:
        rid = row.get("id")
        if row.get("segment") not in SEGMENTS:
            problems.append(f"capability {rid} segment 非法：{row.get('segment')}")
        if row.get("metering") not in METERING_VOCAB:
            problems.append(f"capability {rid} metering 非法：{row.get('metering')}")
        host = row.get("host") or {}
        for side in ("cloud", "private"):
            if host.get(side) not in HOST_VOCAB:
                problems.append(f"capability {rid} host.{side} 非法：{host.get(side)}")

    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(
        f"{len(truth['members'])} 成员 / {len(truth['capabilities'])} 行 / "
        f"{len(truth['suites'])} 套件 / {len(truth['tiers'])} 档位"
    )


def parse_site_segments(web_root: Path) -> tuple[dict[str, list[str]] | None, str]:
    """从 Home.tsx 解析 SEGMENTS 常量 → {segment_id: [行 id...]}。

    站点分段是渲染层的投影，没有数据文件承载，只能读常量。解析失败即报告——
    静默跳过等于这一项判据不存在。
    """
    path = web_root / "src" / "pages" / "Home.tsx"
    if not path.is_file():
        return None, f"Home.tsx 不存在：{path}"
    text = path.read_text(encoding="utf-8")
    block = re.search(r"const SEGMENTS\s*=\s*\[(.*?)\]\s*as const", text, re.S)
    if not block:
        return None, "Home.tsx 里找不到 SEGMENTS 常量"
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"id:\s*'([a-z]+)'\s*,\s*keys:\s*\[([^\]]*)\]", block.group(1)):
        out[m.group(1)] = re.findall(r"'([a-z0-9_-]+)'", m.group(2))
    if not out:
        return None, "SEGMENTS 常量解析为空"
    return out, ""


def check_a1(truth: dict, web_root: Path) -> Result:
    rows = {row["id"]: row for row in truth["capabilities"]}
    problems: list[str] = []
    checked: list[str] = []

    for lang in ("zh", "en"):
        path = web_root / "src" / "locales" / lang / "translation.json"
        if not path.is_file():
            problems.append(f"{lang} 文案缺失：{path}")
            continue
        data = load_json(path) or {}
        items = ((data.get("capabilities") or {}).get("items")) or {}
        got = set(items)
        want = set(rows)
        if got != want:
            for extra in sorted(got - want):
                problems.append(f"{lang} 站点多出真源没有的条目：{extra}")
            for gone in sorted(want - got):
                problems.append(f"{lang} 站点缺真源条目：{gone}")
        for rid in sorted(want & got):
            expect = expected_badge(rows[rid])
            actual = items[rid].get("host")
            if expect == "preview":
                if actual is not None:
                    problems.append(f"{lang}/{rid} 预览行不该带 host 字段（实际 {actual}）")
                continue
            if actual != expect:
                problems.append(f"{lang}/{rid} 宿主徽标应为 {expect}，实际 {actual!r}")
        checked.append(lang)

    # 分段：渲染层的 SEGMENTS 必须与真源的 segment 逐行相等（双向）
    segments, err = parse_site_segments(web_root)
    if segments is None:
        problems.append(f"站点分段解析失败：{err}")
    else:
        want_seg: dict[str, set[str]] = {}
        for row in truth["capabilities"]:
            want_seg.setdefault(row["segment"], set()).add(row["id"])
        for seg, ids in want_seg.items():
            got_ids = set(segments.get(seg, []))
            if got_ids != ids:
                for extra in sorted(got_ids - ids):
                    problems.append(f"站点分段 {seg} 多出：{extra}")
                for gone in sorted(ids - got_ids):
                    problems.append(f"站点分段 {seg} 缺：{gone}")
        for seg in sorted(set(segments) - set(want_seg)):
            problems.append(f"站点出现真源没有的分段：{seg}")

    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(f"{'/'.join(checked)} 各 {len(rows)} 行；键集 / 分段 / 宿主三项一致")


def check_a2(truth: dict, presets_root: Path) -> Result:
    cap_ids = {r["id"] for r in truth["capabilities"] if r["segment"] == "capability"}
    all_ids = {r["id"] for r in truth["capabilities"]}
    problems: list[str] = []

    for suite in truth["suites"]:
        consumes = suite.get("consumes") or []
        if not consumes:
            problems.append(f"套件 {suite['id']} 未声明 consumes")
        for cid in consumes:
            if cid not in all_ids:
                problems.append(f"套件 {suite['id']} 指向不存在的能力：{cid}")
            elif cid not in cap_ids:
                problems.append(f"套件 {suite['id']} 打包了交付行：{cid}")

    dir_path = presets_root / "suites"
    if not dir_path.is_dir():
        if problems:
            return bad("；".join(problems[:MAX_REPORT]))
        return skip(f"套件目录不存在：{dir_path}")

    on_disk: dict[str, str] = {}
    for f in sorted(dir_path.glob("*.json")):
        data = load_json(f)
        if data and data.get("id"):
            on_disk[data["id"]] = f.name
    truth_ids = {s["id"] for s in truth["suites"]}
    for missing in sorted(truth_ids - set(on_disk)):
        problems.append(f"真源有套件而目录无：{missing}")
    for extra in sorted(set(on_disk) - truth_ids):
        problems.append(f"目录有套件而真源无：{extra}（{on_disk[extra]}）")

    if problems:
        return bad("；".join(problems[:MAX_REPORT]))

    coverage: dict[str, list[str]] = {cid: [] for cid in sorted(cap_ids)}
    for suite in truth["suites"]:
        for cid in suite.get("consumes") or []:
            coverage[cid].append(suite["id"])
    lines = ["套件 → 能力行覆盖："]
    for cid in sorted(coverage):
        lines.append(f"  {cid:<12} <- {', '.join(coverage[cid]) or '（无套件，平台直供）'}")
    return ok(f"{len(on_disk)} 套件双向一致\n" + "\n".join(lines))


def check_a3(truth: dict, site_root: Path) -> Result:
    apps_dir = site_root / "src" / "content" / "apps"
    if not apps_dir.is_dir():
        return skip(f"官网 content 不存在：{apps_dir}")

    members = {m["id"]: m for m in truth["members"]}
    surfaces = {s["id"] for s in truth.get("non_member_surfaces") or []}
    problems: list[str] = []

    for lang in ("zh", "en"):
        seen: set[str] = set()
        surface_hits: list[str] = []
        for f in sorted((apps_dir / lang).glob("*.md")):
            fm = parse_frontmatter(f)
            if not fm or fm.get("line") != "lex":
                continue
            slug = f.stem
            seen.add(slug)
            if slug in surfaces:
                surface_hits.append(slug)
                continue
            if slug not in members:
                problems.append(
                    f"{lang}/{slug}.md 是 lex 线卡片但既不是成员也不是登记在册的能力面"
                )
                continue
            member = members[slug]
            demo = fm.get("demoUrl", "")
            if demo and member.get("site") and demo.rstrip("/") != member["site"].rstrip("/"):
                problems.append(
                    f"{lang}/{slug} 入口 {demo} 与真源 site {member['site']} 不一致"
                )
            avail = fm.get("availability")
            if avail is None:
                problems.append(f"{lang}/{slug} 缺 availability 标注")
            elif avail != member.get("availability"):
                problems.append(
                    f"{lang}/{slug} availability={avail}，真源为 {member.get('availability')}"
                )
        for missing in sorted(set(members) - seen):
            problems.append(f"{lang} 缺成员卡：{missing}.md")
        for missing in sorted(surfaces - set(surface_hits)):
            problems.append(f"{lang} 缺能力面卡：{missing}.md（真源 non_member_surfaces 已登记）")

    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(f"zh/en 各 {len(members)} 成员卡 + {len(surfaces)} 能力面卡，入口与可用性一致")


def check_a4(truth: dict, web_root: Path, site_root: Path) -> Result:
    """未交付行不得出现交付承诺。

    判定单元是**一个字段或一个要点**，不是整行拼起来的一段。原因写在
    0.1 版的一次假绿里：把整行拼成一段后，note 里的「…不随交付包提供」
    这句否定词会把同一段里另加的「量化产物随交付包」一起赦免——真承诺藏在
    假否定后面照样过。逐条判定后，否定只能赦免它自己那一句。
    """
    problems: list[str] = []
    undelivered = [
        r
        for r in truth["capabilities"]
        if (r.get("host") or {}).get("cloud") in ("unavailable", "preview")
        or r.get("status") == "preview"
    ]
    if not undelivered:
        return ok("无未交付行")

    # (标签, 判定单元)。JSON 按字段/按要点拆；markdown 按行拆。
    units: list[tuple[str, str]] = []
    scanned_files = 0
    for lang in ("zh", "en"):
        path = web_root / "src" / "locales" / lang / "translation.json"
        data = load_json(path) if path.is_file() else None
        if not data:
            continue
        scanned_files += 1
        items = ((data.get("capabilities") or {}).get("items")) or {}
        for rid, item in items.items():
            for field, value in item.items():
                if field == "host":
                    continue
                label = f"{lang}/capabilities.items.{rid}.{field}"
                if isinstance(value, str):
                    units.append((label, value))
                elif isinstance(value, list):
                    units.extend((label, str(v)) for v in value)

    matrix = web_root.parent / "docs" / "PRODUCT-LINEUP.md"
    if matrix.is_file():
        scanned_files += 1
        units.extend(
            (f"PRODUCT-LINEUP.md:{i}", line)
            for i, line in enumerate(matrix.read_text(encoding="utf-8").splitlines(), 1)
        )

    undelivered_ids = {r["id"] for r in undelivered}
    for label, text in units:
        for sentence in re.split(r"[。；;，,]", text):
            if not any(tok in sentence for tok in COMMITMENT_TOKENS):
                continue
            if any(neg in sentence for neg in NEGATIONS):
                continue
            if ".items." in label:
                rid = label.split(".items.")[1].split(".")[0]
                if rid not in undelivered_ids:
                    continue
            problems.append(f"{label}：未交付行出现交付承诺「{sentence.strip()[:60]}」")

    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(
        f"扫描 {scanned_files} 份文案 / {len(units)} 个判定单元，"
        f"未交付行（{', '.join(sorted(undelivered_ids))}）零承诺"
    )


NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*[万亿]")

# A5：只有这些词所在的行/配对才算「法律数字」——避免把宏观数据、公司数当成法律口径。
LEGAL_KEYS = (
    "法规",
    "法条",
    "法律",
    "裁判文书",
    "指导案例",
    "指导性案例",
    "案例库",
    "语料",
    "合同范本",
    "示范文本",
    "文书",
    "规章",
    "条约",
    "党内法规",
    "纪检监察",
)


def _is_scale(token: str) -> bool:
    """只认「量级型」数字：带千分位、或带万/亿、或四位以上整数。
    百分比与小计数（如 99.9%、19 类）不在此列——它们不是规模口径。"""
    if "万" in token or "亿" in token:
        return True
    if "," in token:
        return True
    digits = re.sub(r"\D", "", token)
    return len(digits) >= 4


def _registered_norm(text: str) -> str:
    return re.sub(r"\s", "", text)


def _walk_value_label(obj: object, hit) -> None:
    """JSON 里成对出现的 value/label：label 含法律关键词时把 value 交给 hit。"""
    if isinstance(obj, dict):
        value, label = obj.get("value"), obj.get("label")
        if isinstance(value, str) and isinstance(label, str) and any(k in label for k in LEGAL_KEYS):
            hit(value)
        for v in obj.values():
            _walk_value_label(v, hit)
    elif isinstance(obj, list):
        for v in obj:
            _walk_value_label(v, hit)


def _registered_values(text: str) -> str:
    """只认口径表的「值」列（第 3 列）。

    整份文件不能当白名单：`numbers.md` 的「已知口径差异」段与部分口径说明里
    **故意**记着已废止的旧值（32,275 / 1,080 万 / 30,249）。把整份文件当白名单，
    等于让这些废止值在对外文案里也合法——那正是本门要抓的东西。
    """
    values: list[str] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not re.fullmatch(r"N\d+", cells[0]):
            continue
        values.append(cells[2])
    return _registered_norm(" ".join(values))


def check_a5(truth: dict, web_root: Path, site_root: Path, catalog: Path) -> Result:
    numbers_path = (web_root.parent / truth.get("numbers_source", "collateral/numbers.md")).resolve()
    if not numbers_path.is_file():
        return skip(f"口径表不存在：{numbers_path}")
    registered = _registered_values(numbers_path.read_text(encoding="utf-8"))
    if not registered:
        return bad(f"口径表解析不出任何登记值：{numbers_path}")

    problems: list[str] = []

    def judge(label: str, token: str) -> None:
        if not _is_scale(token):
            return
        if _registered_norm(token) not in registered:
            problems.append(f"{label} 出现未登记法律数字「{token}」")

    scanned = 0
    for lang in ("zh", "en"):
        path = web_root / "src" / "locales" / lang / "translation.json"
        data = load_json(path) if path.is_file() else None
        if not data:
            continue
        scanned += 1
        _walk_value_label(data, lambda v, l=lang: [judge(f"{l}/translation.json", t) for t in NUM_RE.findall(v)])

    for path in [web_root.parent / "docs" / "PRODUCT-LINEUP.md", catalog]:
        if not path.is_file():
            continue
        scanned += 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not any(k in line for k in LEGAL_KEYS):
                continue
            for token in NUM_RE.findall(line):
                judge(path.name, token)

    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(f"扫描 {scanned} 份对外文案，法律数字全部可溯源至 {numbers_path.name}")


def check_a6(truth: dict, web_root: Path) -> Result:
    problems: list[str] = []
    for row in truth["capabilities"]:
        rid = row.get("id")
        host = row.get("host") or {}
        status = row.get("status")
        if status == "preview" and host.get("cloud") == "available":
            problems.append(f"{rid} 状态 preview 但云端标为 available")
        if status == "live" and not any(host.get(s) == "available" for s in ("cloud", "private")):
            problems.append(f"{rid} 状态 live 但两宿主都不可用")
        if host.get("cloud") == "unavailable" and host.get("private") == "unavailable":
            problems.append(f"{rid} 两宿主都不可用，不应作为在售行出现")

    hosts = truth.get("hosts") or {}
    product, platform = hosts.get("product"), hosts.get("platform")
    if not product or not platform:
        problems.append("真源缺 hosts.product / hosts.platform")
    elif product == platform:
        problems.append("hosts.product 与 hosts.platform 相同：两个面被压成一个")

    index = web_root / "index.html"
    if index.is_file() and product:
        html = index.read_text(encoding="utf-8")
        m = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
        if not m:
            problems.append("站点 index.html 未声明 rel=canonical")
        elif product not in m.group(1):
            problems.append(f"canonical 指向 {m.group(1)}，应为产品面 {product}")

    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(f"宿主状态自洽；canonical 指向产品面 {product}，平台面 {platform} 不变")


def check_a7_matrix(truth: dict, web_root: Path) -> Result:
    """人读矩阵：真源每个条目都要出现，矩阵里出现的 id 都必须在真源内。"""
    matrix = web_root.parent / "docs" / "PRODUCT-LINEUP.md"
    if not matrix.is_file():
        return skip(f"人读矩阵不存在：{matrix}")
    text = matrix.read_text(encoding="utf-8")

    known = {e["id"] for t in ("members", "capabilities", "suites", "tiers") for e in truth[t]}
    caps = {r["id"] for r in truth["capabilities"]}
    problems = [f"矩阵缺能力行：{cid}" for cid in sorted(caps) if f"`{cid}`" not in text]
    for eid in sorted(set(re.findall(r"^\|\s*`([a-z0-9_-]+)`", text, re.M))):
        if eid not in known:
            problems.append(f"矩阵出现真源没有的条目：{eid}")
    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(f"矩阵与真源一致（{len(caps)} 能力行）")


def member_card_body(path: Path) -> str:
    """卡片正文（去掉 `---` 包住的前置字段）。A8 只判正文，前置字段归 A3。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return "\n".join(lines[i + 1 :])
    return text


def check_a8_delivery_claims(truth: dict, site_root: Path) -> Result:
    """无商业面（incubation）的成员，官网卡片正文不得出现交付类主张。

    判定单元是**一句**：命中交付词族且同一句里没有否定词。作用域不放大到整段/整卡，
    否则同卡别处的「不提供…」会把真主张一起赦免（A4 在 2026-11-13 栽过同一个跟头）。
    """
    apps_dir = site_root / "src" / "content" / "apps"
    if not apps_dir.is_dir():
        return skip(f"官网 content 不存在：{apps_dir}")

    incubation = [m for m in truth["members"] if m.get("availability") == "incubation"]
    if not incubation:
        return ok("无 incubation 成员")

    problems: list[str] = []
    scanned = 0
    for member in incubation:
        mid = member["id"]
        for lang in ("zh", "en"):
            path = apps_dir / lang / f"{mid}.md"
            if not path.is_file():
                continue  # 缺卡由 A3 点名；这里不重复
            scanned += 1
            for sentence in re.split(r"[。；;，,\n]", member_card_body(path)):
                low = sentence.lower()
                hit = next((t for t in DELIVERY_CLAIM_TOKENS if t.lower() in low), None)
                if not hit:
                    continue
                if any(n in sentence for n in NEGATIONS) or any(n in low for n in NEGATIONS_EN):
                    continue
                problems.append(f"{lang}/{mid}.md 宣称交付「{hit}」：{sentence.strip()[:60]}")

    if not scanned:
        return skip("incubation 成员卡均不存在，无从判定")
    if problems:
        return bad("；".join(problems[:MAX_REPORT]))
    return ok(f"{len(incubation)} 个 incubation 成员 × 2 语，卡片正文零交付主张")


# --------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="识律产品条线一致性门（只读）")
    ap.add_argument("--web-root", type=Path, default=ROOT / "fd-lex" / "web")
    ap.add_argument("--site-root", type=Path, default=ROOT / "fd-official-web")
    ap.add_argument("--presets-root", type=Path, default=ROOT / "docs" / "legal-presets")
    ap.add_argument("--catalog", type=Path, default=ROOT / "docs" / "CUSTOMER-SERVICE-CATALOG.md")
    ap.add_argument("--strict", action="store_true", help="SKIP 视为失败（CI 用）")
    args = ap.parse_args()

    truth_path = args.web_root.parent / "docs" / "product-lineup.json"
    truth, first = load_truth(truth_path)
    if truth is None:
        print(f"{first.code:<4} A0 真源          {first.detail}")
        return 1

    checks: list[tuple[str, str, Result]] = []

    def run(name: str, label: str, fn) -> None:
        try:
            checks.append((name, label, fn()))
        except Exception as exc:  # 门自身出错必须显形，不能静默放行
            checks.append((name, label, bad(f"门内部错误：{type(exc).__name__}: {exc}")))

    run("A0", "真源结构", lambda: check_a0(truth))
    run("A1", "站点目录", lambda: check_a1(truth, args.web_root))
    run("A2", "套件映射", lambda: check_a2(truth, args.presets_root))
    run("A3", "成员卡", lambda: check_a3(truth, args.site_root))
    run("A4", "禁用承诺", lambda: check_a4(truth, args.web_root, args.site_root))
    run("A5", "数字白名单", lambda: check_a5(truth, args.web_root, args.site_root, args.catalog))
    run("A6", "宿主一致性", lambda: check_a6(truth, args.web_root))
    run("A7", "人读矩阵", lambda: check_a7_matrix(truth, args.web_root))
    run("A8", "成员交付主张", lambda: check_a8_delivery_claims(truth, args.site_root))

    failures = 0
    skips = 0
    for name, label, res in checks:
        head = f"{res.code:<4} {name} {label}"
        print(f"{head:<28} {res.detail.splitlines()[0] if res.detail else ''}")
        for extra in res.detail.splitlines()[1:]:
            print(f"     {extra}")
        if res.code == FAIL:
            failures += 1
        elif res.code == SKIP:
            skips += 1

    print()
    if failures:
        print(f"红：{failures} 项断言失败。按上面逐条修；改完重跑本门。")
        return 1
    if skips and args.strict:
        print(f"红：严格模式下 {skips} 项 SKIP 视为失败（真源之外的投影路径缺失）。")
        return 1
    if skips:
        print(f"绿：0 项失败，{skips} 项 SKIP（非严格模式放行；CI 请加 --strict）。")
        return 0
    print("绿：九类断言全过（A0-A8），四层投影与真源一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

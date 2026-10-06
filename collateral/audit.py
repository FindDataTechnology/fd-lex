#!/usr/bin/env python3
"""lex-collateral 红线审计（change: lex-marketing-kit，spec: lex-collateral）

三项检查：
  1. number-trace  材料中每个数字必须能在 collateral/numbers.md 的「值」列找到（年份/个位数/版本号豁免）
  2. model-promise 模型交付承诺扫描（随包提供模型文件/适配器/权重等措辞）；同行含 v1.x 标注视为合规
  3. forbidden     禁区扫描：价格数字、客户案例、用量数据

用法：
  python3 collateral/audit.py                 # 扫描默认成品集
  python3 collateral/audit.py <file> [...]    # 扫描指定文件
  python3 collateral/audit.py --parity <zh> <en>   # 双语结构对等检查（章节+数字）
  python3 collateral/audit.py --selftest      # 注入违规样例自检

退出码：0=通过，1=有违规。"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # fd-lex/
NUMBERS = Path(__file__).resolve().parent / "numbers.md"

DEFAULT_TARGETS = [
    "collateral/onepager-business/*.html",
    "collateral/onepager-arch/*.html",
    "collateral/wechat/*.md",
    "web/src/content/docs/zh/*.md",
    "web/src/content/docs/en/*.md",
]

# ---- number tokenization -----------------------------------------------------

NUM_RE = re.compile(r"\d[\d,，]*(?:\.\d+)?")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
VERSION_RE = re.compile(r"v\d+(?:\.\d+)+|\d+(?:\.\d+){2,}", re.IGNORECASE)


def norm(s: str) -> str:
    return s.replace(",", "").replace("，", "").rstrip(".")


def table_numbers() -> set[str]:
    """numbers.md「值」列中的全部数字（规范化）。"""
    out: set[str] = set()
    for line in NUMBERS.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(N\d+)\s*\|", line)
        if not m:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 4:
            continue
        value_cell = cells[3]
        for tok in NUM_RE.findall(value_cell):
            out.add(norm(tok))
    return out


def extract_numbers(text: str):
    """返回 [(token, line_no, line)]；跳过年份、版本号、个位数、[N#] 引用标记。"""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = re.sub(r"^\s*[-*|>#\s]+", "", line)
        if stripped.startswith("<!--") or "audit-exempt" in line:
            continue
        line = re.sub(r"\[?\s*N\d+\s*\]?", " ", line)  # 剥离 [N1] 类口径引用标记
        # 去掉版本号 token 避免误报（v1.x、4.0.11）
        line_wo_ver = VERSION_RE.sub(" ", line)
        if '"jsonrpc"' in line or "'jsonrpc'" in line:
            line_wo_ver = line_wo_ver.replace("2.0", " ")  # JSON-RPC 协议版本常量
        for m in NUM_RE.finditer(line_wo_ver):
            tok = norm(m.group())
            if YEAR_RE.match(tok) or len(tok.replace(".", "").lstrip("0") or "0") <= 1:
                continue
            if re.fullmatch(r"[1-5]\d{2}", tok):  # HTTP 状态码等协议常量
                continue
            if line_wo_ver[max(0, m.start() - 3):m.start()].lower() == "sha":  # sha256 等哈希常量
                continue
            hits.append((tok, i, line.strip()))
    return hits


# ---- rule scanners -----------------------------------------------------------

MODEL_PROMISE_RE = re.compile(
    r"(随包提供|随包交付|随交付包提供|随交付包交付|交付模型文件|交付模型权重|交付适配器|"
    r"提供模型文件|提供模型权重|提供适配器|模型文件随|模型权重随|适配器随包|量化产物随包|"
    r"ship(?:ping)?\s+(?:the\s+)?model\s+(?:files?|weights?|adapters?)|deliver(?:y\s+of)?\s+model\s+(?:files?|weights?|adapters?)|"
    r"model\s+(?:files?|weights?|adapters?)\s+(?:are\s+)?(?:shipped|included|delivered))",
    re.IGNORECASE,
)
PRICE_RE = re.compile(r"(￥|¥|USD\s?\$|\$\s?\d|\d+\s*(?:元|万元|美元)|价格[:：]?\s*\d|定价[:：]?\s*\d|priced?\s+at\s+\d|price[:：]\s*\d)", re.IGNORECASE)
CASE_RE = re.compile(r"(客户案例|成功案例|客户名单|某客户|首家客户|case\s+study|testimonial|customer\s+story)", re.IGNORECASE)
USAGE_RE = re.compile(r"(已服务|已处理|累计调用|调用量达|处理了?\s*[\d,，]+\s*次|requests?\s+served|calls?\s+processed)", re.IGNORECASE)


STYLE_BLOCK_RE = re.compile(r"<(style|script)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _visible(path: Path, text: str) -> str:
    """HTML 剥离样式/脚本块与注释（按块保留行数，行号近似不变）。"""
    if path.suffix.lower() != ".html":
        return text

    def _blank(m):
        return "\n" * m.group().count("\n")

    return HTML_COMMENT_RE.sub(_blank, STYLE_BLOCK_RE.sub(_blank, text))


def scan_file(path: Path):
    """返回违规列表 [(rule, line_no, detail)]。"""
    text = _visible(path, path.read_text(encoding="utf-8", errors="replace"))
    issues = []
    allowed = table_numbers()

    for tok, ln, line in extract_numbers(text):
        if tok not in allowed:
            issues.append(("number-trace", ln, f"数字 {tok!r} 不在口径表：{line[:80]}"))

    for i, line in enumerate(text.splitlines(), 1):
        if MODEL_PROMISE_RE.search(line) and "v1.x" not in line:
            issues.append(("model-promise", i, f"模型交付承诺且无 v1.x 标注：{line.strip()[:80]}"))
        for name, rx in (("forbidden-price", PRICE_RE), ("forbidden-case", CASE_RE), ("forbidden-usage", USAGE_RE)):
            if rx.search(line):
                issues.append((name, i, f"{line.strip()[:80]}"))
    return issues


# ---- bilingual parity --------------------------------------------------------

HEAD_RE = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
HTML_HEAD_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


def _headings(path: Path, text: str):
    if path.suffix.lower() == ".html":
        body = STYLE_BLOCK_RE.sub("", text)
        return [(int(l), TAG_RE.sub("", t).strip()) for l, t in HTML_HEAD_RE.findall(body)]
    return [(len(h), t.strip()) for h, t in HEAD_RE.findall(text)]


def extract_token_values(text: str):
    """[(token, 量级换算后的数值)]——「3,700 万」按 37,000,000 计。"""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        stripped = re.sub(r"^\s*[-*|>#\s]+", "", line)
        if stripped.startswith("<!--") or "audit-exempt" in line:
            continue
        line2 = re.sub(r"\[?\s*N\d+\s*\]?", " ", line)
        line2 = VERSION_RE.sub(" ", line2)
        line2 = TAG_RE.sub(" ", line2)
        for m in NUM_RE.finditer(line2):
            tok = norm(m.group())
            if YEAR_RE.match(tok) or len(tok.replace(".", "").lstrip("0") or "0") <= 1:
                continue
            rest = line2[m.end():m.end() + 8].lstrip()
            scale = 1e8 if rest.startswith(("亿", "億")) else 1e4 if rest.startswith(("万", "萬")) else 1.0
            try:
                out.append((tok, float(tok) * scale))
            except ValueError:
                pass
    return out


def parity(zh: Path, en: Path):
    """章节结构与数字集合对等。返回差异列表。"""
    zh_raw, en_raw = zh.read_text(encoding="utf-8"), en.read_text(encoding="utf-8")
    zh_text, en_text = _visible(zh, zh_raw), _visible(en, en_raw)
    diffs = []
    zh_heads = _headings(zh, zh_text)
    en_heads = _headings(en, en_text)
    if len(zh_heads) != len(en_heads):
        diffs.append(f"章节数不等：zh={len(zh_heads)} en={len(en_heads)}")
    else:
        for (zl, zt), (el, et) in zip(zh_heads, en_heads):
            if zl != el:
                diffs.append(f"章节级别不一致：zh「{zt[:30]}」(h{zl}) vs en「{et[:30]}」(h{el})")
    zh_vals = extract_token_values(zh_text)
    en_vals = extract_token_values(en_text)
    zh_map, en_map = {}, {}
    for tok, v in zh_vals:
        zh_map.setdefault(tok, []).append(v)
    for tok, v in en_vals:
        en_map.setdefault(tok, []).append(v)

    def _close(tok: str, vals: list, other_map: dict, other_keys: set) -> bool:
        for o in other_keys:
            for x in other_map.get(o, []):
                for v in vals:
                    if x != 0 and abs(x - v) / max(abs(x), abs(v)) <= 0.01:
                        return True
        return False

    zh_only_keys = set(zh_map) - set(en_map)
    en_only_keys = set(en_map) - set(zh_map)
    for tok in sorted(zh_only_keys):
        if not _close(tok, zh_map[tok], en_map, en_only_keys):
            diffs.append(f"数字 {tok} 仅出现在 zh 版（对侧无）")
    for tok in sorted(en_only_keys):
        if not _close(tok, en_map[tok], zh_map, zh_only_keys):
            diffs.append(f"数字 {tok} 仅出现在 en 版（对侧无）")
    return diffs


# ---- selftest ----------------------------------------------------------------

def selftest() -> int:
    ok = True
    cases = [
        ("number-trace", "本次活动共 123,456 条数据。\n"),
        ("model-promise", "交付包随包提供模型文件，开箱即用。\n"),
        ("model-promise-ok", "模型能力随交付包规划提供（v1.x 标注）。\n"),
        ("forbidden-price", "服务定价：99 元/月。\n"),
        ("forbidden-case", "我们的客户案例包括某银行。\n"),
        ("forbidden-usage", "平台已服务 1,000,000 次调用。\n"),
    ]
    for rule, body in cases:
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(body)
            tmp = Path(f.name)
        got = {r for r, _, _ in scan_file(tmp)}
        tmp.unlink()
        expect_hit = rule != "model-promise-ok"
        hit = rule in got
        status = "PASS" if hit == expect_hit else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  [{status}] {rule}: got={sorted(got)}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---- main --------------------------------------------------------------------

def main() -> int:
    argv = sys.argv[1:]
    if "--selftest" in argv:
        return selftest()
    if "--parity" in argv:
        i = argv.index("--parity")
        diffs = parity(Path(argv[i + 1]), Path(argv[i + 2]))
        for d in diffs:
            print(f"PARITY {d}")
        print("PARITY", "PASS" if not diffs else "FAIL")
        return 0 if not diffs else 1

    if argv:
        targets = [Path(a) for a in argv]
    else:
        targets = [p for pat in DEFAULT_TARGETS for p in sorted(ROOT.glob(pat))]
    if not targets:
        print("no target files found")
        return 0

    total = 0
    for t in targets:
        issues = scan_file(t)
        try:
            show = t.resolve().relative_to(ROOT)
        except ValueError:
            show = t
        for rule, ln, detail in issues:
            print(f"VIOLATION {show}:{ln} [{rule}] {detail}")
        total += len(issues)
    print(f"AUDIT {'PASS' if total == 0 else 'FAIL'} — {total} violation(s) across {len(targets)} file(s)")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

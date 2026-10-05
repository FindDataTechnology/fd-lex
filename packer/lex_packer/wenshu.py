"""裁判文书子集（文书包）取数：wenshu_db.public.judgments + RustFS zstd 块。

spec lex-private-distribution「数据三档分层」：文书包（裁判文书全文）默认不随标准包，
SHALL 仅在按项目完成法务审核后定制提供；本模块只服务**子集**形态
（``--judgment-limit``，硬上限 1000），绝不真打全量（全量约 175GB，挂起）。

实址口径（2026-10-05 实测）：
- 元数据：``wenshu_db.public.judgments``（37,001,012 行；PK ``doc_id``）；
  真实列为中文列（案号/标题/判决时间/判决结果/正文形态/block_key/block_ordinal）
  + 字典引用列（法院引用/案由引用/…，解码表 ``public.dict_texts``（列, ref_key, txt））。
- 正文：RustFS bucket ``wenshu`` 的 zstd 块对象，key 形如 ``blocks/2025/02/000040.zst``；
  块头 JSON ``{"docs": [...], "offsets": [...]}``，双写方格式：
  - blocks.js：整对象 zstd（magic ``WENSHUBLOCK1`` 行 + JSON 头 + payload，offsets 为
    ``len(docs)+1`` 个边界）；
  - legacy（现网主流，实测命中的就是它）：u32-BE 头长度 + JSON 头 + zstd payload，
    offsets 为每篇绝对 ``[start, byte_len]``。
  解析口径与 fd-legal-search-mcp ``tools_case.py`` 同源（双格式嗅探），保证包内正文与
  线上 wenshu_read 逐字节一致。

隐私/内部坐标铁律（沿用 wenshu_read 投影白名单）：包内行**不含**
``block_key``/``block_ordinal``/``src_id``/``id_origin`` 与任何原始字典引用列，
只含解码后的文本字段与正文。
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .errors import TierGateError

#: 文书子集硬上限：spec 挂起全量，构建器只做 ≤1000 篇抽样子集功能验证。
MAX_JUDGMENT_LIMIT = 1000
DEFAULT_JUDGMENT_LIMIT = 1000

BLOCK_MAGIC = "WENSHUBLOCK1"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

#: 内部坐标与原始字典引用（禁止进入包内行）。
FORBIDDEN_KEYS = frozenset({
    "block_key", "block_ordinal", "src_id", "id_origin",
    "法院引用", "案由引用", "省引用", "市引用", "案件类型引用",
    "文书类型引用", "审理程序引用", "案例级别引用", "法院级别引用", "法条引用",
})

#: 包内投影（= wenshu_read 输出字段 + 正文；内部坐标在解码后剔除）。
PROJECT_FIELDS = (
    "doc_id", "案号", "标题", "法院", "案由", "省", "市", "案件类型", "文书类型",
    "审理程序", "法院级别", "判决年份", "判决结果", "发布时间", "判决时间",
    "受理时间", "body_shape", "content",
)

#: 静态 SQL（列名硬编码，无字符串拼接；LIMIT 走参数化）。ORDER BY doc_id 走 PK 索引。
SUBSET_SQL = """
SELECT j.doc_id,
       j."案号" AS "案号",
       j."标题" AS "标题",
       j."判决结果" AS "判决结果",
       j."发布时间" AS "发布时间",
       j."判决时间" AS "判决时间",
       j."受理时间" AS "受理时间",
       j."判决年份" AS "判决年份",
       j."正文形态" AS "body_shape",
       j.block_key AS "_block_key",
       df."txt" AS "法院",
       da."txt" AS "案由",
       dp."txt" AS "省",
       dc."txt" AS "市",
       dct."txt" AS "案件类型",
       dwt."txt" AS "文书类型",
       dpr."txt" AS "审理程序",
       dcl."txt" AS "法院级别"
FROM public.judgments j
LEFT JOIN public.dict_texts df  ON df."列"  = '法院'     AND df.ref_key  = j."法院引用"
LEFT JOIN public.dict_texts da  ON da."列"  = '案由'     AND da.ref_key  = j."案由引用"
LEFT JOIN public.dict_texts dp  ON dp."列"  = '省'       AND dp.ref_key  = j."省引用"
LEFT JOIN public.dict_texts dc  ON dc."列"  = '市'       AND dc.ref_key  = j."市引用"
LEFT JOIN public.dict_texts dct ON dct."列" = '案件类型' AND dct.ref_key = j."案件类型引用"
LEFT JOIN public.dict_texts dwt ON dwt."列" = '文书类型' AND dwt.ref_key = j."文书类型引用"
LEFT JOIN public.dict_texts dpr ON dpr."列" = '审理程序' AND dpr.ref_key = j."审理程序引用"
LEFT JOIN public.dict_texts dcl ON dcl."列" = '法院级别' AND dcl.ref_key = j."法院级别引用"
WHERE j.block_key IS NOT NULL
ORDER BY j.doc_id
LIMIT %s
""".strip()


def assert_subset_limit(limit: int) -> int:
    """子集上限校验：1..MAX_JUDGMENT_LIMIT（>1000 = 拒绝打全量）。"""
    if not isinstance(limit, int) or limit < 1:
        raise TierGateError(f"文书子集上限非法：{limit!r}（须为 1..{MAX_JUDGMENT_LIMIT}）")
    if limit > MAX_JUDGMENT_LIMIT:
        raise TierGateError(
            f"拒绝文书子集上限 {limit}：文书全量（约 175GB）挂起，构建器只做 "
            f"≤{MAX_JUDGMENT_LIMIT} 篇抽样子集功能验证（lex-platform-v1 6.2 切法）"
        )
    return limit


# ---------------------------------------------------------------------------
# 块解析（双写方嗅探；与 fd-legal-search-mcp tools_case.py 同源口径）
# ---------------------------------------------------------------------------


def _zstd_read(blob: bytes) -> bytes | None:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise TierGateError(
            "读取文书正文块需要 zstandard：pip install 'lex-packer[wenshu]'"
        ) from exc
    try:
        return zstandard.ZstdDecompressor().stream_reader(io.BytesIO(blob)).read()
    except Exception:  # noqa: BLE001 - 坏块 fail-soft，由调用侧如实计数
        return None


def parse_block(compressed: bytes) -> tuple[list[str], Callable[[int], bytes | None]] | None:
    """解析块对象为 ``(docs, slicer)``；两种写方格式皆可，坏块返回 None。"""
    if compressed[:4] == _ZSTD_MAGIC:
        return _parse_blocks_js(compressed)
    return _parse_legacy(compressed)


def _parse_blocks_js(compressed: bytes) -> tuple[list[str], Callable[[int], bytes | None]] | None:
    """blocks.js 布局：整对象 zstd（magic 行 + JSON 头 + payload）。"""
    dec = _zstd_read(compressed)
    if dec is None:
        return None
    nl = dec.find(b"\n")
    if nl == -1 or dec[:nl] != BLOCK_MAGIC.encode("utf-8"):
        return None
    start = nl + 1
    nl2 = dec.find(b"\n", start)
    if nl2 == -1:
        return None
    try:
        header = json.loads(dec[start:nl2].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    docs = header.get("docs") if isinstance(header, dict) else None
    offsets = header.get("offsets") if isinstance(header, dict) else None
    if (not isinstance(docs, list) or not isinstance(offsets, list)
            or len(offsets) != len(docs) + 1):
        return None
    payload_start = nl2 + 1

    def slicer(i: int) -> bytes | None:
        a, b = offsets[i], offsets[i + 1]
        if (not isinstance(a, int) or not isinstance(b, int)
                or a < 0 or b < a or payload_start + b > len(dec)):
            return None
        return dec[payload_start + a:payload_start + b]

    return docs, slicer


def _parse_legacy(compressed: bytes) -> tuple[list[str], Callable[[int], bytes | None]] | None:
    """legacy（现网主流）：u32-BE 头长度 + JSON 头 + zstd payload。"""
    if len(compressed) < 5:
        return None
    plen = int.from_bytes(compressed[:4], "big")
    if plen <= 0 or 4 + plen >= len(compressed):
        return None
    try:
        header = json.loads(compressed[4:4 + plen].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    docs = header.get("docs") if isinstance(header, dict) else None
    offsets = header.get("offsets") if isinstance(header, dict) else None
    if (not isinstance(docs, list) or not isinstance(offsets, list)
            or len(offsets) != len(docs)):
        return None
    payload = _zstd_read(compressed[4 + plen:])
    if payload is None:
        return None

    def slicer(i: int) -> bytes | None:
        pair = offsets[i]
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(v, int) for v in pair)
                or pair[0] < 0 or pair[1] < 0
                or pair[0] + pair[1] > len(payload)):
            return None
        return payload[pair[0]:pair[0] + pair[1]]

    return docs, slicer


def decode_doc(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def project_row(row: dict[str, Any], *, content: str | None) -> dict[str, Any]:
    """按投影白名单出包（剔除内部坐标/原始引用；content 为 None 时丢弃该行）。"""
    projected = {key: row.get(key) for key in PROJECT_FIELDS if key != "content"}
    for key in FORBIDDEN_KEYS:  # 双保险：任何内部坐标都不得随行出货
        projected.pop(key, None)
    if content is None:
        projected["content"] = None
        projected["body_available"] = False
        return projected
    projected["content"] = content
    projected["body_available"] = True
    return projected


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------


@dataclass
class SubsetStats:
    requested: int
    packed: int
    skipped_missing_body: int
    blocks_fetched: int
    blocks_failed: int


class RustfsBlockReader:
    """RustFS 块读取（boto3；只读 GET）。"""

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str,
                 bucket: str = "wenshu", region: str = "us-east-1", timeout: int = 60) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
            from botocore.config import Config  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise TierGateError(
                "读取文书正文块需要 boto3：pip install 'lex-packer[wenshu]'"
            ) from exc
        self.bucket = bucket
        self._client = boto3.client(
            "s3", endpoint_url=endpoint, aws_access_key_id=access_key,
            aws_secret_access_key=secret_key, region_name=region,
            config=Config(connect_timeout=10, read_timeout=timeout,
                          retries={"max_attempts": 1}, s3={"addressing_style": "path"}),
        )

    def fetch(self, block_key: str) -> bytes | None:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=block_key)["Body"].read()
        except Exception:  # noqa: BLE001 - 坏块 fail-soft，调用侧如实计数
            return None


def iter_subset_rows(
    candidates: Iterator[dict[str, Any]],
    block_reader: Any,
    *,
    limit: int,
    stats: SubsetStats,
) -> Iterator[dict[str, Any]]:
    """候选行 → 出包行（逐篇补正文；坏块/缺正文的篇跳过并计数）。

    触发 SQL 游标：candidates 是生成器，本函数消费它。
    """
    cache: dict[str, tuple[list[str], Callable[[int], bytes | None]] | None] = {}
    for row in candidates:
        if stats.packed >= limit:
            break
        block_key = row.pop("_block_key", None)
        if not block_key:
            stats.skipped_missing_body += 1
            continue
        if block_key not in cache:
            blob = block_reader.fetch(block_key)
            stats.blocks_fetched += 1
            parsed = parse_block(blob) if blob is not None else None
            if parsed is None:
                stats.blocks_failed += 1
            cache[block_key] = parsed
            if len(cache) > 4:  # 块解压后约 8MB，保留极少量
                cache.pop(next(iter(cache)))
        parsed = cache.get(block_key)
        if parsed is None:
            stats.skipped_missing_body += 1
            continue
        docs, slicer = parsed
        try:
            index = docs.index(str(row["doc_id"]))
        except ValueError:
            stats.skipped_missing_body += 1
            continue
        content = decode_doc(slicer(index))
        if content is None:
            stats.skipped_missing_body += 1
            continue
        projected = project_row(row, content=content)
        if not projected.get("body_available"):
            stats.skipped_missing_body += 1
            continue
        stats.packed += 1
        yield projected


def iter_wenshu_candidates(dsn: str, *, limit: int, itersize: int = 200) -> Iterator[dict[str, Any]]:
    """wenshu_db 只读流式候选（严格子集上限；SQL 全静态 + 参数化 LIMIT）。"""
    assert_subset_limit(limit)
    try:
        import psycopg2  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise TierGateError(
            "wenshu_db 直读需要 psycopg2：pip install 'lex-packer[wenshu]'"
        ) from exc
    conn = psycopg2.connect(dsn)
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor(name="lex_packer_wenshu") as cursor:
            cursor.itersize = itersize
            cursor.execute(SUBSET_SQL, (limit,))
            # 命名游标在取到首行前 description 为 None（同 sources.LawDbSource 口径）
            columns: list[str] | None = None
            for raw in cursor:
                if columns is None:
                    columns = [desc[0] for desc in cursor.description]
                yield dict(zip(columns, raw))
        conn.commit()
    finally:
        conn.close()


def subset_source_note(stats: SubsetStats, *, limit: int, bucket: str = "wenshu") -> str:
    return (
        f"wenshu_db.public.judgments（{bucket} 桶 zstd 块补正文）；"
        f"requested={stats.requested} packed={stats.packed} "
        f"skipped_missing_body={stats.skipped_missing_body} "
        f"blocks_fetched={stats.blocks_fetched} blocks_failed={stats.blocks_failed}；"
        f"子集上限={limit}（全量约 175GB 挂起，绝不真打）"
    )
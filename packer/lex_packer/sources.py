"""数据源适配（只读）与 JSONL 落盘。

数据源口径（INFRA-NOTES / fd-legal-search-mcp/.env）：
- law_db：``fdbiz_ro@100.64.0.5:5432/law_db``（只读账号）；表
  laws=32,296 / law_chunks_v2 / guide_cases=293 / legal_documents=29,958。
- wenshu_db：同实例（judgments），文书包只做门禁与子集，绝不真打全量。
- SAMR 合同范本语料（scraw-law-contracts 爬虫产物）实址不在本机：见
  ``SamrContractSource`` 文档与 README「合同范本语料实址」。

只读铁律：PG 会话强制 ``readonly=True``；SQL 全静态（无字符串拼接用户输入）。
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator

DEFAULT_LAW_DB_DSN_ENV = "FDLEX_LAW_DATABASE_URL"

LAWS_SQL = """
SELECT id, src_db, src_id, source_id, category, title, publish, expiry, type, status,
       file_path, content, content_status, quality_flag, source_note, created_at
FROM public.laws
ORDER BY id
""".strip()

LAW_CHUNKS_SQL = """
SELECT chunk_id, law_id, chunk_no, chunk_text, char_start, char_end, category, title,
       publish, embedded, created_at
FROM public.law_chunks_v2
ORDER BY law_id, chunk_no
""".strip()

GUIDE_CASES_SQL = """
SELECT source_id, case_no, doc_type, title, listed_at, publish_at, source, source_url,
       fetched_at, content_hash, keyword, gist, facts, result, reasoning, statutes,
       body, reference_status, reference_note, loaded_at
FROM public.guide_cases
ORDER BY source_id
""".strip()

LEGAL_DOCUMENTS_SQL = """
SELECT id, source, source_id, doc_type, title, published_at, effective_at, status,
       authority, language, content, content_hash, source_url, metadata, fetched_at, loaded_at
FROM public.legal_documents
ORDER BY id
""".strip()

def json_default(value: Any) -> Any:
    """PG 值 → JSON：日期/Decimal 显式化，未知类型转字符串不静默丢字段。"""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


@dataclass
class ComponentStats:
    """一次落盘同时得到的（行数, 字节数, sha256）——避免为算摘要再读一遍大文件。"""

    rows: int
    bytes: int
    sha256: str


class _DigestFile:
    """包装文件对象：每次写入同时更新 sha256（= 落盘文件内容的摘要）。"""

    def __init__(self, handle: Any, digest: Any) -> None:
        self._handle = handle
        self._digest = digest

    def write(self, data: bytes) -> int:
        self._digest.update(data)
        return self._handle.write(data)

    def close(self) -> None:
        self._handle.close()


def _open_gzip_writer(path: Path):
    import hashlib

    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    raw = _DigestFile(open(path, "wb"), digest)
    writer = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
    return writer, raw, digest


def _encode_row(row: dict[str, Any]) -> bytes:
    return json.dumps(
        row, ensure_ascii=False, sort_keys=True, default=json_default,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def write_jsonl_gz(path: Path, rows: Iterable[dict[str, Any]]) -> ComponentStats:
    """稳定 JSONL（sorted keys + UTF-8）gzip 落盘；gzip mtime=0 保证可复现。

    返回行数/字节数/落盘文件 sha256（边写边算，摘要即 pack 内该组件的校验值）。
    """
    writer, raw, digest = _open_gzip_writer(path)
    count = 0
    try:
        for row in rows:
            writer.write(_encode_row(row))
            count += 1
    finally:
        writer.close()
        raw.close()
    return ComponentStats(rows=count, bytes=path.stat().st_size, sha256=digest.hexdigest())


def write_jsonl_gz_tee(
    primary: Path,
    secondary: Path,
    rows: Iterable[dict[str, Any]],
    *,
    project: Any,
) -> tuple[ComponentStats, ComponentStats]:
    """一趟扫描同时落两个文件（如 laws 与 law_catalog 同源投影）。"""
    writer_a, raw_a, digest_a = _open_gzip_writer(primary)
    writer_b, raw_b, digest_b = _open_gzip_writer(secondary)
    count_a = count_b = 0
    try:
        for row in rows:
            writer_a.write(_encode_row(row))
            count_a += 1
            writer_b.write(_encode_row(project(row)))
            count_b += 1
    finally:
        writer_a.close()
        raw_a.close()
        writer_b.close()
        raw_b.close()
    return (
        ComponentStats(rows=count_a, bytes=primary.stat().st_size, sha256=digest_a.hexdigest()),
        ComponentStats(rows=count_b, bytes=secondary.stat().st_size, sha256=digest_b.hexdigest()),
    )


def read_jsonl_gz(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    """抽样读取（构建后可复现性硬证据用）。"""
    items: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            items.append(json.loads(line))
            if limit is not None and len(items) >= limit:
                break
    return items


class LawDbSource:
    """law_db 只读适配（psycopg2，可选依赖；``pip install lex-packer[pg]``）。"""

    def __init__(self, dsn: str, *, itersize: int = 5000) -> None:
        try:
            import psycopg2  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - 环境相关
            raise RuntimeError(
                "law_db 直读需要 psycopg2：pip install 'lex-packer[pg]'（或走离线 JSONL 输入）"
            ) from exc
        self._psycopg2 = psycopg2
        self._conn = psycopg2.connect(dsn)
        # 只读会话：任何写入在这一层就被 PG 拒绝
        self._conn.set_session(readonly=True, autocommit=False)
        self._itersize = itersize

    def stream(self, sql: str, params: tuple | None = None) -> Iterator[dict[str, Any]]:
        """服务端游标流式读取（大表不整读内存）；SQL 必须为静态常量。

        注意：psycopg2 命名游标在取到第一行之前 ``description`` 为 None，
        故列名在首行到达时才解析。
        """
        with self._conn.cursor(name="lex_packer_stream") as cursor:
            cursor.itersize = self._itersize
            cursor.execute(sql, params)
            columns: list[str] | None = None
            for row in cursor:
                if columns is None:
                    columns = [desc[0] for desc in cursor.description]
                yield dict(zip(columns, row))
        self._conn.commit()

    def scalar(self, sql: str) -> Any:
        with self._conn.cursor() as cursor:
            cursor.execute(sql)
            value = cursor.fetchone()[0]
        self._conn.commit()
        return value

    def counts(self) -> dict[str, int]:
        return {
            "laws": int(self.scalar("SELECT count(*) FROM public.laws")),
            "law_chunks_v2": int(self.scalar("SELECT count(*) FROM public.law_chunks_v2")),
            "guide_cases": int(self.scalar("SELECT count(*) FROM public.guide_cases")),
            "legal_documents": int(self.scalar("SELECT count(*) FROM public.legal_documents")),
        }

    def close(self) -> None:
        self._conn.close()


class SamrContractSource:
    """SAMR 合同范本语料入口（scraw-law-contracts 爬虫产物）。

    实址调查结论（2026-10-05）：
    - 爬虫仓 ``scraw-law-contracts``（不在 finddata 工作树；bare 镜像
      ``/Users/chengsishi/finddata/attic/scraw-law-contracts.git``），
      Helm chart ``helm-law-scraw`` 以 CronJob（每日 02:00）部署；
    - 产物落点：SQLite ``data/scraw.db`` + JSONL ``output/items.jl``（PVC）与
      对象存储 bucket ``scraw-law-contracts``（chart 内 MinIO endpoint
      ``minio.minio.svc.cluster.local:9000``；历史上另有
      ``23.144.68.246:30900`` 的 HTTP 实例）；
    - 本机/tailnet 均不可直达：RustFS(100.64.0.11) 无该 bucket，23.144.68.246:30900
      超时。故构建器只保留接口位：语料包构建必须显式提供本地 JSONL/SQLite
      （或在可达环境直读），缺席即经 --allow-missing 如实记录 missing_inputs。
    """

    def __init__(self, rows: Iterator[dict[str, Any]], *, origin: str) -> None:
        self._rows = rows
        self.origin = origin

    @classmethod
    def from_jsonl(cls, path: Path) -> "SamrContractSource":
        def _iter() -> Iterator[dict[str, Any]]:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        yield json.loads(line)

        return cls(_iter(), origin=f"samr-jsonl:{path}")

    @classmethod
    def from_sqlite(cls, path: Path) -> "SamrContractSource":
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

        def _iter() -> Iterator[dict[str, Any]]:
            try:
                cursor = conn.execute("SELECT * FROM contracts")
                columns = [desc[0] for desc in cursor.description]
                for row in cursor:
                    yield dict(zip(columns, row))
            finally:
                conn.close()

        return cls(_iter(), origin=f"samr-sqlite:{path}")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self._rows
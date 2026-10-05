"""lex-packer 测试共用夹具：合成数据源（离线，不触网/不触库）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lex_packer.build import BuildRequest, build_bundle  # noqa: E402

TIER_SOURCE_FILES: dict[str, list[dict]] = {
    "laws": [
        {
            "id": 1,
            "src_db": "flk",
            "src_id": 101,
            "source_id": "flk-101",
            "category": "法律",
            "title": "中华人民共和国民法典",
            "publish": "2020-05-28",
            "expiry": None,
            "type": "法律",
            "status": "有效",
            "file_path": "flk/民法典.docx",
            "content": "第一条 为了保护民事主体的合法权益……",
            "content_status": "ok",
            "quality_flag": None,
            "source_note": None,
            "created_at": "2026-09-01T00:00:00+00:00",
        },
        {
            "id": 2,
            "src_db": "flk",
            "src_id": 102,
            "source_id": "flk-102",
            "category": "行政法规",
            "title": "优化营商环境条例",
            "publish": "2019-10-22",
            "expiry": None,
            "type": "行政法规",
            "status": "有效",
            "file_path": None,
            "content": "第一条 为了持续优化营商环境……",
            "content_status": "ok",
            "quality_flag": None,
            "source_note": None,
            "created_at": "2026-09-02T00:00:00+00:00",
        },
    ],
    "law_catalog": [
        {"id": 1, "title": "中华人民共和国民法典", "category": "法律", "type": "法律",
         "status": "有效", "publish": "2020-05-28", "expiry": None, "source_id": "flk-101"},
        {"id": 2, "title": "优化营商环境条例", "category": "行政法规", "type": "行政法规",
         "status": "有效", "publish": "2019-10-22", "expiry": None, "source_id": "flk-102"},
    ],
    "law_chunks_v2": [
        {"chunk_id": "1-1", "law_id": 1, "chunk_no": 1,
         "chunk_text": "第一条 为了保护民事主体的合法权益……", "char_start": 0, "char_end": 40,
         "category": "法律", "title": "中华人民共和国民法典", "publish": "2020-05-28",
         "embedded": True, "created_at": "2026-09-01T00:00:00+00:00"},
        {"chunk_id": "1-2", "law_id": 1, "chunk_no": 2, "chunk_text": "第二条 民法调整平等主体……",
         "char_start": 41, "char_end": 90, "category": "法律", "title": "中华人民共和国民法典",
         "publish": "2020-05-28", "embedded": True, "created_at": "2026-09-01T00:00:00+00:00"},
    ],
    "guide_cases": [
        {"source_id": "gc-1", "case_no": 1, "doc_type": "指导案例", "title": "指导案例1号",
         "listed_at": "2011-12-20", "publish_at": "2011-12-20T00:00:00+00:00",
         "source": "最高法", "source_url": "https://example.org/gc-1",
         "fetched_at": "2026-09-01T00:00:00+00:00", "content_hash": "abc", "keyword": None,
         "gist": "要点", "facts": "案情", "result": "结果", "reasoning": "理由",
         "statutes": "民法典", "body": "全文", "reference_status": "有效",
         "reference_note": None, "loaded_at": "2026-09-01T00:00:00+00:00"},
    ],
    "legal_documents": [
        {"id": 1, "source": "mfa-treaty-library", "source_id": "tr-1", "doc_type": "条约",
         "title": "示例条约", "published_at": "2020-01-01T00:00:00+00:00",
         "effective_at": None, "status": "有效", "authority": "外交部", "language": "zh",
         "content": "正文", "content_hash": "h1", "source_url": "https://example.org/tr-1",
         "metadata": {"k": "v"}, "fetched_at": "2026-09-01T00:00:00+00:00",
         "loaded_at": "2026-09-01T00:00:00+00:00"},
    ],
    "contract_templates": [
        {"id": "c-1", "title": "建设工程施工合同（示范文本）", "type": 4,
         "type_name": "建设工程", "tags": "新", "region": "全国", "published_on": "2020-06-01",
         "preview_url": "https://htsfwb.samr.gov.cn/View?id=c-1",
         "file_pdf_object": "contracts/4/c-1.pdf", "file_word_object": "contracts/4/c-1.docx"},
    ],
}


def write_source_dir(base: Path, components: list[str]) -> Path:
    """把合成行写为 JSONL（组件级 .jsonl 文件）。"""
    base.mkdir(parents=True, exist_ok=True)
    for name in components:
        rows = TIER_SOURCE_FILES.get(name, [])
        path = base / f"{name}.jsonl"
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return base


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    return write_source_dir(tmp_path / "src", [
        "laws", "law_catalog", "law_chunks_v2", "guide_cases", "legal_documents",
        "contract_templates",
    ])


def make_request(source_dir: Path, out_dir: Path, **overrides) -> BuildRequest:
    payload = dict(
        tier="citation",
        version="0.1.0",
        out_dir=out_dir,
        source_dir=source_dir,
        built_at="2026-10-05T00:00:00Z",
        mtime=1_000_000,
    )
    payload.update(overrides)
    return BuildRequest(**payload)


@pytest.fixture()
def citation_build(source_dir: Path, tmp_path: Path):
    """一次合成引用包构建（测试基准）。"""
    return build_bundle(make_request(source_dir, tmp_path / "dist"))
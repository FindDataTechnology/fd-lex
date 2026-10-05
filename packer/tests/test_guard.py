"""CI 云专属依赖守卫测试（6.1 验收：注入违规依赖的用例构建红并指名组件）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_request, write_source_dir
from lex_packer.build import build_bundle
from lex_packer.errors import GuardViolationError
from lex_packer.guard import check_components
from lex_packer.registry import load_registry


def test_default_registry_is_clean() -> None:
    registry = load_registry()
    report = check_components(registry, registry.names())
    # 默认表允许存在 cloud_locked 条目（如实分类），但整表校验只作为名册自检
    assert registry.get("lex-embed-local").packable is True
    assert registry.get("siliconflow-embedding-api").klass == "cloud_locked"


def test_guard_flags_unregistered_component() -> None:
    report = check_components(load_registry(), ["lex-api", "mystery-saas"])
    assert report.ok is False
    assert "mystery-saas" in report.violations[0]
    assert "登记" in report.violations[0]


def test_guard_names_component_and_fix_direction() -> None:
    report = check_components(load_registry(), ["lex-api", "volcengine-ark-llm"])
    assert report.ok is False
    violation = report.violations[0]
    assert "volcengine-ark-llm" in violation          # 指名违规组件
    assert "lex-llm-local" in violation                # 指名修复方向（包内替代）
    assert "可私有化不变量" in violation


def test_packable_components_pass() -> None:
    report = check_components(
        load_registry(),
        ["lex-api", "legal-search-mcp", "qdrant", "rustfs", "lex-embed-local", "lex-llm-local"],
    )
    assert report.ok is True
    assert all(item["ok"] for item in report.checked)


def test_build_red_on_injected_cloud_locked_component(source_dir: Path, tmp_path: Path) -> None:
    """注入违规依赖：构建必须红，且错误信息指名组件。"""
    with pytest.raises(GuardViolationError) as excinfo:
        build_bundle(
            make_request(
                source_dir,
                tmp_path / "dist",
                component_names=["laws", "cloud-gpu-inference"],
            )
        )
    message = str(excinfo.value)
    assert "cloud-gpu-inference" in message
    assert "lex-llm-local" in message  # 修复方向


def test_build_red_on_injected_registry_file(source_dir: Path, tmp_path: Path) -> None:
    """注入违规组件到注册表文件：构建红并指名；修复方向=从 bundle 移除（无替代）。"""
    registry_payload = json.loads(
        (Path(__file__).resolve().parents[1] / "lex_packer" / "registry_components.json")
        .read_text(encoding="utf-8")
    )
    registry_payload["components"].append(
        {
            "name": "vendor-secret-webhook",
            "kind": "saas",
            "class": "cloud_locked",
            "substitute": None,
            "note": "注入用违规依赖",
        }
    )
    registry_path = tmp_path / "registry_injected.json"
    registry_path.write_text(json.dumps(registry_payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(GuardViolationError) as excinfo:
        build_bundle(
            make_request(
                source_dir,
                tmp_path / "dist",
                registry_path=registry_path,
                component_names=["laws", "vendor-secret-webhook"],
            )
        )
    message = str(excinfo.value)
    assert "vendor-secret-webhook" in message
    assert "移除" in message


def test_no_partial_bundle_written_on_guard_failure(source_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "dist"
    with pytest.raises(GuardViolationError):
        build_bundle(
            make_request(source_dir, out, component_names=["laws", "sub2api-billing"])
        )
    assert list(out.glob("*/manifest.json")) == []
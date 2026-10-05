"""bundle 版本口径：``lex-dist-vX.Y.Z``（CONTEXT.md「交付包 (Distribution)」= 版本化整机交付单元）。

bundle id 形态：
- 全量：``lex-dist-v0.1.0-citation``
- 增量：``lex-dist-v0.1.0-citation-inc-2026-10-05``
"""

from __future__ import annotations

import re

BUNDLE_PREFIX = "lex-dist-v"
_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")
_BUNDLE_RE = re.compile(
    r"^lex-dist-v(?P<version>\d+\.\d+\.\d+)-(?P<tier>[a-z_]+)"
    r"(?P<form_suffix>-inc-(?P<as_of>\d{4}-\d{2}-\d{2}))?$"
)


def parse_bundle_version(version: str) -> tuple[int, int, int]:
    """解析 ``X.Y.Z``（不带前缀）；非法即 ValueError。"""
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise ValueError(f"bundle version must be X.Y.Z, got {version!r}")
    return int(match["major"]), int(match["minor"]), int(match["patch"])


def bundle_id(version: str, tier: str, *, form: str = "full", as_of: str | None = None) -> str:
    """组装 bundle id；增量形态必须带 ``as_of``（YYYY-MM-DD）。"""
    parse_bundle_version(version)
    if form not in ("full", "incremental"):
        raise ValueError(f"form must be full|incremental, got {form!r}")
    base = f"{BUNDLE_PREFIX}{version}-{tier}"
    if form == "incremental":
        if not as_of or not re.match(r"^\d{4}-\d{2}-\d{2}$", as_of):
            raise ValueError("incremental bundle requires as_of=YYYY-MM-DD")
        return f"{base}-inc-{as_of}"
    return base


def parse_bundle_id(value: str) -> dict[str, str | None]:
    """反解 bundle id（服务端/验证侧用）。"""
    match = _BUNDLE_RE.match(value)
    if not match:
        raise ValueError(f"not a lex bundle id: {value!r}")
    return {
        "version": match["version"],
        "tier": match["tier"],
        "form": "incremental" if match["as_of"] else "full",
        "as_of": match["as_of"],
    }
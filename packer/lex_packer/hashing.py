"""文件摘要工具：sha256 + 字节数（manifest 的组件 digest 锁定口径）。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

_CHUNK = 1024 * 1024


def sha256_file(path: Path | str) -> str:
    """流式哈希文件（GB 级组件不整读内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while True:
        block = stream.read(_CHUNK)
        if not block:
            break
        digest.update(block)
    return digest.hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_size(path: Path | str) -> int:
    return Path(path).stat().st_size
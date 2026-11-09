"""测试共享夹具。

**为什么需要这个文件**（审计 06 §12）：`fd_lex_api.config.metering_auto_create_tables`
的默认值已从 `True` 改为 `False`——schema 的唯一事实源应当是 alembic 迁移链，
而不是启动时的 `create_all()`（生产早已显式设 false 且表齐全）。

但仓内大量测试用 SQLite 内存/临时库 + `TestClient(app)` 触发 startup 来建表，
它们依赖那个自动建表。所以**测试里显式打开**：

    METERING_AUTO_CREATE_TABLES=1

这正是「显式比默认安全」的示范——测试要建表就明说，生产靠迁移链就不该默认建。
这样两边都不再依赖一个含糊的默认值。
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _auto_create_tables_for_tests(monkeypatch):
    """每个测试都显式打开自动建表（测试库是临时的，没有迁移链可跑）。"""
    monkeypatch.setenv("METERING_AUTO_CREATE_TABLES", "1")
    yield

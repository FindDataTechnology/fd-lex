"""schema 的唯一事实源必须是 alembic 迁移链，不能是启动时的 `create_all()`

审计 06 §12。`main._init_tables()` 在 `metering_auto_create_tables` 为真时会
`create_all()` 建 metering/billing/console/esign/matter 全套表。这个开关原来的默认值
是 **True**，配的注释是「alembic 就位后关闭」——

而 alembic 链**早就就位**（`server/alembic/versions/` 三个迁移，最新
`20261109_0003_lex_matter_spine`）。也就是说那个 TODO 的条件满足了，开关却没关。

留着 True 的后果：一个**忘了设这个 env** 的新环境会绕过迁移链直接建表。
两套建表方式并存时，谁先跑谁定义 schema，迁移链的版本号与真实表结构就会分叉——
这类分叉不会报错，只会让 `alembic upgrade` 在某天突然失败。

本文件钉住"默认关"。生产已显式设 `METERING_AUTO_CREATE_TABLES=false`
（实测 fd-prod/fd-lex-env），所以这个断言防的是**未来新环境**。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_auto_create_tables_defaults_to_false():
    """默认必须是 False——显式开启比默认开启安全。"""
    src = (REPO / "fd_lex_api" / "config.py").read_text(encoding="utf-8")
    assert "metering_auto_create_tables: bool = False" in src, (
        "`metering_auto_create_tables` 默认又不是 False 了 —— "
        "新环境会绕过 alembic 迁移链直接 create_all()，schema 事实源分叉"
    )


def test_alembic_chain_actually_exists():
    """前提：迁移链真的在，否则「关掉自动建表」会让新环境起不来。"""
    versions = list((REPO / "alembic" / "versions").glob("*.py"))
    assert len(versions) >= 3, (
        f"alembic 迁移只有 {len(versions)} 个 —— 关掉自动建表前得先确认迁移链覆盖了全部表"
    )


def test_init_tables_is_gated_by_the_flag():
    """`_init_tables()` 必须真的被那个开关挡住（不是无条件建表）。"""
    src = (REPO / "fd_lex_api" / "main.py").read_text(encoding="utf-8")
    assert "metering_auto_create_tables" in src, (
        "_init_tables() 不再受开关控制 —— 启动即建表"
    )

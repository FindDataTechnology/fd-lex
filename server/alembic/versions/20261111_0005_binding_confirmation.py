"""组织级预绑定：绑定行分「管理员预绑定」与「本人确认」两步

Revision ID: 0005_binding_confirmation
Revises: 0004_line_identity
Create Date: 2026-11-11 22:00:00.000000

变更要点（change converge-line-identity-and-keys 4.3）：

- `console_identity_bindings` 增两列：`confirmed_at`（本人确认时刻）与
  `confirmed_via`（确认通道）。管理员可以替组织成员**预绑定**（一次性建档），
  但预绑定行 `confirmed_at` 为空，平台代做不采信它——"管理员说这是你"不等于
  "这是你"，一个填错的 username 会把别人的动作记到你账上。这就是任务里那句
  "不得零确认"的落点。
- **回填既有行**：变更之前就存在的绑定行按 `confirmed_at = created_at`、
  `confirmed_via = 'legacy'` 处理。理由：这些行早已在承载计量与登录解析
  （`customer_identities` / `resolve_console_customer`），把它们降级成"待确认"
  会让线上客户立刻失去控制台可见性——那不是收紧安全，那是制造故障。回填是
  一次性的、可审计的（`confirmed_via` 明写 legacy）。
- `down_revision = '0004_line_identity'`；纯增量、可降级（降级即删两列，
  已确认信息随之丢失，故降级前须确认没有依赖它的排障）。

纪律：`METERING_AUTO_CREATE_TABLES` 默认 False，生产库靠这条链；上产前先在
真实客户库副本上跑 upgrade → downgrade → upgrade 往返。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005_binding_confirmation"
down_revision: Union[str, None] = "0004_line_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("console_identity_bindings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("confirmed_via", sa.String(length=32), nullable=True))

    # 既有行视为已确认（legacy）：它们此前就在生效，降级会让线上客户失去可见性。
    op.execute(
        sa.text(
            "UPDATE console_identity_bindings "
            "SET confirmed_at = created_at, confirmed_via = 'legacy' "
            "WHERE confirmed_at IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("console_identity_bindings", schema=None) as batch_op:
        batch_op.drop_column("confirmed_via")
        batch_op.drop_column("confirmed_at")

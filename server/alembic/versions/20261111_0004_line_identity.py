"""line identity：平台授权行 + 断言 jti 台账

Revision ID: 0004_line_identity
Revises: 0003_lex_matter
Create Date: 2026-11-11 14:10:00.000000

变更要点（change converge-line-identity-and-keys 2.1/2.2）：

- 新增 `console_platform_consents`：`consent` 档下"平台被允许代这个客户做什么"
  的显式授权行（撤销写 `revoked_at` 留痕，不删行）；
- 新增 `console_assertion_replays`：断言 `jti` 一次性台账。**必须落库**——
  进程内台账在多副本部署下形同虚设，重放换个副本就过。

为什么要单独一版迁移：`METERING_AUTO_CREATE_TABLES` 默认 **False**，生产库靠
alembic 迁移链，`ensure_console_schema()` 只是 dev 便利。少了这版，生产上
`/api/console/consent` 与兑换端点的重放判定都会因为缺表而报错。

纪律：纯增量（两张新表），降级即删；上产前须先在真实客户库副本通跑。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0004_line_identity'
down_revision: Union[str, None] = '0003_lex_matter'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'console_platform_consents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('platform_subject', sa.String(length=255), nullable=False),
        sa.Column('scope', sa.String(length=512), nullable=False),
        sa.Column('granted_by', sa.String(length=255), nullable=True),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('console_platform_consents', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_console_platform_consents_customer_id'), ['customer_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_console_platform_consents_platform_subject'),
            ['platform_subject'],
            unique=False,
        )

    op.create_table(
        'console_assertion_replays',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('jti', sa.String(length=128), nullable=False),
        sa.Column('expires_at', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('console_assertion_replays', schema=None) as batch_op:
        # 唯一索引就是"jti 只允许被认领一次"的判据本身：谁先写入谁赢，
        # 不靠应用层"先查再插"（那中间有窗口）。
        batch_op.create_index(
            batch_op.f('ix_console_assertion_replays_jti'), ['jti'], unique=True
        )
        batch_op.create_index(
            batch_op.f('ix_console_assertion_replays_expires_at'), ['expires_at'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('console_assertion_replays', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_console_assertion_replays_expires_at'))
        batch_op.drop_index(batch_op.f('ix_console_assertion_replays_jti'))
    op.drop_table('console_assertion_replays')

    with op.batch_alter_table('console_platform_consents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_console_platform_consents_platform_subject'))
        batch_op.drop_index(batch_op.f('ix_console_platform_consents_customer_id'))
    op.drop_table('console_platform_consents')

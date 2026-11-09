"""lex matter spine（案件脊柱：lex_matter_* 三表 + 签署/计量案件维度）

Revision ID: 0003_lex_matter
Revises: 0002_lex_esignature
Create Date: 2026-11-09 12:00:00.000000

变更要点（change lex-matter-spine）：
- 新增 `lex_matters` / `lex_matter_artifacts` / `lex_matter_refs`；
- `lex_esign_tasks` 增 `matter_id` / `anchor_artifact_id`（均可空，既有路径行为不变）；
- `metering_records` 增 `matter_id`（可空、带索引，纯增量）。

纪律：本迁移为纯增量（新表 + 可空列），降级即删；上产前须先在真实客户库副本通跑。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0003_lex_matter'
down_revision: Union[str, None] = '0002_lex_esignature'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lex_matters',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('customer_identity', sa.String(length=255), nullable=False),
        sa.Column('customer_principal', sa.String(length=255), nullable=True),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('cause_code', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_lex_matters_customer_identity', 'lex_matters', ['customer_identity'])
    op.create_index(
        'ix_matters_customer_time', 'lex_matters', ['customer_identity', 'created_at']
    )

    op.create_table(
        'lex_matter_artifacts',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('matter_id', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=True),
        sa.Column('producer_line', sa.String(length=32), nullable=False),
        sa.Column('producer_server', sa.String(length=128), nullable=False),
        sa.Column('producer_tool', sa.String(length=128), nullable=False),
        sa.Column('producer_version', sa.String(length=64), nullable=False),
        sa.Column('provenance_json', sa.Text(), nullable=True),
        sa.Column('payload_json', sa.Text(), nullable=False),
        sa.Column('storage_path', sa.String(length=1000), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['matter_id'], ['lex_matters.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_matter_artifacts_matter_time', 'lex_matter_artifacts', ['matter_id', 'created_at'])
    op.create_index('ix_matter_artifacts_sha', 'lex_matter_artifacts', ['sha256'])

    op.create_table(
        'lex_matter_refs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('matter_id', sa.String(length=32), nullable=False),
        sa.Column('artifact_id', sa.String(length=32), nullable=False),
        sa.Column('ref_artifact_id', sa.String(length=32), nullable=False),
        sa.Column('relation', sa.String(length=24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_matter_refs_matter', 'lex_matter_refs', ['matter_id'])
    op.create_index('ix_matter_refs_artifact', 'lex_matter_refs', ['artifact_id'])
    op.create_index('ix_matter_refs_ref', 'lex_matter_refs', ['ref_artifact_id'])

    with op.batch_alter_table('lex_esign_tasks') as batch:
        batch.add_column(sa.Column('matter_id', sa.String(length=32), nullable=True))
        batch.add_column(sa.Column('anchor_artifact_id', sa.String(length=32), nullable=True))
    op.create_index('ix_lex_esign_tasks_matter', 'lex_esign_tasks', ['matter_id'])

    with op.batch_alter_table('metering_records') as batch:
        batch.add_column(sa.Column('matter_id', sa.String(length=32), nullable=True))
    op.create_index('ix_metering_records_matter_time', 'metering_records', ['matter_id', 'event_timestamp'])


def downgrade() -> None:
    op.drop_index('ix_metering_records_matter_time', table_name='metering_records')
    with op.batch_alter_table('metering_records') as batch:
        batch.drop_column('matter_id')

    op.drop_index('ix_lex_esign_tasks_matter', table_name='lex_esign_tasks')
    with op.batch_alter_table('lex_esign_tasks') as batch:
        batch.drop_column('anchor_artifact_id')
        batch.drop_column('matter_id')

    op.drop_index('ix_matter_refs_ref', table_name='lex_matter_refs')
    op.drop_index('ix_matter_refs_artifact', table_name='lex_matter_refs')
    op.drop_index('ix_matter_refs_matter', table_name='lex_matter_refs')
    op.drop_table('lex_matter_refs')

    op.drop_index('ix_matter_artifacts_sha', table_name='lex_matter_artifacts')
    op.drop_index('ix_matter_artifacts_matter_time', table_name='lex_matter_artifacts')
    op.drop_table('lex_matter_artifacts')

    op.drop_index('ix_matters_customer_time', table_name='lex_matters')
    op.drop_index('ix_lex_matters_customer_identity', table_name='lex_matters')
    op.drop_table('lex_matters')

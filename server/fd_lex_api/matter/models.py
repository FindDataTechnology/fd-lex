"""识律案件脊柱（spec lex-matter）：案件、案卷产物与证据引用。

表口径：
- ``lex_matters``            一个案子（归属客户身份，与签署任务同键）
- ``lex_matter_artifacts``   案卷里的一张纸（内容寻址 + 生产者 + 数据口径）
- ``lex_matter_refs``        证据链边（结论 → 依据）

复用计量域同一引擎与会话（``metering.db.get_engine``），故本域不自建 DeclarativeBase，
而是挂到 ``MeteringBase`` 上——同库同事务，签署锚定校验才能与案卷读取原子一致。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..metering.db import MeteringBase

MATTER_OPEN = "open"
MATTER_ARCHIVED = "archived"
MATTER_STATUSES = frozenset({MATTER_OPEN, MATTER_ARCHIVED})

#: 证据链关系：cites（引用）/ derives_from（派生）/ supersedes（取代）
REF_RELATIONS = frozenset({"cites", "derives_from", "supersedes"})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Matter(MeteringBase):
    """一个案子。`customer_identity` 与 `lex_esign_tasks.customer_identity` 同键。"""

    __tablename__ = "lex_matters"
    __table_args__ = (Index("ix_matters_customer_time", "customer_identity", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_identity: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    customer_principal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 案由码，可缺（识别不出案由是常态，不得编造）
    cause_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=MATTER_OPEN)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "cause_code": self.cause_code,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class MatterArtifact(MeteringBase):
    """案卷产物。四项必填（producer / refs / sha256 / payload）由 contract 守卫保证。"""

    __tablename__ = "lex_matter_artifacts"
    __table_args__ = (
        Index("ix_matter_artifacts_matter_time", "matter_id", "created_at"),
        Index("ix_matter_artifacts_sha", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    matter_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("lex_matters.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    producer_line: Mapped[str] = mapped_column(String(32), nullable=False)
    producer_server: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_tool: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    #: JSON 文本：来源口径三件套（检索类必填，其余可空）
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: JSON 文本：该产物自身的可复核内容
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def as_dict(self) -> dict:
        import json

        return {
            "id": self.id,
            "matter_id": self.matter_id,
            "kind": self.kind,
            "sha256": self.sha256,
            "title": self.title,
            "producer": {
                "line": self.producer_line,
                "server": self.producer_server,
                "tool": self.producer_tool,
                "version": self.producer_version,
            },
            "provenance": json.loads(self.provenance_json) if self.provenance_json else None,
            "payload": json.loads(self.payload_json),
            "storage_path": self.storage_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class MatterRef(MeteringBase):
    """证据链边：`artifact_id` 以 `relation` 指向 `ref_artifact_id`。"""

    __tablename__ = "lex_matter_refs"
    __table_args__ = (
        Index("ix_matter_refs_matter", "matter_id"),
        Index("ix_matter_refs_artifact", "artifact_id"),
        Index("ix_matter_refs_ref", "ref_artifact_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    matter_id: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_artifact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    relation: Mapped[str] = mapped_column(String(24), nullable=False, default="cites")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


def ensure_matter_schema(engine=None) -> None:
    """幂等建 lex_matter_* 表（dev 便利；生产迁移链见 alembic 0003）。"""
    if engine is None:
        from ..metering.db import get_engine

        engine = get_engine()
    MeteringBase.metadata.create_all(engine)


__all__ = [
    "MATTER_ARCHIVED",
    "MATTER_OPEN",
    "MATTER_STATUSES",
    "REF_RELATIONS",
    "Matter",
    "MatterArtifact",
    "MatterRef",
    "ensure_matter_schema",
    "utcnow",
]

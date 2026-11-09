"""案件脊柱路由：控制台面 `/api/console/matter`（spec lex-matter）。

鉴权与控制台其余端点一致：Logto ID token（`require_console_user`）。
租户判定**只**取自已校验身份，不读请求参数——他人案件一律 404（不以 403 泄露存在性）。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..console_api.auth import ConsoleUser, require_console_user
from ..metering.db import get_db
from .contract import ArtifactContractError, validate_artifact
from .models import (
    MATTER_ARCHIVED,
    MATTER_OPEN,
    REF_RELATIONS,
    Matter,
    MatterArtifact,
    MatterRef,
    utcnow,
)

router = APIRouter(prefix="/api/console/matter", tags=["matter"])


def _new_id() -> str:
    return uuid.uuid4().hex


class MatterCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    cause_code: str | None = Field(default=None, max_length=64)


class ArtifactIn(BaseModel):
    kind: str
    producer: dict[str, Any]
    refs: list[str]
    sha256: str
    payload: dict[str, Any]
    provenance: dict[str, Any] | None = None
    title: str | None = None
    #: 引用关系，缺省 cites
    relation: str = "cites"


def _own_matter(db: Session, matter_id: str, user: ConsoleUser) -> Matter:
    """取本租户案件；不存在与不属于本租户同一语义（404）。"""
    matter = db.get(Matter, matter_id)
    if matter is None or matter.customer_identity != user.identity:
        raise HTTPException(404, "案件不存在")
    return matter


@router.post("")
def create_matter(
    body: MatterCreateIn,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db),
):
    matter = Matter(
        id=_new_id(),
        customer_identity=user.identity,
        customer_principal=user.email or user.username,
        title=body.title,
        cause_code=body.cause_code,
        status=MATTER_OPEN,
    )
    db.add(matter)
    db.flush()
    return matter.as_dict()


@router.get("")
def list_matters(
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Matter)
        .where(Matter.customer_identity == user.identity)
        .order_by(Matter.created_at.desc())
        .limit(200)
    ).all()
    return [m.as_dict() for m in rows]


@router.get("/{matter_id}")
def get_matter(
    matter_id: str,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db),
):
    matter = _own_matter(db, matter_id, user)
    artifacts = db.scalars(
        select(MatterArtifact)
        .where(MatterArtifact.matter_id == matter.id)
        .order_by(MatterArtifact.created_at.asc())
    ).all()
    return {**matter.as_dict(), "artifacts": [a.as_dict() for a in artifacts]}


@router.post("/{matter_id}/artifact")
def add_artifact(
    matter_id: str,
    body: ArtifactIn,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db),
):
    matter = _own_matter(db, matter_id, user)
    if matter.status == MATTER_ARCHIVED:
        raise HTTPException(409, "案件已归档，禁止新增产物")
    if body.relation not in REF_RELATIONS:
        raise HTTPException(422, f"relation: 必须是 {sorted(REF_RELATIONS)} 之一")

    try:
        normalized = validate_artifact(body.model_dump())
    except ArtifactContractError as exc:
        # 契约拒绝：指名缺失/非法字段，不落库
        raise HTTPException(422, f"{exc.field}: {exc.detail}") from exc

    artifact = MatterArtifact(
        id=_new_id(),
        matter_id=matter.id,
        kind=normalized["kind"],
        sha256=normalized["sha256"],
        title=normalized["title"],
        producer_line=normalized["producer"]["line"],
        producer_server=normalized["producer"]["server"],
        producer_tool=normalized["producer"]["tool"],
        producer_version=normalized["producer"]["version"],
        provenance_json=normalized["provenance_json"],
        payload_json=normalized["payload_json"],
        storage_path=body.payload.get("storage_path"),
    )
    db.add(artifact)
    db.flush()

    # 证据链边：即便目标不存在也落边（查询时显式标断链，不静默丢弃）
    for ref_id in normalized["refs"]:
        db.add(
            MatterRef(
                matter_id=matter.id,
                artifact_id=artifact.id,
                ref_artifact_id=ref_id,
                relation=body.relation,
            )
        )
    matter.updated_at = utcnow()
    db.flush()
    return artifact.as_dict()


@router.get("/{matter_id}/artifact")
def list_artifacts(
    matter_id: str,
    kind: str | None = None,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db),
):
    matter = _own_matter(db, matter_id, user)
    stmt = select(MatterArtifact).where(MatterArtifact.matter_id == matter.id)
    if kind:
        stmt = stmt.where(MatterArtifact.kind == kind)
    rows = db.scalars(stmt.order_by(MatterArtifact.created_at.asc())).all()
    return [a.as_dict() for a in rows]


@router.get("/{matter_id}/cost")
def matter_cost(
    matter_id: str,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db),
):
    """案件级用量合计（spec lex-billing-metering「案件维度计量」）。

    纯查询侧聚合：不改写入路径、不改既有对账三方口径。
    合计 = 该 matter_id 下计量记录按单位求和；无记录的客户看到全零而非报错。
    """
    from sqlalchemy import func

    from ..metering.models import MeteringRecord

    matter = _own_matter(db, matter_id, user)
    rows = db.execute(
        select(MeteringRecord.unit, func.count(), func.sum(MeteringRecord.quantity))
        .where(MeteringRecord.matter_id == matter.id)
        .group_by(MeteringRecord.unit)
    ).all()
    by_unit = {
        unit: {"records": int(count), "quantity": int(quantity or 0)}
        for unit, count, quantity in rows
    }
    return {
        "matter_id": matter.id,
        "by_unit": by_unit,
        "total_records": sum(v["records"] for v in by_unit.values()),
        "total_quantity": sum(v["quantity"] for v in by_unit.values()),
    }


@router.get("/{matter_id}/graph")
def artifact_graph(
    matter_id: str,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db),
):
    """证据链：每张纸引用了谁、被谁引用；目标缺失者显式标 `broken`。"""
    matter = _own_matter(db, matter_id, user)
    artifacts = {
        a.id: a
        for a in db.scalars(
            select(MatterArtifact).where(MatterArtifact.matter_id == matter.id)
        ).all()
    }
    edges = db.scalars(select(MatterRef).where(MatterRef.matter_id == matter.id)).all()

    nodes = [
        {
            "id": a.id,
            "kind": a.kind,
            "sha256": a.sha256,
            "title": a.title,
            "provenance": json.loads(a.provenance_json) if a.provenance_json else None,
            "broken": False,
        }
        for a in artifacts.values()
    ]
    broken_ids: set[str] = set()
    out_edges = []
    for edge in edges:
        target_missing = edge.ref_artifact_id not in artifacts
        if target_missing:
            broken_ids.add(edge.artifact_id)
        out_edges.append(
            {
                "artifact_id": edge.artifact_id,
                "ref_artifact_id": edge.ref_artifact_id,
                "relation": edge.relation,
                "broken": target_missing,
            }
        )
    for node in nodes:
        node["broken"] = node["id"] in broken_ids
    return {"matter": matter.as_dict(), "nodes": nodes, "edges": out_edges}

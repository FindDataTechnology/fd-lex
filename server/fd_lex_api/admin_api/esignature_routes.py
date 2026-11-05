"""电子签机器通道（/api/admin/esignature/*，change add-esignature-wire-actions 任务组 1）。

面向柏讯动作闸的四操作（spec `wire-esignature-actions`「四动作注册与风险分级」），
全部挂既有 ``require_admin``（X-Admin-Token 与 INTERNAL_API_TOKEN 严格相等 /
Logto 管理员角色；未配置 fail-closed 403，不假成功）。

复用 esignature.service 的状态机/闸门/出证聚合，不开第二套校验；租户限定沿用
``get_task`` 的 customer_identity 对账（他租户与不存在一律 404，不泄露存在性）。

红线（spec「不代签机器红线」，routes.py 注释同款）：
- 本 router 路由全集只含下述四操作；签署人面（/p/、/s/、realname、intent、sign）
  在本前缀不可达，签署落签只存在于签署人 H5 面（链接 token+短会话 token）。
- 发起响应**不回显签署链接**（链接明文只在控制台面回显一次）；催签/归档响应同款。
- 机器凭证（X-Admin-Token）对签署人面无意义（签署人面只认 x-esign-session 头）。
"""

from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..billing.deps import get_db_session
from ..esignature import adapter as adapter_mod
from ..esignature import gate, service
from ..esignature.routes import SignerIn
from .auth import require_admin

router = APIRouter(
    prefix="/api/admin/esignature",
    tags=["esignature-admin"],
    dependencies=[Depends(require_admin)],
)


class AdminInitiateIn(BaseModel):
    customer_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: str = "other"
    file_name: str = Field(min_length=1)
    file_b64: str = Field(min_length=1)
    signers: list[SignerIn] = Field(min_length=1)
    acknowledged: bool = False


class AdminTaskRefIn(BaseModel):
    customer_id: str = Field(min_length=1)


def _err(exc: service.EsignError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


def _task(db: Session, task_id: str, customer_id: str):
    try:
        return service.get_task(db, task_id, customer_id)
    except service.EsignError as exc:
        raise _err(exc) from exc


def _decode_b64(raw: str) -> bytes:
    try:
        return base64.b64decode(raw.encode("ascii", "ignore"), validate=True)
    except Exception:
        raise HTTPException(400, "file_b64 不是合法 base64")


def _precheck_gate(category: str, title: str, acknowledged: bool) -> None:
    """发起前闸门预检（与 service.initiate 调用同一 gate.evaluate，校验同源）。

    预检拒绝发生在建档之前——任务零残留；notice 级必须显式 acknowledged
    （该 acknowledged 由柏讯确认面的人类确认产生，机器不代决）。
    """
    verdict = gate.evaluate(category, title)
    if verdict["verdict"] == "block":
        raise HTTPException(422, json.dumps(verdict, ensure_ascii=False))
    if verdict["verdict"] == "notice" and not acknowledged:
        raise HTTPException(
            400, json.dumps({"require_ack": True, **verdict}, ensure_ascii=False)
        )


@router.post("/tasks")
def initiate_task(body: AdminInitiateIn, db: Session = Depends(get_db_session)):
    """原子建档+发起（wire ``esignature.initiate`` 的执行体）。

    闸门预检先于建档：阻断/未确认提示 → 422/400 且零残留；通过后
    create+initiate 仍走原 service 全链（闸门内再校验、adapter fail-closed），
    任一步异常由 get_db 回滚，不落半写。
    """
    _precheck_gate(body.category, body.title, body.acknowledged)
    data = _decode_b64(body.file_b64)
    try:
        task = service.create_task(
            db,
            customer_identity=body.customer_id,
            customer_principal=None,
            title=body.title,
            category=body.category,
            file_name=body.file_name,
            file_bytes=data,
            signers=[s.model_dump() for s in body.signers],
        )
        initiated = service.initiate(db, task, acknowledged=body.acknowledged)
    except service.EsignError as exc:
        raise _err(exc) from exc
    except adapter_mod.ProviderNotConfigured as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except adapter_mod.AdapterError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    # 只回任务视图：签署链接明文不进机器通道（本模块头部红线）。
    return initiated["task"]


@router.get("/tasks/{task_id}")
def task_status(
    task_id: str,
    customer_id: str = Query(min_length=1),
    db: Session = Depends(get_db_session),
):
    """查状态（wire 只读工具 ``esignature_status`` 的数据面）。"""
    return service.task_detail(db, _task(db, task_id, customer_id))


@router.post("/tasks/{task_id}/nudge")
def nudge(task_id: str, body: AdminTaskRefIn, db: Session = Depends(get_db_session)):
    """催签（台账事件留痕，不外发链接）。"""
    task = _task(db, task_id, body.customer_id)
    try:
        return service.nudge(db, task)
    except service.EsignError as exc:
        raise _err(exc) from exc


@router.post("/tasks/{task_id}/archive")
def archive(task_id: str, body: AdminTaskRefIn, db: Session = Depends(get_db_session)):
    """归档：已完成任务幂等确保出证包并返回证据摘要。"""
    task = _task(db, task_id, body.customer_id)
    try:
        return service.archive(db, task)
    except service.EsignError as exc:
        raise _err(exc) from exc


__all__ = ["router"]

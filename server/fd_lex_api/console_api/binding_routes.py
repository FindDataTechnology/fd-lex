"""本人确认绑定（change converge-line-identity-and-keys 4.3）。

管理员可以替组织成员**预绑定**（一次性建档，见 ``POST /api/admin/customers/{id}/identities/bulk``），
但预绑定行未经本人确认前，平台不得代他做事。本模块就是"确认"这件事**真的存在**
的地方——否则兑换端点那句"请本人确认一次"又是一句无处可去的话。

三条纪律：

  1. **身份取自登录态，不取自请求体**。请求体里没有 ``identity`` 字段，也不接受
     ``customer_id``：能替谁确认就等于能替谁接账。
  2. **确认不建绑定**。没有绑定行就 404——绑定只能由管理员/运营建档，本模块
     绝不因为有人登录就顺手造一个（那就把"永不自动创建身份"从后门放回来了）。
  3. **只确认自己那一行**。身份唯一索引保证一身份一客户；这里再按 identity
     过滤一次，是防止将来有人按 id 传参时把范围写宽。

确认是**幂等**的：已确认再调一次仍返回 200 与同一份状态（前端重试、用户手滑
都不该报错），但不会刷新 ``confirmed_at``——那个时间戳是审计事实，不是心跳。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..billing.deps import get_db_session
from .auth import ConsoleUser, require_console_user
from .identity import binding_for
from .models import ConsoleIdentityBinding

router = APIRouter(prefix="/api/console/binding", tags=["console"])

#: 与兑换端点同名的原因码
REASON_BINDING_REQUIRED = "binding_required"


def _state(row: ConsoleIdentityBinding | None, identity: str) -> dict[str, Any]:
    if row is None:
        return {"binding": None, "identity": identity}
    return {"binding": row.as_dict(), "identity": identity}


@router.get("")
def my_binding(
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """当前登录身份自己的绑定状态；没有绑定返回 ``binding: null``（不是 404）。

    用 200 + null 而不是 404：这条路由回答的是"我是什么状态"，"没有绑定"是
    一个合法状态，不该与"路由不存在"同形。
    """
    return _state(binding_for(db, user.identity), user.identity)


@router.post("/confirm")
def confirm_my_binding(
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """把**我自己**那行预绑定标记为已确认（幂等；从不建行）。"""
    row = binding_for(db, user.identity)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "reason": REASON_BINDING_REQUIRED,
                "detail": f"identity {user.identity!r} has no binding to confirm",
                "hint": "绑定由管理员/运营建档后才能确认，本接口不会代为创建",
            },
        )
    if not row.is_confirmed:
        row.confirmed_at = datetime.now(timezone.utc)
        row.confirmed_via = "self"
        db.flush()
    return {"binding": row.as_dict(), "identity": user.identity, "confirmed": True}

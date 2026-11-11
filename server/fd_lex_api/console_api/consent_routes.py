"""平台代做的授权入口（change converge-line-identity-and-keys 2.2）。

兑换端点（``POST /api/auth/exchange``）在 ``consent`` 档缺授权行时会拒绝，并在
响应里指名「去这里授权」。本模块就是那个地方**真的存在**的地方——否则"给出
授权入口"只是一句话，调用方拿着一个 403 无处可去。

三条纪律：

  1. **客户身份取自登录身份，不取自请求体**。请求体能带 ``platform_subject``
     和 ``scope``，绝不允许带 ``customer_id``——那等于让任何登录用户替别人授权。
  2. **未绑定 = 不能授权**。绑定回答"你是哪个客户"，缺了它授权无处可挂。
     这里不建绑定，也不猜。
  3. **变更留痕**。改 scope 不是原地改字段，而是"撤旧行 + 记新行"：审计要看
     的是"曾经授权过什么、什么时候收回去的"，原地覆盖把这件事实抹掉。

只信任本线配置的那一个平台主体（``PLATFORM_ASSERTION_ISSUER``）：没配 = 503
（本线还不知道该信谁，记下一条授权毫无意义），配了但对不上 = 400。放开它等于
允许客户把自己的权限授给任意自称平台的地址。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..billing.deps import get_db_session
from ..billing.models import Customer
from ..config import get_settings
from .auth import ConsoleUser, require_console_user
from .delegation import grant_consent, revoke_consent
from .identity import resolve_console_customer
from .models import ConsolePlatformConsent

router = APIRouter(prefix="/api/console/consent", tags=["console"])

#: 与兑换端点同名的原因码：调用方拿到 403 后按它决定跳哪一面。
REASON_BINDING_REQUIRED = "binding_required"
REASON_PLATFORM_NOT_CONFIGURED = "platform_not_configured"
REASON_UNKNOWN_PLATFORM = "unknown_platform"
REASON_SCOPE_EMPTY = "scope_empty"
REASON_ALREADY_REVOKED = "already_revoked"


class ConsentBody(BaseModel):
    """授权请求体。**没有** ``customer_id`` 字段——见模块头纪律 1。"""

    platform_subject: str = Field(min_length=1, max_length=255)
    scope: str = Field(min_length=1, max_length=512)


def _row_dict(row: ConsolePlatformConsent) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform_subject": row.platform_subject,
        "scope": str(row.scope or "").split(),
        "granted_by": row.granted_by,
        "granted_at": row.granted_at.isoformat() if row.granted_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


def _customer_or_403(db: Session, user: ConsoleUser) -> Customer:
    customer = resolve_console_customer(db, user.identity)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason": REASON_BINDING_REQUIRED,
                "detail": f"identity {user.identity!r} is not bound to any customer",
            },
        )
    return customer


def _trusted_subject() -> str:
    """本线被配置去信任的平台主体；未配置即 503（不猜、不放行）。"""
    expected = (get_settings().platform_assertion_issuer or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "reason": REASON_PLATFORM_NOT_CONFIGURED,
                "detail": "PLATFORM_ASSERTION_ISSUER 未配置：本线尚不知道该信任哪个平台主体",
            },
        )
    return expected


def _normalize_scope(raw: str) -> str:
    """空格分隔、去重、保持首次出现顺序；空集合直接拒（授权空 scope 无意义）。"""
    seen: list[str] = []
    for token in str(raw or "").split():
        if token not in seen:
            seen.append(token)
    if not seen:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"reason": REASON_SCOPE_EMPTY, "detail": "scope 不能为空"},
        )
    return " ".join(seen)


def _active(db: Session, customer_id: int, platform_subject: str) -> ConsolePlatformConsent | None:
    return db.execute(
        select(ConsolePlatformConsent)
        .where(ConsolePlatformConsent.customer_id == customer_id)
        .where(ConsolePlatformConsent.platform_subject == platform_subject)
        .where(ConsolePlatformConsent.revoked_at.is_(None))
        .order_by(ConsolePlatformConsent.id.desc())
    ).scalars().first()


@router.get("")
def list_consents(
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """本客户当前生效的平台授权（只读；未绑定给空列表 + ``bound: false``）。

    读不需要"本线信任哪个平台"这个前提，所以不套 ``_trusted_subject``。
    """
    customer = resolve_console_customer(db, user.identity)
    if customer is None:
        return {"bound": False, "customer_id": None, "consents": []}
    rows = db.execute(
        select(ConsolePlatformConsent)
        .where(ConsolePlatformConsent.customer_id == customer.id)
        .where(ConsolePlatformConsent.revoked_at.is_(None))
        .order_by(ConsolePlatformConsent.id.desc())
    ).scalars().all()
    return {"bound": True, "customer_id": customer.id, "consents": [_row_dict(r) for r in rows]}


@router.post("", status_code=status.HTTP_200_OK)
def create_consent(
    body: ConsentBody,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """记一条授权。同一 (客户, 平台主体) 只保留一条生效行。"""
    expected = _trusted_subject()
    subject = body.platform_subject.strip()
    if subject != expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": REASON_UNKNOWN_PLATFORM,
                # 不回显"应该填什么"以外的东西；这个值本来就是公开配置。
                "detail": f"本线只信任 {expected!r}，不收 {subject!r} 的授权",
            },
        )
    customer = _customer_or_403(db, user)
    scope = _normalize_scope(body.scope)

    existing = _active(db, customer.id, subject)
    if existing is not None:
        if set(str(existing.scope or "").split()) == set(scope.split()):
            # 幂等：同样的授权再来一次不新增行，也不动时间戳。
            return {"created": False, "consent": _row_dict(existing)}
        # 变更留痕：先撤旧行再记新行，不原地覆盖。
        revoke_consent(db, existing.id)

    row = grant_consent(
        db,
        customer_id=customer.id,
        platform_subject=subject,
        scope=scope,
        granted_by=user.email or user.identity,
    )
    return {"created": True, "consent": _row_dict(row)}


@router.delete("/{consent_id}")
def delete_consent(
    consent_id: int,
    user: ConsoleUser = Depends(require_console_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """撤销自己的一条授权（留痕，不删行）。别人的行按"不存在"处理。"""
    customer = resolve_console_customer(db, user.identity)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reason": REASON_BINDING_REQUIRED, "detail": "未绑定客户"},
        )
    row = db.get(ConsolePlatformConsent, consent_id)
    if row is None or row.customer_id != customer.id:
        # 不区分"不存在"和"不是你的"：区分了就是在探测别人的授权。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="consent not found")
    if row.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"reason": REASON_ALREADY_REVOKED, "detail": f"consent {consent_id} 已撤销"},
        )
    revoke_consent(db, row.id)
    db.refresh(row)
    return {"revoked": True, "consent": _row_dict(row)}

"""平台代做的授权判定（change converge-line-identity-and-keys 2.2）。

断言验签通过只回答"这份断言是真的"，不回答"这个人/这个平台被允许在这条线
做什么"。本模块补后半个问题，三档：

  off         一律拒（默认）。连"这个人登记过没有"都不问。
  first-use   必须先有身份绑定（``console_identity_bindings``）。**永不自动创建**：
              没有绑定就返回 ``binding_required``，不猜、不建。
  consent     除绑定外还要一条**未撤销**的授权行，且授予的 scope 不得被断言放大
              （取交集）。

为什么不在这一步"顺手把绑定建了"：绑定等于把某个 Logto 身份与一个 billing
客户划等号，做错了就是把 A 的账单和权限挂到 B 身上。自动化它买不到任何东西，
只买到一个静默越权的入口。真正的"本人确认"需要一个平台伪造不了的通道（例如
用户自己在 lex 控制台完成一次 OIDC 登录），那需要一个单独的界面/流程，不在
本模块里假装已经存在。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..billing.models import Customer
from .identity import binding_for, resolve_console_customer
from .models import ConsolePlatformConsent

__all__ = [
    "DelegationDecision",
    "DelegationDenied",
    "active_consent",
    "authorize_delegation",
    "grant_consent",
]

#: 该身份没有任何绑定行
BINDING_REQUIRED = "binding_required"
#: 有绑定行，但本人尚未确认（管理员预绑定，4.3）
CONFIRMATION_REQUIRED = "confirmation_required"


class DelegationDenied(Exception):
    """授权判定失败；``reason`` 是稳定码（调用方与测试断言它，不断言文案）。"""

    JIT_DISABLED = "jit_disabled"
    BINDING_REQUIRED = BINDING_REQUIRED
    CONFIRMATION_REQUIRED = CONFIRMATION_REQUIRED
    CONSENT_REQUIRED = "consent_required"
    SCOPE_EMPTY = "scope_empty"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class DelegationDecision:
    customer_id: int
    #: 生效 scope = 断言 scope ∩ 授权 scope（first-use 档用断言 scope）
    scope: tuple[str, ...]
    consent_id: int | None


def active_consent(db: Session, customer_id: int, platform_subject: str) -> ConsolePlatformConsent | None:
    """该（客户, 平台主体）当前生效的授权行；无则 None。"""
    return db.execute(
        select(ConsolePlatformConsent)
        .where(ConsolePlatformConsent.customer_id == customer_id)
        .where(ConsolePlatformConsent.platform_subject == platform_subject)
        .where(ConsolePlatformConsent.revoked_at.is_(None))
        .order_by(ConsolePlatformConsent.id.desc())
    ).scalars().first()


def authorize_delegation(
    db: Session,
    *,
    subject: str,
    platform_subject: str,
    requested_scope: tuple[str, ...],
    jit_policy: str,
) -> DelegationDecision:
    """按策略判定这次代做是否成立。失败一律抛 :class:`DelegationDenied`。"""
    if jit_policy == "off":
        raise DelegationDenied(DelegationDenied.JIT_DISABLED, "jit_policy=off")

    # 确认这一步在这里把住（4.3）：管理员可以替组织预绑定，但"管理员说这是你"
    # 不等于"这是你"——预绑定行未经本人确认前，平台不得代他做事。
    binding = binding_for(db, subject)
    if binding is not None and not binding.is_confirmed:
        raise DelegationDenied(
            DelegationDenied.CONFIRMATION_REQUIRED,
            f"identity {subject!r} is pre-bound to customer {binding.customer_id} "
            "but has not confirmed yet; the member must confirm once",
        )

    customer: Customer | None = resolve_console_customer(db, subject, require_confirmed=True)
    if customer is None:
        # 铁律：身份永不自动创建。这里只回答"没有绑定"，不去建。
        raise DelegationDenied(
            DelegationDenied.BINDING_REQUIRED,
            f"identity {subject!r} is not bound to any customer; bind it first",
        )

    if jit_policy == "first-use":
        return DelegationDecision(
            customer_id=customer.id, scope=tuple(requested_scope), consent_id=None
        )

    if jit_policy != "consent":
        raise DelegationDenied(DelegationDenied.JIT_DISABLED, f"unknown jit_policy {jit_policy!r}")

    consent = active_consent(db, customer.id, platform_subject)
    if consent is None:
        raise DelegationDenied(
            DelegationDenied.CONSENT_REQUIRED,
            f"no active consent row for {platform_subject!r} on customer {customer.id}",
        )
    granted = set(str(consent.scope or "").split())
    effective = tuple(s for s in requested_scope if s in granted)
    if requested_scope and not effective:
        # 授权行存在但一条都没覆盖：不是"放行但少点权"，是这次请求越界。
        raise DelegationDenied(
            DelegationDenied.CONSENT_REQUIRED,
            f"consent {consent.id} grants none of {sorted(requested_scope)}",
        )
    return DelegationDecision(customer_id=customer.id, scope=effective, consent_id=consent.id)


def grant_consent(
    db: Session,
    *,
    customer_id: int,
    platform_subject: str,
    scope: str,
    granted_by: str | None = None,
) -> ConsolePlatformConsent:
    """写一条授权行（调用方负责鉴权：只有该客户的 owner 能替自己授权）。"""
    row = ConsolePlatformConsent(
        customer_id=customer_id,
        platform_subject=platform_subject,
        scope=scope,
        granted_by=granted_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def revoke_consent(db: Session, consent_id: int) -> bool:
    """撤销：留痕而不是删行（审计要看"曾经授权过"）。"""
    from datetime import datetime, timezone

    row = db.get(ConsolePlatformConsent, consent_id)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return True

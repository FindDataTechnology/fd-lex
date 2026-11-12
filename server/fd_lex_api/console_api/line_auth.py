"""门 K 顾客轨接入控制台/工具面（change converge-line-identity-and-keys 8.2）。

工具面（`lex-ops-mcp`）只把进站 Bearer 原样转发给本服务，所以「词元客户轨」的
落点在这里：同一个端点先按 Logto ID token 认人，认不下来再按**门 K 换发令牌**
（网关用客户的词元 `sk-` 换来的、`aud`/`source` 都等于本线的那枚 JWT）认客户。

三条刻意的边界：

1. **未配置 = 这条路不存在**。`line_token_public_key_pem` / `line_token_jwks_uri`
   都空时，`require_console_or_line()` 的行为与改动前**逐字节一致**：只认 Logto，
   错凭据回原来那条 401。既有 Logto 与 lex PAT 两条路一个字节不变（8.2 的
   「保留 lex PAT」）。
2. **顾客轨只到客户级**。门 K 令牌表达的是「这个客户可以打这条线」，不表达成员
   身份。所以它不能用来铸造长命凭据（`ensure_credential_mint_allowed`）：铸造是
   管理动作，必须 `line:admin` **且**有一条生效的 consent 行——这正是 8.2 验收
   里「`lex_credential_mint` 仅对带 `line:admin` 与 consent 行的服务主体开放」。
3. **身份永不自动创建**。令牌验过但解析不到客户 → 403 `unknown_identity`，
   不去建行（与 `authorize_delegation` 同一条铁律）。

映射口径：令牌里的 `email` 先按绑定表解析（`console_identity_bindings`，最准），
再退回 `sub`（网关填的是 sub2api 用户 id，命中 `customers.sub2api_user_id`）。
两者都不命中才是 `unknown_identity`。**这里要求绑定已确认吗：不要求**——这是客户
本人拿自己的钥匙读自己的东西，不是平台代做；4.3 的确认门只把代做那条路挡住
（见 `delegation.authorize_delegation`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..billing.deps import get_db_session
from ..billing.models import Customer
from ..config import get_settings
from ..line_token import LineTokenIdentity, LineTokenRejected, LineTokenSettings, verify_line_token
from .auth import ConsoleUser, _bearer, verify_id_token
from .delegation import active_consent
from .identity import resolve_console_customer

__all__ = [
    "LINE_CALLER_CLAIM",
    "ensure_credential_admin_allowed",
    "is_line_caller",
    "line_caller_customer",
    "line_settings_from_config",
    "require_console_or_line",
]

#: 在 ``ConsoleUser.claims`` 里标记「这次身份来自门 K 令牌」。
LINE_CALLER_CLAIM = "line_token"

#: 铸造凭据要求的管理 scope（与 manifest 的 scopes 枚举一致）。
ADMIN_SCOPE = "line:admin"

#: 稳定原因码（端点据此给 403，不靠文案）
REASON_UNKNOWN_IDENTITY = "unknown_identity"
REASON_ADMIN_SCOPE_REQUIRED = "admin_scope_required"
REASON_CONSENT_REQUIRED = "consent_required"


def line_settings_from_config() -> LineTokenSettings:
    """配置 → 校验参数。未配置时 ``configured`` 为假（这条路自动关闭）。"""
    s = get_settings()
    return LineTokenSettings(
        issuer=s.line_token_issuer,
        line=s.line_token_line or "lex",
        public_key_pem=s.line_token_public_key_pem,
        jwks_uri=s.line_token_jwks_uri,
        max_ttl_seconds=s.line_token_max_ttl_seconds,
    )


def is_line_caller(user: ConsoleUser) -> bool:
    return bool(user.claims.get(LINE_CALLER_CLAIM))


def _as_console_user(
    identity: LineTokenIdentity, customer: Customer, matched: str
) -> ConsoleUser:
    """门 K 身份 → ``ConsoleUser``（控制台各端点继续按同一个类型工作）。

    ``identity`` 必须填**当初命中客户的那个值**（email 或 sub2api 用户 id），不能
    一律填 email：端点内部还会用 ``user.identity`` 再解析一次客户，填错就等于
    解析不到（2026-11-12 真接缝用例撞出：email 无绑定、靠 sub2api_user_id 才
    命中，结果 /me 返回 customer=None）。
    """
    return ConsoleUser(
        identity=matched,
        username=None,
        email=identity.email,
        sub=identity.subject,
        roles=[],
        organizations=[],
        claims={
            LINE_CALLER_CLAIM: True,
            "scope": " ".join(identity.scope),
            "source": identity.source,
            "platform_subject": identity.subject,
            "customer_id": customer.id,
            "expires_at": identity.expires_at,
            "jti": identity.jti,
        },
    )


def line_caller_customer(
    db: Session, identity: LineTokenIdentity
) -> tuple[Customer, str] | None:
    """令牌身份 → (本线客户, 命中的身份值)；email 先走绑定表，再走 sub2api 用户 id。

    返回命中的那个值是为了让调用方把它当作 ``ConsoleUser.identity``——端点内部
    会用它再解析一次客户，口径必须一致。
    """
    if identity.email:
        customer = resolve_console_customer(db, identity.email)
        if customer is not None:
            return customer, identity.email
    customer = resolve_console_customer(db, identity.subject)
    if customer is not None:
        return customer, identity.subject
    return None


async def require_console_or_line(
    request: Request, db: Session = Depends(get_db_session)
) -> ConsoleUser:
    """先 Logto，再门 K。两条都失败时抛 **Logto 那条原始 401**。

    为什么保留原错误：开关关闭/未配置时，调用方看到的必须和改动前完全一样
    （同一状态码、同一 detail）；否则"加了一条新轨"这件事会以错误文案的形状
    漏给所有老客户端。
    """
    try:
        return verify_id_token(_bearer(request))
    except HTTPException as logto_error:
        settings = line_settings_from_config()
        if not settings.configured:
            raise logto_error from None
        try:
            identity = verify_line_token(_bearer(request), settings=settings)
        except (LineTokenRejected, HTTPException):
            raise logto_error from None
        resolved = line_caller_customer(db, identity)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "reason": REASON_UNKNOWN_IDENTITY,
                    "detail": "this line token resolves to no customer on this line",
                },
            )
        customer, matched = resolved
        return _as_console_user(identity, customer, matched)


def ensure_credential_admin_allowed(db: Session, user: ConsoleUser) -> None:
    """铸造/吊销凭据的闸：门 K 顾客轨默认不可做，除非 line:admin + 生效 consent 行。

    人（Logto）这条路不变——客户在自己控制台里铸自己的 PAT 是既有能力，上游
    registry 仍按 owner-only 判定。这里只拦「换发令牌」那条路：那枚票是**客户级**
    凭据，用它铸一把更长命的钥匙等于把短命约束绕开。
    """
    if not is_line_caller(user):
        return

    scope = set(str(user.claims.get("scope") or "").split())
    if ADMIN_SCOPE not in scope:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason": REASON_ADMIN_SCOPE_REQUIRED,
                "detail": f"minting a credential requires the {ADMIN_SCOPE} scope on the line token",
            },
        )

    customer_id = user.claims.get("customer_id")
    platform_subject = str(user.claims.get("platform_subject") or "")
    consent = active_consent(db, int(customer_id), platform_subject) if customer_id else None
    if consent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason": REASON_CONSENT_REQUIRED,
                "detail": (
                    "minting on a customer's behalf requires an active consent row "
                    f"for {platform_subject!r}"
                ),
            },
        )
    # 授权行存在但没覆盖 line:admin = 这次越界（与 delegation 的判定同口径）。
    if ADMIN_SCOPE not in set(str(consent.scope or "").split()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "reason": REASON_CONSENT_REQUIRED,
                "detail": f"consent {consent.id} does not grant {ADMIN_SCOPE}",
            },
        )

"""断言兑换端点（change converge-line-identity-and-keys 2.1）。

``POST /api/auth/exchange``：壹座带着一份成员级断言来问"我是谁、能做什么、
到什么时候"。返回的是**身份文档**，不是另一枚本线 token——另铸一枚等于长出一条
本线自己的续期链，那正是本 change 要消掉的东西。

为什么放在本线 API 而不是 Logto：Logto 不支持 RFC 8693（注册处台账已证
``obo_exchange`` 不支持 Logto），而"谁信任谁"本来就该由被访问的线决定。

未配置时 fail-closed（503 "not configured"），绝不"没配就放行"。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from .assertion import AssertionRejected, AssertionSettings, JitPolicy, verify_assertion
from .config import get_settings
from .console_api.delegation import DelegationDenied, authorize_delegation
from .console_api.replay import DbReplayStore

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: 稳定原因码 → HTTP 状态。拒绝的**原因**必须可区分，否则调用方只能瞎猜。
_STATUS = {
    AssertionRejected.NOT_CONFIGURED: status.HTTP_503_SERVICE_UNAVAILABLE,
    AssertionRejected.TOKEN_MALFORMED: status.HTTP_400_BAD_REQUEST,
    AssertionRejected.BAD_SIGNATURE: status.HTTP_401_UNAUTHORIZED,
    AssertionRejected.WRONG_ISSUER: status.HTTP_401_UNAUTHORIZED,
    AssertionRejected.WRONG_AUDIENCE: status.HTTP_401_UNAUTHORIZED,
    AssertionRejected.EXPIRED: status.HTTP_401_UNAUTHORIZED,
    AssertionRejected.TTL_TOO_LONG: status.HTTP_401_UNAUTHORIZED,
    AssertionRejected.MISSING_CLAIM: status.HTTP_400_BAD_REQUEST,
    AssertionRejected.REPLAYED: status.HTTP_409_CONFLICT,
    AssertionRejected.JIT_DISABLED: status.HTTP_403_FORBIDDEN,
    AssertionRejected.UNKNOWN_IDENTITY: status.HTTP_403_FORBIDDEN,
    AssertionRejected.CONSENT_REQUIRED: status.HTTP_403_FORBIDDEN,
}


def _session():
    """console 面的库会话（console_* 表与 billing 同库）。

    用 ``metering.db.get_session``——与 ``/api/console/*`` 走的是同一个会话工厂。
    这里曾写成 ``get_sessionmaker()()``（该名字在 metering.db 里并不存在）：
    单测只覆盖了"未配置 503"与"缺 bearer 400"两条，都在这一行之前就返回，
    于是真机一调就 ImportError 500。见 tests 里那条端到端用例。
    """
    from .metering.db import get_session

    return get_session()


def assertion_settings() -> AssertionSettings:
    """从运行时配置读断言校验参数（未配置即 fail-closed）。"""
    s = get_settings()
    return AssertionSettings(
        issuer=(s.platform_assertion_issuer or "").strip(),
        audience=(s.platform_assertion_audience or "").strip(),
        jwks_uri=(s.platform_assertion_jwks_uri or "").strip(),
        public_key_pem=(s.platform_assertion_public_key_pem or "").strip(),
        max_ttl_seconds=s.platform_assertion_max_ttl_seconds,
        jit_policy=(s.platform_assertion_jit_policy or JitPolicy.OFF).strip(),
    )


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


#: 拒绝时"下一步去哪"。只登记**真的有落点**的两个：
#: 写一个不存在的地址比不写更糟——调用方跳过去撞 404，会以为自己拿错了凭据。
#: 授权入口由 ``console_api/consent_routes.py`` 提供（POST 即记一条授权行）。
_ENTRY_KINDS = {
    DelegationDenied.CONSENT_REQUIRED: "consent",
    DelegationDenied.BINDING_REQUIRED: "identity-binding",
    DelegationDenied.CONFIRMATION_REQUIRED: "identity-confirmation",
}


def _entry_point(reason: str, ident: Any) -> dict[str, Any] | None:
    kind = _ENTRY_KINDS.get(reason)
    if kind is None:
        return None
    base = (get_settings().console_origin or "").strip().rstrip("/")
    if kind == "consent":
        return {
            "kind": "consent",
            "method": "POST",
            "url": f"{base}/api/console/consent",
            "requires": "以该客户本人身份登录控制台（Logto）",
            # 请求体形状直接给出：调用方不必猜，也不必先读一份文档。
            "request": {"platform_subject": ident.issuer, "scope": " ".join(ident.scope)},
            "actionable": True,
        }
    if kind == "identity-confirmation":
        # 与"没有绑定"必须分开指路：那个要等管理员建档，这个只要本人点一次。
        # 指错地方比不指更糟——用户按提示去"申请绑定"会被运营告知已经建好了。
        return {
            "kind": "identity-confirmation",
            "method": "POST",
            "url": f"{base}/api/console/binding/confirm",
            "requires": "以本人身份登录控制台（Logto）",
            # 无请求体：身份只取自登录态，这正是"本人确认"的意义。
            "request": None,
            "actionable": True,
        }
    # 绑定不是自助动作：把"你是谁"与一个计费客户划等号，做错了就是把 A 的账单
    # 和权限挂到 B 身上。这里只给一个能确认当前绑定状态的落点，并如实说不可自助。
    return {
        "kind": "identity-binding",
        "method": None,
        "url": f"{base}/api/console/me",
        "requires": "以本人身份登录控制台（Logto）",
        "actionable": False,
        "hint": "绑定需由管理员/运营为本人建档；登录后可用该地址确认绑定状态",
    }


@router.post("/exchange")
def exchange(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    settings = assertion_settings()
    token = _bearer(authorization)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing bearer assertion",
        )
    settings_db = None
    try:
        # jti 一次性必须在这里接上：`verify_assertion` 只在给了 replay 台账时才查
        # 重放，不传就等于这条验收（任务 2.1「jti 一次性」）只是文档里的一句话。
        # 台账落库（不是进程内），否则多副本间各自为政，重放换副本即可通过。
        settings_db = _session()
        ident = verify_assertion(
            token, settings=settings, replay=DbReplayStore(settings_db)
        )
    except AssertionRejected as exc:
        if settings_db is not None:
            settings_db.close()
        raise HTTPException(
            status_code=_STATUS.get(exc.reason, status.HTTP_401_UNAUTHORIZED),
            detail={"reason": exc.reason, "detail": exc.detail},
            headers={"WWW-Authenticate": "Bearer"} if exc.reason not in (
                AssertionRejected.NOT_CONFIGURED,
            ) else None,
        ) from exc
    # 验签只回答"这份断言是真的"；下面回答"这个平台被允许代这个人做什么"。
    try:
        decision = authorize_delegation(
            settings_db,
            subject=ident.sub,
            platform_subject=ident.issuer,
            requested_scope=ident.scope,
            jit_policy=settings.jit_policy,
        )
    except DelegationDenied as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
                if exc.reason != DelegationDenied.BINDING_REQUIRED
                else status.HTTP_409_CONFLICT
            ),
            detail={
                "reason": exc.reason,
                "detail": exc.detail,
                # 拒绝必须可执行：光说"缺授权"而不给地方授权，调用方只能干瞪眼。
                "entry_point": _entry_point(exc.reason, ident),
            },
        ) from exc
    finally:
        settings_db.close()

    return {
        "sub": ident.sub,
        "customer_id": decision.customer_id,
        # 生效 scope：consent 档下是"断言 ∩ 授权"，不放大。
        "scope": list(decision.scope),
        "consent_id": decision.consent_id,
        "issuer": ident.issuer,
        "audience": ident.audience,
        "expires_at": ident.expires_at,
    }

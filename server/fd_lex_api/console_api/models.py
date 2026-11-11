"""控制台/后台面自有表（与 billing_* 同库，但不改 billing 模块的表）。

两张表都刻意用普通 Integer 承载对 ``billing_customers.id`` 的软引用
（不建 FK 约束）：billing 表的元数据归 billing 模块所有，本模块的
``create_all`` 不与它耦合，迁移链收敛时也不会互相牵扯。

- ``console_identity_bindings``：Logto username ↔ billing Customer 的绑定
  （结算侧 v1 以 sub2api_user_id 承载计量身份键；本表补齐真实的 Logto
  身份映射，控制台按登录身份解析客户——未绑定时回落 sub2api_user_id 相等）。
- ``console_credential_events``：经 lex 控制台代理发生的凭据操作留痕
  （铸造/吊销的元数据；明文与哈希均不落库——registry 侧只存哈希）。
  仅覆盖经本控制台发生的操作；客户在 registry 控制台直铸的凭据不在本表
  （列表查询仍以 registry 实时接口为准，本表是审计视图的补充）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: 凭据操作动作（与 routes 写入口径一致）
CREDENTIAL_ACTION_MINT = "mint"
CREDENTIAL_ACTION_REVOKE = "revoke"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConsoleBase(DeclarativeBase):
    """控制台/后台面 ORM 基类（console_* 表）。"""


class ConsoleIdentityBinding(ConsoleBase):
    """Logto username ↔ billing Customer 绑定（一客户多身份；一身份一客户）。"""

    __tablename__ = "console_identity_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 软引用 billing_customers.id（不建 FK；billing 表归 billing 模块）
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: Logto 身份（username 优先，缺省回落 email/sub）
    identity: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "identity": self.identity,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ConsoleCredentialEvent(ConsoleBase):
    """凭据操作留痕（lex 控制台代理路径；明文/哈希不落库）。"""

    __tablename__ = "console_credential_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # mint|revoke
    key_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    key_name: Mapped[str | None] = mapped_column(String(128))
    key_prefix: Mapped[str | None] = mapped_column(String(32))
    username: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    groups_json: Mapped[str | None] = mapped_column(Text)
    #: 结果说明（如 registry 错误 detail），成功为 None
    detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "key_id": self.key_id,
            "key_name": self.key_name,
            "key_prefix": self.key_prefix,
            "username": self.username,
            "groups": _load_groups(self.groups_json),
            "detail": self.detail,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ConsolePlatformConsent(ConsoleBase):
    """平台服务主体代某客户的授权行（converge-line-identity-and-keys 2.2）。

    ``consent`` 档下，绑定存在还不够：平台要代该客户做事，必须有一条**显式、
    可审计、可撤销**的授权行。撤销用 ``revoked_at`` 留痕——删行会让"曾经授权过"
    这件事消失，而审计要看的正是这个。
    """

    __tablename__ = "console_platform_consents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 软引用 billing_customers.id（同 ConsoleIdentityBinding，不建 FK）
    customer_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    #: 被信任的平台服务主体（断言的 iss 或 iss+sub 组合）
    platform_subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: 授予的 scope（空格分隔；与断言 scope 取交集，不放大权限）
    scope: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    #: 谁会话/哪条链路授权了它（审计起点）
    granted_by: Mapped[str | None] = mapped_column(String(255))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "platform_subject": self.platform_subject,
            "scope": self.scope,
            "granted_by": self.granted_by,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "active": self.revoked_at is None,
        }


class ConsoleAssertionReplay(ConsoleBase):
    """平台断言的 jti 一次性台账（converge-line-identity-and-keys 2.1）。

    "jti 一次性"是兑换端点的验收原文。**落库而不是放进程内**：进程内台账在多副本
    部署下形同虚设——同一份断言打到另一个副本照样通过，而"重放"这件事恰恰是
    攻击者会主动挑副本的。唯一约束让"谁先写入谁赢"由数据库裁决，不靠应用层判等。

    行只活到断言自身的 ``exp``：过期的行不再有保护价值，清理时按 ``expires_at``
    删（见 ``replay.DbReplayStore.claim``）。
    """

    __tablename__ = "console_assertion_replays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: 断言的 jti（全局唯一：同一个 jti 只允许被认领一次）
    jti: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    #: 断言自身的到期时刻（epoch 秒）；过此即无效，可清理
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


def _load_groups(raw: str | None) -> list[str]:
    if not raw:
        return []
    import json

    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def ensure_console_schema(engine=None) -> None:
    """幂等建 console_* 表（dev 便利；生产迁移链由主代理收敛）。"""
    if engine is None:
        from ..metering.db import get_engine

        engine = get_engine()
    ConsoleBase.metadata.create_all(engine)


__all__ = [
    "CREDENTIAL_ACTION_MINT",
    "CREDENTIAL_ACTION_REVOKE",
    "ConsoleAssertionReplay",
    "ConsoleBase",
    "ConsoleCredentialEvent",
    "ConsoleIdentityBinding",
    "ConsolePlatformConsent",
    "ensure_console_schema",
    "utcnow",
]
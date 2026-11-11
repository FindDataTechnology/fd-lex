"""平台代做的授权判定（change converge-line-identity-and-keys 2.2）。

断言验签通过只证明"这份断言是真的"；本模块回答后半个问题："这个平台此刻被
允许代这个人在这条线做什么"。三档判据：

  off        一律拒（默认）
  first-use  必须先有身份绑定；**没有绑定就返回 binding_required，不猜不建**
  consent    除绑定外还要一条未撤销的授权行，且生效 scope = 断言 ∩ 授权

第三条的"交集"是关键：授权行授予 line:read 时，一份带 line:admin 的断言必须
连 line:read 都拿不到（越界整条拒），而不是"降级放行"。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fd_lex_api.billing.models import Base as BillingBase, Customer, Plan
from fd_lex_api.console_api.delegation import (
    DelegationDenied,
    authorize_delegation,
    grant_consent,
    revoke_consent,
)
from fd_lex_api.console_api.models import ConsoleBase, ConsoleIdentityBinding


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    BillingBase.metadata.create_all(engine)
    ConsoleBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)()
    try:
        yield session
    finally:
        session.close()


def _plan(db) -> Plan:
    """billing_customers.plan_id 是 NOT NULL 外键——没有套餐就没有客户。"""
    plan = Plan(code="std", name="Standard", monthly_price_micro=0)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _bound_customer(db, identity: str = "alice", sub2api_user_id: str | None = None) -> Customer:
    plan = _plan(db)
    customer = Customer(
        name=identity.title(), sub2api_user_id=sub2api_user_id or identity, plan_id=plan.id
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    db.add(ConsoleIdentityBinding(customer_id=customer.id, identity=identity))
    db.commit()
    return customer


def test_jit_off_rejects_before_any_lookup(db):
    # off 档连"这个人登记过没有"都不问：不查绑定、不查库。
    with pytest.raises(DelegationDenied) as e:
        authorize_delegation(
            db, subject="whoever", platform_subject="https://platform.test",
            requested_scope=("line:read",), jit_policy="off",
        )
    assert e.value.reason == DelegationDenied.JIT_DISABLED


def test_unknown_identity_is_refused_and_never_created(db):
    with pytest.raises(DelegationDenied) as e:
        authorize_delegation(
            db, subject="stranger", platform_subject="https://platform.test",
            requested_scope=("line:read",), jit_policy="first-use",
        )
    assert e.value.reason == DelegationDenied.BINDING_REQUIRED
    # 铁律的反向断言：被拒之后库里不该多出任何一行。
    assert db.query(ConsoleIdentityBinding).count() == 0
    assert db.query(Customer).count() == 0


def test_first_use_with_binding_passes_and_carries_requested_scope(db):
    customer = _bound_customer(db)
    decision = authorize_delegation(
        db, subject="alice", platform_subject="https://platform.test",
        requested_scope=("line:read", "line:write"), jit_policy="first-use",
    )
    assert decision.customer_id == customer.id
    assert decision.scope == ("line:read", "line:write")
    assert decision.consent_id is None


def test_first_use_accepts_binding_by_sub2api_user_id(db):
    # 绑定表为空、但 Customer.sub2api_user_id 相等 → 既有骨架兼容路径。
    customer = _bound_customer(db, identity="alice", sub2api_user_id="alice")
    db.query(ConsoleIdentityBinding).delete()
    db.commit()
    decision = authorize_delegation(
        db, subject="alice", platform_subject="https://platform.test",
        requested_scope=("line:read",), jit_policy="first-use",
    )
    assert decision.customer_id == customer.id


def test_consent_without_row_is_refused(db):
    _bound_customer(db)
    with pytest.raises(DelegationDenied) as e:
        authorize_delegation(
            db, subject="alice", platform_subject="https://platform.test",
            requested_scope=("line:read",), jit_policy="consent",
        )
    assert e.value.reason == DelegationDenied.CONSENT_REQUIRED


def test_consent_scope_is_intersection_not_upgrade(db):
    customer = _bound_customer(db)
    grant_consent(
        db, customer_id=customer.id, platform_subject="https://platform.test",
        scope="line:read line:write", granted_by="alice@lex",
    )
    # 请求在授权范围内 → 通过，且只拿到交集
    decision = authorize_delegation(
        db, subject="alice", platform_subject="https://platform.test",
        requested_scope=("line:read",), jit_policy="consent",
    )
    assert decision.scope == ("line:read",)
    assert decision.consent_id is not None

    # 请求越界（含未授予的 line:admin）→ 整条拒，不是"降级放行 read"
    with pytest.raises(DelegationDenied) as e:
        authorize_delegation(
            db, subject="alice", platform_subject="https://platform.test",
            requested_scope=("line:admin",), jit_policy="consent",
        )
    assert e.value.reason == DelegationDenied.CONSENT_REQUIRED


def test_consent_is_per_platform_subject(db):
    customer = _bound_customer(db)
    grant_consent(
        db, customer_id=customer.id, platform_subject="https://platform.test",
        scope="line:read", granted_by="alice@lex",
    )
    with pytest.raises(DelegationDenied):
        authorize_delegation(
            db, subject="alice", platform_subject="https://other-platform.test",
            requested_scope=("line:read",), jit_policy="consent",
        )


def test_revoke_takes_effect_immediately_and_keeps_the_row(db):
    customer = _bound_customer(db)
    row = grant_consent(
        db, customer_id=customer.id, platform_subject="https://platform.test",
        scope="line:read", granted_by="alice@lex",
    )
    assert revoke_consent(db, row.id) is True
    with pytest.raises(DelegationDenied) as e:
        authorize_delegation(
            db, subject="alice", platform_subject="https://platform.test",
            requested_scope=("line:read",), jit_policy="consent",
        )
    assert e.value.reason == DelegationDenied.CONSENT_REQUIRED
    # 撤销要留痕（审计要看"曾经授权过"），不是删行
    assert db.query(type(row)).count() == 1
    assert revoke_consent(db, row.id) is False, "重复撤销应幂等返回 False"

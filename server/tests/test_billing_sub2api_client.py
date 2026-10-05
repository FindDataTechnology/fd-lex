"""sub2api admin 客户端契约：幂等头 / 带符号金额 / 余额解析 / 探针 fail-closed。
（全部经 httpx.MockTransport，绝不真实调用 sub2api。）"""

from __future__ import annotations

import httpx
import pytest

from fd_lex_api.billing import Sub2ApiClient, Sub2ApiError, yuan_to_micro

from test_billing_common import BASE, FakeSub2Api, make_engine, make_plan


def test_adjust_balance_sends_idempotency_key_and_negative_amount() -> None:
    fake = FakeSub2Api()
    fake.add_user("u-1", balance_micro=1_000_000)
    client = fake.client()
    client.adjust_balance(user_id="u-1", amount_micro=-400_000, idempotency_key="k-1")
    call = fake.adjust_calls[0]
    assert call["idem"] == "k-1"
    assert "-400000" in call["body"]  # 扣减为负
    assert fake.balance("u-1") == 600_000


def test_adjust_requires_explicit_idempotency_key() -> None:
    fake = FakeSub2Api()
    client = fake.client()
    with pytest.raises(ValueError):
        client.adjust_balance(user_id="u-1", amount_micro=-1, idempotency_key="")


def test_replay_same_key_charged_once_via_server_cache() -> None:
    """同幂等键重放：假件服务端缓存返回同响应，余额只动一次。"""
    fake = FakeSub2Api()
    fake.add_user("u-1", balance_micro=1_000_000)
    client = fake.client()
    client.adjust_balance(user_id="u-1", amount_micro=-100, idempotency_key="same")
    client.adjust_balance(user_id="u-1", amount_micro=-100, idempotency_key="same")
    assert len(fake.adjust_calls) == 2  # 两次调用
    assert fake.balance("u-1") == 1_000_000 - 100  # 净一次扣减


def test_read_user_parses_yuan_and_micro_forms() -> None:
    """余额解析：*_micro 整数优先；「balance=元」兼容形态折算微元。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization")
        if "users/u-2" in str(request.url):
            return httpx.Response(200, json={"id": "u-2", "name": "n",
                                             "balance_micro": 1_234_567})
        return httpx.Response(200, json={"user": {"id": "u-3", "name": "n",
                                                  "balance": "12.5"}})

    client = Sub2ApiClient(
        base_url=BASE, admin_key="key-x",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    user = client.read_user("u-2")
    assert user.balance_micro == 1_234_567
    assert captured["auth"] == "Bearer key-x"
    legacy = client.read_user("u-3")  # {"user": {...}} 包裹 + 元单位
    assert legacy.balance_micro == yuan_to_micro("12.5")


def test_unreachable_maps_to_unavailable_and_probe_fail_closed() -> None:
    fake = FakeSub2Api()
    fake.add_user("u-1", balance_micro=1)
    client = fake.client()
    fake.fail_reads_left = 2  # read_user 与 probe 各消耗一次 503

    with pytest.raises(Sub2ApiError):
        client.read_user("u-1")  # 503 → Sub2ApiUnavailable

    probe = client.probe("u-1", min_balance_micro=0)
    assert probe.reachable is False and probe.sufficient is False  # fail-closed
    assert probe.error is not None


def test_probe_sufficient_boundary() -> None:
    fake = FakeSub2Api()
    fake.add_user("u-1", balance_micro=1_000)
    client = fake.client()
    assert client.probe("u-1", min_balance_micro=1_000).sufficient is True
    assert client.probe("u-1", min_balance_micro=1_001).sufficient is False


def test_4xx_raises_contract_error() -> None:
    fake = FakeSub2Api()
    client = fake.client()  # u-missing 不存在 → 404
    with pytest.raises(Sub2ApiError) as exc_info:
        client.read_user("u-missing")
    assert exc_info.value.status_code == 404


def test_create_user_idempotent() -> None:
    fake = FakeSub2Api()
    client = fake.client()
    first = client.create_user(name="客户A", idempotency_key="customer:ascii-key-1")
    again = client.create_user(name="客户A", idempotency_key="customer:ascii-key-1")
    assert first.id == again.id
    assert len([c for c in fake.calls if c["op"] == "POST" and c["url"].endswith("/users")]) == 2
    assert len(fake.users) == 1


def test_ensure_customer_default_key_is_ascii_safe() -> None:
    """默认幂等键对客户名做散列：HTTP 头不能带非 ASCII（中文客户名不炸）。"""
    from sqlalchemy.orm import Session

    from fd_lex_api.billing import ensure_customer

    fake = FakeSub2Api()
    engine = make_engine()
    with Session(engine) as session:
        plan = make_plan(session, code="std")
        session.commit()
        customer, created = ensure_customer(
            session, fake.client(), name="华东法务客户", plan_code="std"
        )
        assert created is True
        key = [c["idem"] for c in fake.calls if c["op"] == "POST" and c["url"].endswith("/users")][0]
        assert key.isascii() and key.startswith("customer:")

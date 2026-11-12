"""门 K 直连调用的计量落账（change converge-line-identity-and-keys 7.3）。

spec（`line-key-track`「计量按线归属且不重复记账」）：每次跨线调用**只在被调用的
那条线上记账一次**，归属 ``source=<line>``；**换票动作本身不得产生任何计费条目**。

两条车道、同一张表：

- **registry 车道**（经网关 `/mcp` 代理）：账来自 registry 审计事件，由
  ``metering/ingest.py`` 幂等落库（键 ``request_id + server_name``）。
- **门 K 直连车道**（客户拿换来的票直接打线，见 paper-trading 的
  ``consumption_note`` 与 7.2）：**没有** registry 审计事件，所以由本模块在
  调用点落账——同一张 ``metering_records``、同一个幂等键，只是 ``source`` 记线名
  而不是 ``registry``/``lex-internal``。

为什么不做成第二条账本：两本账迟早对不上，而对不上时没人能判断哪本是钱的真源。
同一张表 + 同一个幂等键，才使「一次调用一条账」在两条车道上都成立。

**换票不计费**在代码里的保证不是"记得别写"，而是：换票那条路（``line_token.py``
的校验与网关侧的签发）不 import 也不接触本模块——校验是无副作用的纯函数。
用例 ``test_verify_token_writes_no_ledger_row`` 把这一点钉住。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .line_token import LineTokenIdentity
from .metering.models import UNIT_CALL, MeteringRecord, TRACK_CUSTOMER

__all__ = ["meter_line_call"]

#: 门 K 直连车道落账时的 server 名（线上该线的 MCP 面）
DEFAULT_SERVER_NAME = "lex-ops-mcp"


def meter_line_call(
    db: Session,
    identity: LineTokenIdentity,
    *,
    request_id: str,
    tool_name: str | None = None,
    quantity: int = 1,
    unit: str = UNIT_CALL,
    server_name: str = DEFAULT_SERVER_NAME,
    server_path: str | None = "/lex-ops-mcp",
    event_timestamp: datetime | None = None,
) -> MeteringRecord | None:
    """为一次门 K 直连调用落一条账。重复的 ``request_id`` 不产生第二条。

    ``request_id`` 由调用方给（通常取 MCP 请求 id + 工具名）：它是幂等键，
    重放/重试必须带同一个值，否则就是两次账。缺它不得落账——宁可报错也不写
    一条无法去重的行。

    返回：新写入的行；已存在同键行时返回 ``None``（"这次没有新增"），
    让调用方能区分「记了」与「早就记过」。
    """
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id is required for metering (it is the idempotency key)")

    row = MeteringRecord(
        event_timestamp=event_timestamp or datetime.now(UTC),
        request_id=request_id.strip(),
        # 客户轨才进结算（`credential_track=platform` 归集不结算）
        caller=identity.email or identity.subject,
        caller_principal=identity.email,
        credential_track=TRACK_CUSTOMER,
        server_name=server_name,
        server_path=server_path,
        tool_name=tool_name,
        quantity=max(1, int(quantity)),
        unit=unit,
        # 归属：这张票为哪条线签发，账就记在哪条线（跨线复用票在 7.2 已被拒）
        source=identity.source,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        # 唯一约束 (request_id, server_name) 就是幂等闸：谁先写入谁赢，
        # 不靠应用层先查后写（并发下"先查后写"必双写）。
        db.rollback()
        return None
    return row

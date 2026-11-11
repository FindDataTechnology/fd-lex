"""断言 jti 的落库台账（change converge-line-identity-and-keys 2.1）。

``verify_assertion`` 只接受一个 ``ReplayStore`` 协议实现；本模块给它一个**跨进程
有效**的实现。为什么不能放进程内：多副本部署下同一份断言打到另一个副本会再次通过，
而重放正是攻击者会主动换副本做的事。

判据由数据库裁决：``jti`` 唯一约束 + 一次显式 commit。插入成功=本次是首次见到；
``IntegrityError``=别人已经认领过，按"重放"拒绝。应用层不做"先查再插"——那中间
有窗口，两个并发请求会双双查到"没见过"。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ConsoleAssertionReplay

__all__ = ["DbReplayStore"]


class DbReplayStore:
    """``ReplayStore`` 协议的库实现：``claim(jti, expires_at) -> bool``。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(self, jti: str, expires_at: int) -> bool:
        """认领一个 jti。True=首次见到（可放行），False=已被用过（重放）。"""
        row = ConsoleAssertionReplay(jti=str(jti), expires_at=int(expires_at))
        self._session.add(row)
        try:
            self._session.commit()
        except IntegrityError:
            # 别人先写了：唯一约束挡在这里，回滚后如实返回"已用过"。
            self._session.rollback()
            return False
        # 顺手清掉**已经过期**的行（只在这一步做，省掉单独的清道夫）。
        # 判据必须是"早于此刻"，不是"早于本断言的 exp"——后者会删掉尚未到期、
        # 仍具保护价值的其它 jti，等于把它们的重放窗口重新打开。
        try:
            import time

            self._session.query(ConsoleAssertionReplay).filter(
                ConsoleAssertionReplay.expires_at < int(time.time())
            ).delete(synchronize_session=False)
            self._session.commit()
        except Exception:  # noqa: BLE001 — 清理失败不影响"本次认领成立"
            self._session.rollback()
        return True

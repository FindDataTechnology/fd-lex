"""fd-lex 运行时配置：全部 env 可覆盖（同名环境变量优先）。

命名沿用 fd-wire 生产验证的借用契约口径（README「借用契约」纪律）：
对不归本仓的能力（registry / sub2api）只经显式 HTTP 契约访问，端点与
密钥都是占位默认值、部署时用同名环境变量覆盖。

环境变量一览：

- ``DATABASE_URL``：lex 库连接串（计量 + 账本同库），默认
  ``sqlite:///./fd_lex_dev.db``；测试指向内存库/临时库。
- ``SUB2API_BASE`` / ``SUB2API_ADMIN_KEY``：sub2api 计费面 admin API
  （与 wire 同一生产设施、同一契约端点）。
- ``INTERNAL_API_TOKEN``：全部 ``/internal/*`` 端点的 bearer 校验令牌；
  未配置时 fail-closed（403），绝不无令牌放行。接入 Logto 后由此收敛
  （骨架期简单 bearer，见 billing/deps.py）。
- ``PLATFORM_IDENTITIES``：平台服务身份名单（逗号分隔 username，大小写
  不敏感）。命中的调用 → credential_track=platform（平台自用，不进客户
  账单），否则 customer。
- ``REGISTRY_MONGO_URI`` / ``REGISTRY_MONGO_DB`` /
  ``REGISTRY_MONGO_COLLECTION``：registry Mongo 审计流直读（可选路径）；
  URI 未配置时该路径禁用。
- ``METERING_POLL_LIMIT``：audit 流单轮消费批量上限，默认 500。
- ``METERING_AUTO_CREATE_TABLES``：启动时 create_all（默认 true，开发
  便利；生产迁移链由主代理收敛后可关闭）。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """lex 运行时配置（同名环境变量优先）。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./fd_lex_dev.db"
    sub2api_base: str = "https://router.finddatatech.cloud"
    sub2api_admin_key: str = ""
    internal_api_token: str | None = None
    platform_identities: str = ""
    registry_mongo_uri: str | None = None
    registry_mongo_db: str | None = None
    registry_mongo_collection: str = "audit_events"
    metering_poll_limit: int = 500
    metering_auto_create_tables: bool = True

    def platform_identity_set(self) -> frozenset[str]:
        """平台服务身份名单（小写精确匹配）。"""
        return frozenset(
            name.strip().lower() for name in self.platform_identities.split(",") if name.strip()
        )

    def describe(self) -> dict[str, Any]:
        """当前生效配置一览（不含令牌明文，排障用）。"""
        return {
            "database_url": self.database_url,
            "sub2api_base": self.sub2api_base,
            "sub2api_admin_key_configured": bool(self.sub2api_admin_key),
            "internal_api_token_configured": bool(self.internal_api_token),
            "platform_identities": sorted(self.platform_identity_set()),
            "registry_mongo_configured": bool(self.registry_mongo_uri and self.registry_mongo_db),
            "registry_mongo_collection": self.registry_mongo_collection,
            "metering_poll_limit": self.metering_poll_limit,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例；测试用 ``reset_settings()`` 后重设 env 再取。"""
    return Settings()


def reset_settings() -> None:
    """清空配置缓存——测试切换环境变量用。"""
    get_settings.cache_clear()

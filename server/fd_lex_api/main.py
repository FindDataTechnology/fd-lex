"""fd-lex API 服务端。

lex-platform-v1（task 5.1+5.2）：结算/分发/后台 API。
本期提供：/healthz 健康检查、/api/version 版本自报、
/internal/metering（双单位计量摄取/消费/查询）、
/internal/billing（结算触发/三方对账/账本/额度状态/套餐配置）。
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fd_lex_api.billing.routes import router as billing_router
from fd_lex_api.metering.routes import router as metering_router

app = FastAPI(
    title="fd-lex-api",
    description="识律(lex) 平台服务端：结算/分发/后台 API",
    version="0.1.0",
)

# 骨架期 CORS 全开（allow_origins=* 时 credentials 必须为 False）；
# 接入 Logto / 网关后按域名收敛 allow_origins。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 内部服务面（计量 + 结算账本）；鉴权骨架见 billing/deps.require_internal_token
app.include_router(metering_router)
app.include_router(billing_router)


def _build_version() -> str:
    """构建版本：CI/部署注入 BUILD_VERSION 优先，缺省 dev。"""
    return os.environ.get("BUILD_VERSION") or "dev"


@app.get("/healthz")
def healthz() -> dict:
    """存活探针。"""
    return {"status": "ok"}


@app.get("/api/version")
def version() -> dict:
    """版本自报：镜像 tag / 部署核对用。"""
    return {"service": "fd-lex-api", "version": _build_version()}

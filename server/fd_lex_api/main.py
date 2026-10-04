"""fd-lex API 服务端骨架。

lex-platform-v1（task 4.1）：结算/分发/后台 API 的落位起点。
本期只提供最小可运行面：/healthz 健康检查、/api/version 版本自报。
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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

# fd-lex-api

识律平台服务端（FastAPI）。当前提供健康检查、版本自报与计量结算内部服务面
（lex-platform-v1 task 5.1+5.2）：

- `GET /healthz` — 存活探针，返回 `{"status": "ok"}`
- `GET /api/version` — 版本自报（`BUILD_VERSION` 环境变量优先，默认 `dev`）
- `/internal/metering` — 双单位计量（行/次，token 预留）：审计事件摄取、
  registry Mongo audit 流消费、用量聚合、异常留痕查询
- `/internal/billing` — 结算账本：结算触发（sub2api 幂等扣减）、账本×计量流×
  支付侧三方对账、账本流水、额度状态预检（fail-closed）、套餐配置（调价不发版）
- 全部 `/internal/*` 端点走 `INTERNAL_API_TOKEN` 简单 bearer（未配置时
  fail-closed 403）；接 Logto 后在 `fd_lex_api/billing/deps.py` 一处收敛
- CORS 骨架期全开（接入 Logto/网关后收敛）

## 本地开发

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/uvicorn fd_lex_api.main:app --reload   # http://127.0.0.1:8000
```

环境变量样例见 `.env.example`。架构参照 fd-wire 结算域移植（账本权威 +
sub2api 幂等结算 + 两档预警 80%/95%）；规格见
`openspec/changes/lex-platform-v1/specs/lex-billing-metering/spec.md`。

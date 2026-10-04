# fd-lex-api

识律平台服务端骨架（FastAPI）。当前为最小可运行面：

- `GET /healthz` — 存活探针，返回 `{"status": "ok"}`
- `GET /api/version` — 版本自报（`BUILD_VERSION` 环境变量优先，默认 `dev`）
- CORS 骨架期全开（接入 Logto/网关后收敛）

后续落位：计量记录/账本、分发端点、后台 admin API（见 `openspec/changes/lex-platform-v1`）。

## 本地开发

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/uvicorn fd_lex_api.main:app --reload   # http://127.0.0.1:8000
```

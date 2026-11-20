# fd-lex

<!-- deploy-location -->
> **部署清单不在本仓。** 集群清单已迁至 `fd-infra-deploy/all-services/`，
> Application 定义在 `fd-infra-deploy/argocd/`。
> 原因与判据：`openspec/changes/consolidate-deploy-manifests`（2026-11-20）。


> **Part of the [Lex line (寻数·识律)](https://www.finddatatech.cloud/products/lex) of [FindData](https://www.finddatatech.cloud)** — the legal workbench platform: matter management, metering and metering audit, served as REST and MCP surfaces.

识律（lex）独立平台门面单仓：四入口的 web 面（官网 / 控制台 / 管理后台）+ FastAPI 服务端（结算/分发/后台 API）。

**两个宿主面（不是同一入口的两个地址）**——完整口径见 `docs/PRODUCT-LINEUP.md` 与单一真源 `docs/product-lineup.json`：

| 宿主面 | 域名 | 职责 |
|---|---|---|
| 产品面 `product` | `law.finddatatech.cloud` | 良律（LiangLü）产品门面；官网与材料里的对外入口指这里；页面声明 `rel=canonical` |
| 平台面 `platform` | `lex.finddatatech.cloud` | 家族与既有客户入口，同时承载 `/console`、`/mcp`、签署人公网链——Logto 回跳与在途签署链钉在这里，**不可改指** |

两面之间不做跳转（2026-10-30 裁决：各自独立可访问）。**已知欠账**：两面目前服务同一份前端构建产物，产品站与控制台的内容分化未做。

建仓依据 `openspec/changes/lex-platform-v1`（task 4.1 **当年只落地骨架**；此后服务端已成长为真实计费面，见「仓结构」下的更正）。技术栈（design D12）：前端 React 19 + Vite + TS + Tailwind v4 + i18next 双语（fd 舰队配方）；服务端 Python FastAPI（萬星 bookkeeping 概念移植）；部署 cheap fd-prod + TCR + ArgoCD（mcp-service-deploy 既有模式）。

服务本体（法条/类案/语料检索 MCP）在独立仓 `fd-legal-search-mcp`，本仓不承载检索实现。

## 仓结构

| 目录 | 内容 | 镜像（hkccr.ccs.tencentyun.com/finddata-business/） |
|---|---|---|
| `web/` | React 19 + Vite 单页：`/` 官网 landing、`/docs`、`/deploy` 等（**当前 4 个真实页面，仍在扩充**） | `fd-lex-web` |
| `server/` | FastAPI 服务端：**51 个路由 / 7 个业务模块**（`billing` 结算与账本、`metering` 计量、`console_api` 租户控制台、`admin_api` 管理后台、`esignature` 电子签、`distribution` 分发、`matter` 事项），含 Logto JWKS 校验与 `require_admin` 门禁 | `fd-lex-api` |

> ⚠️ **本节 2026-11-10 更正**：此前这里写的是「骨架期均为真实可渲染占位页」「FastAPI 服务端骨架：`/healthz`、`/api/version`」——**与代码不符**。服务端已是承载真实资金与身份的计费服务（sub2api 幂等结算 + 账本、计量摄取、电子签的 fail-closed provider 门、Range 分发），不是骨架。措辞失真会让读者低估它的风险等级（审计 06 §12）。
>
> 数据库 schema 唯一事实源是 **alembic 迁移链**（`server/alembic/versions/`），启动时的 `create_all()` 默认关闭（`METERING_AUTO_CREATE_TABLES=false`）。

## 四入口规划

| 入口 | 路径 | 说明 | 状态（2026-11-10 实测） |
|---|---|---|---|
| 官网 landing | `/` | 「识律 Lex」品牌与产品定位（法律数据·应用·模型，全栈可私有化） | 已上线（另有 `/docs`、`/deploy`） |
| 控制台 console | `/console` | 租户/用量/结算/分发入口 | **8 个业务页**：Overview / Usage / Billing / Quota / Credentials / Copilot 等 |
| 管理后台 admin | `/admin` | 平台后台，按 panel 角色门禁模式 | **7 个业务页** |
| 平台 API | `/api`（server 侧） | 结算/分发/后台 API | **51 个路由 / 7 个模块**（见上方更正） |

i18n：i18next 中英双语（zh/en 各一个 `translation` namespace），导航栏可切换。

## 本地开发

server（FastAPI）：

```sh
cd server
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/uvicorn fd_lex_api.main:app --reload   # http://127.0.0.1:8000
```

web（React + Vite）：

```sh
cd web
npm ci
npm run dev        # http://localhost:5173
npm run build      # 产物 dist/
```

## 发布通道

GitHub Actions -> TCR 个人版（`.github/workflows/image.yml`，一个 workflow 两个 job：web / api 各一）。

- 仓库：`hkccr.ccs.tencentyun.com/finddata-business/fd-lex-web`、`.../fd-lex-api`
- tag：`sha-<短哈希>` + `main`；provenance 关闭、gha 缓存
- the relay node tcr-relay 5 分钟内回灌 `ccr` 侧
- 配方：`docs/IMAGE-RELEASE.md`（硬性要求：上游源为默认，国内镜像源一律 ARG 且默认空）

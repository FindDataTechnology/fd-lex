# fd-lex 部署 runbook（lex-platform-v1 任务 4.5）

通道：tcr-image-pipeline（GHA→TCR→relay）+ mcp-service-deploy（ArgoCD/GitOps、不可变 sha- tag）。

| 工件 | 作用 |
|---|---|
| `deploy/k8s/fd-lex-web.yaml` | Deployment + Service（NodePort 31885，nginx :80） |
| `deploy/k8s/fd-lex-api.yaml` | Deployment + Service（ClusterIP :80→8000） |
| `deploy/k8s/ingress.yaml` | Ingress 占位（host `lex.finddatatech.cloud`；实际入口=Caddy） |
| `deploy/argocd/application.yaml` | ArgoCD Application（gitee 源 `deploy/k8s`，dest `fd-prod`） |

## Secret `fd-lex-env` 键名表（集群内创建，永不入 git）

| env | 用途 |
|---|---|
| `DATABASE_URL` | 结算账本库（**2026-10-12 起=PG**：postgresql+psycopg://lex:***@100.64.0.12:5434/lex_platform，cheap-3 lex-postgres 容器；旧 sqlite 骨架债已销） |
| `INTERNAL_API_TOKEN` | /internal/* 简单 bearer（接 Logto 后收敛到 billing/deps.py 一处）；同时是后台 X-Admin-Token 路径的令牌 |
| `SUB2API_BASE` | https://router.finddatatech.cloud |
| `SUB2API_ADMIN_KEY` | sub2api admin API（与 fd-wire-env 同键借用） |
| `REGISTRY_MONGO_*` | audit 流直读（MongoAuditSource；**2026-10-12 起生产在役**：lex_metering_ro 只读账号 @100.64.0.11:27017 + COLLECTION=audit_events_default） |
| `METERING_SERVER_ALLOWLIST` | lex 计费域 server 白名单（防别线流量吸进 lex 计量表） |
| `METERING_DRIVER_ENABLED/INTERVAL_SECONDS` | 周期「审计摄取→闭月结算」驱动（生产 true/300s） |
| `METERING_AUTO_CREATE_TABLES` | 生产 false——schema 唯一事实源=alembic 链（initContainer upgrade head） |
| `LOGTO_ISSUER` | 控制台登录（4.3）：`https://auth.finddatatech.cloud`（issuer 取其 + /oidc） |
| `LOGTO_CLIENT_ID` | 控制台 SPA 应用 client_id（Logto 侧新建，见下「外部配置」） |
| `LOGTO_RESOURCE` | 控制台请求的 API resource（默认 `https://mcp.finddatatech.cloud/api`，须与 registry `LOGTO_M2M_RESOURCE` 一致） |
| `LOGTO_REQUIRED_ORG_IDS` | 组织门禁（`hnrbfpgq2717` = lex-customers）；留空则不做组织门禁（不推荐） |
| `ADMIN_ROLES` | 后台 Logto 角色名（默认 `lex-admin`；未在 Logto 建角色则只能走 X-Admin-Token） |
| `REGISTRY_BASE_URL` | 凭据代理目标（默认 `https://mcp.finddatatech.cloud`） |
| `CONSOLE_ORIGIN` | `https://lex.finddatatech.cloud`（回跳提示；不参与鉴权判定） |
| `LINE_TOKEN_ISSUER` | 门 K（change converge-line-identity-and-keys 7.2）：线票签发方 `https://mcp.finddatatech.cloud`。**三个 `LINE_TOKEN_*` 缺任一个 = 这条路自动关闭**，只认 Logto、错凭据仍回原来那条 401（与接入前逐字节一致） |
| `LINE_TOKEN_LINE` | 门 K：本线名 `lex`——令牌的 `aud` 与 `source` 都必须等于它，别的线换来的票一律不认 |
| `LINE_TOKEN_PUBLIC_KEY_PEM` | 门 K：平台换票用的 ES256 **公钥**。私钥只留平台侧（Secret `line-signing-keys` 的 `LINE_EXCHANGE_KEY_LEX`，含 `LINE_EXCHANGE_KID_LEX`）；轮换=新 `kid` + 新公钥上线，旧票自然过期后下线旧钥 |
| `ESIGN_FILE_STORE_DIR` | 电子签文件仓（默认 `./esign_files`；生产=`/data/esign` → PVC `lex-esign-files`）。合同全文只落客户侧，**卷必须纳入备份** |
| `ESIGN_SIGNER_SESSION_TTL_SECONDS` | 签署人 H5 会话有效期（默认 1800s） |
| `ESIGN_PROVIDER` | 签署腿适配器（**未设即 fail-closed**：发起如实报「签署腿未配置」；`stub` 仅开发/测试；厂商值待选型 spike 通过后接入，见 `docs/ESIGN-PROVIDER-ONBOARDING.md`） |

### 电子签（lex-contract-esignature）部署要点

- 签署人链接域名=客户侧公网入口 `https://lex.finddatatech.cloud/api/esignature/p/*`
  （复用既有 `/api/*` Caddy 反代；私有化包内由客户自己的前置代理提供）。

- `lex-esign-files` PVC 承载合同全文与出证包 zip：**丢卷=待签任务无法预览、已签任务无法下载出证包**；备份策略随客户备份制度。

- `ESIGN_PROVIDER` 未配置时签署腿如实报错（不静默降级、不假报成功），属预期行为；配置厂商值前须先过 change tasks 组 1 的沙箱验收。

## 控制台/后台的外部配置清单（4.3/4.4，需人工在外部系统操作）

1. **Logto Admin Console**（auth-admin.finddatatech.cloud）
   - 新建**第一方 SPA 应用**（名如 `lex-console`）→ 记下 client_id 回填 `LOGTO_CLIENT_ID`。
   - Redirect URI（精确）：`https://lex.finddatatech.cloud/console/callback`
   - Post sign-out redirect URI（精确）：`https://lex.finddatatech.cloud/console`
   - 允许 scope：`openid profile email roles offline_access urn:logto:scope:organizations`
   - 确保该应用对 API resource `https://mcp.finddatatech.cloud/api` 有访问权
     （registry auth-server 的受信 audience；access token 的 aud 必须是它，
     否则 `/api/patch-keys` 代理会 401）。
   - （可选，后台角色路径）建角色 `lex-admin` 并分配给运营账号；否则后台用
     `INTERNAL_API_TOKEN` 作 X-Admin-Token。
   - lex-customers org（`hnrbfpgq2717`）成员资格即控制台准入；新注册用户
     需运营加入该组织（内外隔离）。若需客户自助注册，Logto Sign-in experience
     需开启 Sign-up（关闭时控制台只提供登录）。
2. **registry / auth-server**：无需改动（Bearer 透传路径已被 `/validate`
   的 Logto provider + patch-key 分支覆盖；CSRF 对无 cookie 请求自动跳过）。
3. **Caddy**：无需改动（`/api/*` → fd-lex-api NodePort 31886 已上线，
   `/api/version` 实测 200；新端点自带 `/api` 前缀）。
4. **Web 构建**：无需构建期变量（OIDC 配置由 `/api/console/auth/config` 运行时下发）。

## 上线步骤

1. push main → GHA 双镜像 `sha-<7>`；relay 回灌 ccr。
2. 回填两 manifest 镜像 tag，提交。
3. 集群建 Secret（上表）+ argocd ns repo 凭据（`repo-fd-lex`，url/username/password/type + label）。
4. `kubectl --context cheap -n argocd apply -f deploy/argocd/application.yaml`。
5. 验收：`curl http://<node>:31885/` 官网 200、`/healthz` ok；`/api/version`（经 ClusterIP）200。
6. 4.3/4.4 验收补充：`curl https://lex.finddatatech.cloud/api/console/auth/config`
   （未配 Logto 时 configured=false 且 missing 列出缺项——如实阻塞态）；
   配置 Logto 后浏览器走 `/console` 登录 → 铸凭据 → MCP 调用 → 用量显示。

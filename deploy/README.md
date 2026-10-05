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
| `DATABASE_URL` | 结算账本库（v1 骨架 sqlite:////tmp/fd_lex_billing.db；接真实客户前迁 PG） |
| `INTERNAL_API_TOKEN` | /internal/* 简单 bearer（接 Logto 后收敛到 billing/deps.py 一处）；同时是后台 X-Admin-Token 路径的令牌 |
| `SUB2API_BASE` | https://router.finddatatech.cloud |
| `SUB2API_ADMIN_KEY` | sub2api admin API（与 fd-wire-env 同键借用） |
| `REGISTRY_MONGO_*` | audit 流消费（MongoAuditSource；未配时消费端点显式 409，骨架期留空） |
| `LOGTO_ISSUER` | 控制台登录（4.3）：`https://auth.finddatatech.cloud`（issuer 取其 + /oidc） |
| `LOGTO_CLIENT_ID` | 控制台 SPA 应用 client_id（Logto 侧新建，见下「外部配置」） |
| `LOGTO_RESOURCE` | 控制台请求的 API resource（默认 `https://mcp.finddatatech.cloud/api`，须与 registry `LOGTO_M2M_RESOURCE` 一致） |
| `LOGTO_REQUIRED_ORG_IDS` | 组织门禁（`hnrbfpgq2717` = lex-customers）；留空则不做组织门禁（不推荐） |
| `ADMIN_ROLES` | 后台 Logto 角色名（默认 `lex-admin`；未在 Logto 建角色则只能走 X-Admin-Token） |
| `REGISTRY_BASE_URL` | 凭据代理目标（默认 `https://mcp.finddatatech.cloud`） |
| `CONSOLE_ORIGIN` | `https://lex.finddatatech.cloud`（回跳提示；不参与鉴权判定） |

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

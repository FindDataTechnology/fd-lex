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
| `INTERNAL_API_TOKEN` | /internal/* 简单 bearer（接 Logto 后收敛到 billing/deps.py 一处） |
| `SUB2API_BASE` | https://router.finddatatech.cloud |
| `SUB2API_ADMIN_KEY` | sub2api admin API（与 fd-wire-env 同键借用） |
| `REGISTRY_MONGO_*` | audit 流消费（MongoAuditSource；未配时消费端点显式 409，骨架期留空） |

## 上线步骤

1. push main → GHA 双镜像 `sha-<7>`；relay 回灌 ccr。
2. 回填两 manifest 镜像 tag，提交。
3. 集群建 Secret（上表）+ argocd ns repo 凭据（`repo-fd-lex`，url/username/password/type + label）。
4. `kubectl --context cheap -n argocd apply -f deploy/argocd/application.yaml`。
5. 验收：`curl http://<node>:31885/` 官网 200、`/healthz` ok；`/api/version`（经 ClusterIP）200。

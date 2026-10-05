# lex-packer — 识律交付包构建器

交付包（Distribution）= 版本化整机交付单元；本目录是它的构建器（独立于 `server/`
服务进程，只读数据源、只做打包，不重复实现检索逻辑）。

## 安装

```bash
cd fd-lex/packer
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # 离线打包/测试
.venv/bin/pip install -e '.[pg]'                              # law_db 直读（可选）
.venv/bin/pip install -e '.[s3]'                              # RustFS 拉取 flk 日增量（可选）
```

## CLI

```bash
# CI 云专属依赖守卫（违规非 0 退出，指名组件与修复方向）
python -m lex_packer guard --components lex-api,qdrant,logto-cloud

# 引用包真实构建（law_db 只读；约 650MB 原始 → ~200MB gz）
python -m lex_packer build --tier citation --version 0.1.0 --out /tmp/lex-dist \
  --source-pg "$FDLEX_LAW_DATABASE_URL"

# manifest 全过校验（组件 sha256/字节数 + tar 摘要 + provenance 三件套）
python -m lex_packer verify --pack-dir /tmp/lex-dist/lex-dist-v0.1.0-citation

# flk 日增量 → 分发增量 bundle（RustFS laws/incremental/ 直拉；也可 --local-dir 离线镜像）
python -m lex_packer flk-incremental --version 0.1.0 --out /tmp/lex-dist --bucket laws
  # 连接参数走 env：FDLEX_RUSTFS_ENDPOINT / FDLEX_RUSTFS_ACCESS_KEY / FDLEX_RUSTFS_SECRET_KEY
  # 选择范围：--days N（最新 N 个有数据的日期）| --date/--since/--until；默认全量

# 结构增量包 + 离线等价（基座+增量应用后与目标 full 逐组件一致）
python -m lex_packer incremental --base <full-N> --target <full-N+1> --out /tmp/inc --verify --tar
```

## 门禁（构建红的条件）

| 门禁 | 违规 | 结果 |
| --- | --- | --- |
| CI 云专属依赖守卫 | 未登记组件 / cloud_locked 组件入包 | `GuardViolationError`，指名组件+修复方向 |
| 模型 lineage 审计门槛 | 模型产物无审计记录或未过审 | `ModelGateError`，指名产物与缺失字段 |
| 文书包法务过审 | 无 `--legal-audit-record` 或记录不合规 | `TierGateError`，拒不出货 |
| 输入缺件 | 必需组件数据源缺席 | `TierGateError`（显式 `--allow-missing` 才降级并记 `missing_inputs`） |
| 空增量日 | 选定日期只有 flk manifest（emitted=0） | `BuildError`（不产出空 bundle；`--days` 形态会把这些日子记入 `empty_days`） |

## 数据三档

- `citation` 引用包（必选底座）：laws + law_catalog + law_chunks_v2 + guide_cases
- `corpus` 语料包：+ legal_documents（五源）+ contract_templates（SAMR）
- `judgment` 文书包：+ judgments_subset（**必须**法务过审记录；只做子集，绝不真打全量）

## 增量源实址：flk 法规每日增量（2026-10-05 实测探明）

- **产源**：`fd-law-data` 仓 `bin/flk-crawl.mjs`；K8s CronJob `flk-law-crawl`
  （`k8s/flk-crawl-cronjob.yaml`，schedule `30 2 * * *` = 02:30 Asia/Shanghai，
  namespace `scraw`，镜像 `100.64.0.8:30880/lawcraw_business/fd-law-data:v0.1.9`）。
- **落点**：RustFS `http://100.64.0.11:9000` bucket `laws`：
  - `incremental/YYYY/MM/flk-YYYY-MM-DD[-partNNN].jsonl` —— 当日新增/变更法规（列同 laws 表）
  - `incremental/YYYY/MM/flk-YYYY-MM-DD.manifest.json` —— counts/byCode/window/finishedAt
  - `incremental/state.json` / `incremental/backfill-state.json` —— crawler 私有水位状态
  - `incremental/backfill/…` —— 历史回填通道（组件名带 `backfill-` 前缀，与日更不混淆）
- **实测存量**（2026-10-05）：日更 JSONL 13 个 / 108.5 MiB，最新 2026-09-25；
  2026-09-30 起每日仅 manifest（`emitted: 0` = 无新增）——构建器如实记入 `empty_days`。
- **应用语义**：按 `(src_db, source_id)` 幂等 upsert（`bin/flk-load.mjs` 口径）；
  bundle 附逐组件 sha256 + flk 当日 manifest 原文，校验不过不应用。
- **离线等价**：同一 bundle 的离线介质形态；结构增量形态另见 `incremental` 子命令
  （`verify_offline_equivalence` 保证基座+增量 == 目标 full）。

## 分发端点（server/fd_lex_api/distribution/）

同一 `.jsonl.gz`/打包约定：`GET /api/distribution/manifests[/{bundle_id}]`、
`GET /api/distribution/packs/{bundle_id}`（Range/If-Range/HEAD 断点续传）、
`GET /api/distribution/packs/{bundle_id}/sha256?verify=1`（坏包 409 拒绝背书）、
`GET /api/distribution/catalog`（双宿主能力目录）。鉴权 v1 = 配置化 token
（`DISTRIBUTION_API_TOKEN`，fail-closed）；客户级凭据（wgk-）为后续项。

## 合同范本语料实址（2026-10-05 调查）

`scraw-law-contracts` 爬虫（仓库不在 finddata 工作树；bare 镜像
`/Users/chengsishi/finddata/attic/scraw-law-contracts.git`）由 `helm-law-scraw`
chart 以 CronJob 每日 02:00 部署（`values.yaml`：`lawContracts.schedule: "0 2 * * *"`，
镜像 `scraw-law-contracts:sha-b9a72ee`），产物落 SQLite `data/scraw.db` +
JSONL `output/items.jl`（PVC `…-law-contracts` 挂 `/app/data`、`/app/output`），
并上传 bucket `scraw-law-contracts`（chart 内 MinIO
`minio.minio.svc.cluster.local:9000`，凭据在 committed values 里是 `change-me`
占位、实际值走 local-only values，不在本工作树）。
可达性复验（2026-10-05）：RustFS `100.64.0.11` 全 bucket 列表无
`scraw-law-contracts`，且各 bucket 无任何 contract/SAMR 键；历史节点
`23.144.68.246:30900` TCP 超时。物件布局见 `openspec/specs/samr-contracts-source/spec.md`
（SQLite 表 `contracts`；对象键 `contracts/{type}/{id}.pdf|docx`，PDF=type2 / Word=type1）。
故构建器保留接口位
`lex_packer.sources.SamrContractSource`（`--contract-source` 本地 JSONL/SQLite，
或 `--source-dir` 下的 `contract_templates.jsonl`/`items.jl`/`scraw.db`），
缺席时需 `--allow-missing contract_templates` 并如实记入 manifest。
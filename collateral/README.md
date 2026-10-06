# fd-lex/collateral — 识律对外材料集

OpenSpec change：`lex-marketing-kit`（spec：`lex-collateral`）。本目录承载六件材料：

| 产物 | 路径 | 对象 |
|---|---|---|
| 数字口径表 | `numbers.md` | （前置，所有材料的数字唯一出处） |
| 业务一页纸 | `onepager-business/` → `pdf/lex-business-{zh,en}.pdf` | 法务总监/律所主任 |
| 架构与私有化一页纸 | `onepager-arch/` → `pdf/lex-arch-{zh,en}.pdf` | CTO/IT 负责人 |
| MCP 接入手册 | `../web/src/content/docs/{zh,en}/mcp-guide.md` | agent 构建者 |
| 合同智能使用手册 | `../web/src/content/docs/{zh,en}/contract-guide.md` | 法务从业者 |
| 公众号文章草稿 | `wechat/` | 公开获客（**仅草稿，不发布**） |

## 三条红线（全部材料的验收条件，见 spec `lex-collateral`）

1. **模型话术**：可说「平台内置自研法律模型」并引用已验证评测数字；不得承诺模型文件/适配器/量化产物随包交付；私有化模型表述必须带 v1.x 标注。
2. **数字溯源**：每个数字来自 `numbers.md`，材料中以行 ID 引用；表外数字=红。
3. **不虚构**：无价格数字（报价一律「按需报价/Contact us」）、无客户案例、无用量数据。

## 审计

```sh
python3 collateral/audit.py            # 扫描全部成品材料
python3 collateral/audit.py <file>     # 扫描单文件
```

检查项：数字↔口径表比对、模型交付承诺扫描、禁区（价格/案例/用量）扫描。

## PDF 生成

源 = `onepager-*/{zh,en}.html` + 共享 print CSS；headless Chromium 打印 A4：

```sh
# 依赖：本机 Chrome 或 Chromium（CJK 字体用系统字体栈 Noto Sans CJK SC / PingFang SC）
chromium --headless --disable-gpu --print-to-pdf=collateral/pdf/lex-business-zh.pdf \
  --no-pdf-header-footer collateral/onepager-business/zh.html
```

产物提交进仓（`pdf/`），由 fd-lex-web 构建时拷入对外路径（见 web 侧 README）。

## 公众号发布闸门

`wechat/` 下内容**仅草稿**。发布前置：①人工逐字确认；②lex-platform-v1 接客前提（sqlite→PG、计量消费链生产形态）解决。任何自动化流程不得将其发布至任何渠道。

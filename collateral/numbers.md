# 识律法律数字口径表

> 本表是全部识律对外材料（一页纸/手册/公众号草稿/官网文案）中**每个数字的唯一合法出处**。
> 材料引用数字时以行 ID 标注（如 `[N1]`）；材料中出现本表没有的数字 = 验收红。
> 刷新流程：任何材料修订前，先重跑本表取数并更新「取得日期」。

| ID | 指标 | 值 | 口径 | 来源 | 取得日期 |
|----|------|----|------|------|----------|
| N1 | 法律法规部数 | 32,296 | law_db.laws 行数（每标题一行去重口径，见 law-db-hygiene） | live：`psql law_db "select count(*) from laws"` | 2026-10-08 |
| N2 | 法条切片数 | 364,916（约 36.5 万） | law_db.law_chunks_v2 行数（语义检索向量库对应点数 364,801，2026-10-05 实测，随增量微幅浮动） | live 查询 + lex-platform-v1 task 1.2 | 2026-10-08 |
| N3 | 裁判文书行数 | 37,001,012（约 3,700 万） | wenshu_db.judgments 行计数 | live 查询 | 2026-10-08 |
| N4 | 裁判文书篇数 | 37,001,012（约 3,700 万） | judgments 按文档标识（doc_id）去重计数=行数（装载链去重后入库，doc_id 全表唯一）；**行数口径与篇数口径在此库上同值** | live：`select count(distinct doc_id) from judgments`；2026-10-08 改由主键唯一索引 judgments_pkey(doc_id) 佐证同值 | 2026-10-08 |
| N5 | 指导性案例 | 278 篇案例 + 15 条批次通知 | law_db.guide_cases 按 doc_type 分型（case 278 / notice 15；1–279 号缺 45） | live 查询 | 2026-10-08 |
| N6 | 权威语料总量 | 47,256 | law_db.legal_documents 合计（**含 rmfyalk 人民法院案例库 22,472**）：gov-rules 10,573 / mfa-treaty 7,408 / ccdi-supervisory 6,412 / rmfyalk 22,472 / party-regulation 391。旧值 30,249 是 rmfyalk 大批量装载之前的快照，已废止 | live 聚合查询（`docs/ROADMAP-modes.md:370` 实测） | 2026-10-19 |
| N7 | 法律检索 MCP 工具数 | 10 | lex 入口（lex.finddatatech.cloud/mcp → fd-legal-search-mcp，现网版本 4.0.11）tools/list 实测：law_search / law_read / law_semantic_search / law_reference / wenshu_search / wenshu_read / guide_cases_search / guide_cases_read / legal_docs_search / legal_docs_read | 现网 tools/list（wgk- 测试 key 全链） | 2026-10-07 |
| N8 | 评审准确率对照 | 10.9% → 84.4% | 自有评测集，通用基线模型 vs 法律领域模型 | docs/CUSTOMER-SERVICE-CATALOG.md §4.1（已核验） | 2026-09（目录口径） |
| N9 | 结构化输出合规率对照 | 38.5% → 100% | 同 N8 | 同 N8 | 2026-09 |
| N10 | 单条处理耗时对照 | 27.3s → 10.2s | 同 N8 | 同 N8 | 2026-09 |
| N11 | 起草质量 chrF 对照 | 19.5 → 51.0 | 同 N8；51.0 为均值（验证集 55 条，中位数 42.9；对外如需更稳健可标注样本量或用中位数——见目录内部备注 8） | 同 N8 | 2026-09 |
| N12 | 合同范本库 | 5,000+ 份 | 市监总局示范文本库，按《民法典》典型合同与实务分类法组织（最多六级分类） | CUSTOMER-SERVICE-CATALOG §2.10 | 2026-09 |
| N13 | 民法典典型合同类数 | 19 类 | 典型有名合同 | CUSTOMER-SERVICE-CATALOG §5.1 | 2026-09 |
| N14 | 合同智能 MCP 工具数 | 52 | LawBench 合同智能平台对外暴露的 MCP 工具数（生成/评测/检索类） | CUSTOMER-SERVICE-CATALOG §3/§5.1（代码实测口径） | 2026-09 |
| N15 | 文书全量数据卷 | 约 175GB | wenshu 文书全量（约 3,700 万篇 [N4]）的存储体积估计，用于私有化容量规划 | lex-platform-v1 proposal（数据三档分层） | 2026-10-05 |
| N16 | 额度预警档位 | 80% / 95% | 额度消耗至两档时触发预警（控制台留档展示；邮件投递通道另立项，现状为站内留档） | lex-platform-v1 task 5.3 | 2026-10-06 |

## 已知口径差异（材料写作注意）

- **裁判文书**：官网/目录旧口径 1,050 万～1,080 万篇是历史数字；库侧实测已到 **3,700 万**（N3/N4，2026-10-07）。新材料一律用 N3/N4 并带「截至」句式；官网 landing 文案更新属 lex-platform-v1 4.2 挂账项，不在本 change 范围。
- **法规部数**：旧目录 32,275 vs live 32,296（N1）——新材料用 N1。
- **法条切片**：向库点数（364,801，10-05）与表行数（364,916，10-07）存在微幅增量差；对外统一用 N2 表口径。

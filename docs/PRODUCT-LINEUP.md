# 识律产品条线 / Lex Product Lineup

> **单一真源**：`docs/product-lineup.json`。本文是它的人读投影；两者不一致由
> `scripts/check_product_lineup.py`（A7）判红。改口径先改真源，再改本文。
>
> 一句话：**成员是谁 → 能卖什么 → 怎么打包 → 装在哪**，四轴正交，不是四层树。

```
成员 members            良律(可售) / 哲思 / 实论 / 剧创 / 政心(孵化)
   |
   +-- 能力 capabilities（能单独报价 / 单独停 / 单独计量）
   |     search  法律检索      contract 合同智能    matter 案件
   |     esign   电子签署      models   自有法律模型
   |
   +-- 交付 delivery（不是能力，是拿到能力的方式）
   |     distribution 私有化交付包      access 接入与凭据
   |
   +-- 打包 suites（套件 = 若干能力行 + 技能层内容）
   |     lex-legal-research / lex-contract-review-cn / lex-litigation-docs
   |     lex-civil-nine-step / lex-criminal-defense / lex-labor
   |
   +-- 档位 tiers（私有化装哪些数据域）
         引用包 / 语料包 / 文书包
```

## 一、成员 / Members

家族页五张平级卡；成熟度只作卡内标注，不改变位置与入口方式。

| ID | 成员 | Member | 成熟度 | 入口 | 今天能卖什么 |
|---|---|---|---|---|---|
| `lianglu` | 良律 | LiangLü | 可售 live | `law.finddatatech.cloud`（产品面；平台面 `lex.` 见 §五） | 报价 · 客户组织 · 计量计费 · 控制台 · 市场套件 · 私有化交付 |
| `zhesi` | 哲思 | Zhesi | 孵化 incubation | `zhesi.finddatatech.cloud` | 无商业面；参照系检索服务在跑 |
| `shilun` | 实论 | Shilun | 孵化 incubation | `shilun.finddatatech.cloud` | 无商业面 |
| `juchuang` | 剧创 | Juchuang | 孵化 incubation | `juchuang.finddatatech.cloud` | 无商业面 |
| `zhengxin` | 政心 | Zhengxin | 孵化 incubation | `zhengxin.finddatatech.cloud` | 无商业面；政情读面在跑 |

**线页上另有两张卡不是家族成员**（能力面 / 数据面，不参与家族平级呈现）：
`zhiyantai`（知验台 · 多线共用评测与本体工作台）、`contract-templates`（合同示范文本数据服务）。

## 二、能力与交付 / Capabilities & Delivery

| 行 | 能力 | Capability | 云端 | 私有化 | 打包进套件 | 计量 |
|---|---|---|---|---|---|---|
| `search` | 法律检索 | Legal Search | 可用 | 可用 | 6 个套件全部 | 按次 |
| `contract` | 合同智能 | LawBench | 可用 | 可用 | 合同审查 | 按次 + 按行 |
| `matter` | 案件 | Matter | 可用 | 可用 | 平台直供（不打包） | 按次 |
| `esign` | 电子签署 | E-Signature | 内测 | 未开放 | 合同审查（附加） | 按份 |
| `models` | 自有法律模型 | Lex Models | 不可用 | 可用 | 内嵌于合同智能 | 不单独计量 |
| `distribution` | 私有化交付包 | Distribution | — | 可用 | 交付形态 | 按项目 |
| `access` | API / MCP 接入 | API & MCP | 可用 | 可用 | 统一入口 | 按次 + 按行 |

两条边界，写在这里免得每次口头解释：

- **`models` 不单独卖**：模型内嵌在合同智能与私有化交付里；云端不提供模型 API 或 GPU 推理入口（v1.x 规划），量化产物与评测报告不随交付包。
- **`esign` 是内测**：编排面（发起 → 相对方签 → 存证 → 出证）可用，签署腿是 stub；持牌服务商通道接通后开云端签署。

## 三、打包 / Suites

套件是**能力的打包单位**，不是独立产品层。技能层内容（分析流程、计算器、文书转换）写在 preset 里，不占能力行。

| 套件 | Suite | 打包的能力行 | 技能层内容 |
|---|---|---|---|
| `lex-legal-research` | 识律 · 法律检索套件 | `search` | 法条效力核查 · 类案报告 · 概念图解 · 五源语料 · 法规生命周期 |
| `lex-contract-review-cn` | 识律 · 合同审查套件 | `contract` `search` | 五张行业审查卡 · 修改前后对比表 |
| `lex-litigation-docs` | 识律 · 诉讼文书套件 | `search` | 起诉状 / 答辩状 / 证据目录 · 要素式格式转换 |
| `lex-civil-nine-step` | 识律 · 民事诉讼套件 | `search` | 九步分析：诉讼请求 → 请求权基础 → 抗辩 → 要件 → 检索 → 争点 |
| `lex-criminal-defense` | 识律 · 刑事辩护套件 | `search` | 总控流程 · 认罪认罚/不起诉论证 · 庭审发问提纲 |
| `lex-labor` | 识律 · 劳动法套件 | `search` | 劳动仲裁全流程 · 劳动补偿金 / 人身损害双计算器 |

**回答「合同审查怎么买」**：能力是 `contract`（+ `search`），打包是 `lex-contract-review-cn`，云上是控制台开通 + 按次/按行计量，私有化是交付包的 `corpus` 档。三句话一次说完，不必现场解释。

## 四、交付档位 / Tiers（仅私有化）

| 档位 | Tier | 包含数据域 | 必选 |
|---|---|---|---|
| `reference` | 引用包 | 法规库 · 法规目录 · 指导案例 | 是 |
| `corpus` | 语料包 | 五源语料 · 合同范本 | 否 |
| `judgments` | 文书包 | 裁判文书全量（约 175 GB，按项目法务过审） | 否 |

## 五、口径纪律 / Rules

1. **两个宿主面，不是一个域名的两个别名**：`law.finddatatech.cloud` = 产品门面（官网入口、SEO canonical 指这里）；`lex.finddatatech.cloud` = 家族与既有客户入口，同时承载控制台 / MCP / 签署人公网链——Logto 回跳与在途签署链钉在这里，不可改指。两面之间不做跳转（2026-10-30 裁决）。已知欠账：两面今天服务同一份前端产物，产品站与控制台的内容分化未做。
2. **对外数字只能出自 `collateral/numbers.md`**；废止值（旧法规部数、旧文书篇数）不得再出现在对外文案。
3. **未交付的不承诺**：孵化成员的卡片不出现价格、试用、SLA、上线时间；未交付能力不出现"随交付包"类表述。
4. **改口径的顺序**：真源 → 投影 → 跑门。反过来一定漂。

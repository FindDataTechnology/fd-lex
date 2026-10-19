# 识律 Lex · 法律数据智能试用套件

> 一页上手：用同一个账号，体验识律控制台、法律检索 API（MCP）与壹座智能体市场的识律套件。
> 本页所有数字均出自识律数字口径表（行 ID 见文末对照），无报价、无案例、无虚构。

---

## 识律是什么

识律（Lex）是寻数旗下法律数据智能平台：把**权威法律数据 + 自研法律模型 + 智能体应用**整合为一条可用、可核验、可私有化的产品线。

- **法规** 三万两千余部（去重口径）[N1]，法条切片约 36.5 万条，支持语义检索 [N2]
- **裁判文书** 约 3,700 万篇（截至 2026-10，常态化增量更新）[N3][N4]
- **指导性案例** 278 篇 + 批次通知 [N5]；**权威语料** 三万余件（规章/条约/监察法规等六分型）[N6]
- **合同范本** 5,000+ 份（市监总局示范文本库）[N12]
- 每条检索结果都带**数据溯源三件套**（来源库 / 稳定标识 / 数据时点），取不到的数据会明说，不会编造。
- 法律领域模型评测：评审准确率 10.9% → 84.4%，结构化输出合规率 38.5% → 100% [N8][N9]（自有评测集，随模型版本 v1.x 持续迭代）。

识律与**壹座**（寻数自研 AI 执行平台）天然一体：壹座负责把数据与技能编排成对话可用的智能体，识律负责供给可核验的法律数据。下面的第 3 步就是在壹座里体验。

---

## 你的试用账号

```
登录账号    lextrial
邮箱        lex-trial@findmail.com
密码        LexTrial#2026a
```

- 识律控制台：https://lex.finddatatech.cloud/console
- 壹座平台：https://platform.finddatatech.cloud
- **同一账号两面通用**（控制台 + 壹座市场），试用额度已内置，无需付费、无需绑卡。
- 已为你预铸一枚 API key（也可登录控制台自助铸造新的）：

```
wgk-5R581Q7ZrKzvXlpDNa8Np3B8u17afStutPj68L0mXFM
```

---

## 三步上手

### 第 1 步 · 控制台（约 5 分钟）

1. 打开 https://lex.finddatatech.cloud/console ，用上方账号登录。
2. **凭据**页：查看/铸造 `wgk-` 长命 API key（每枚 key 记录最近使用时间，可随时吊销）。
3. **用量 / 账单 / 额度**三页：实时查看调用次数与额度消耗，额度消耗至 80% / 95% 会触发站内预警留档 [N16]。

### 第 2 步 · 法律检索 API 直连（约 10 分钟，可选）

入口 `https://lex.finddatatech.cloud/mcp`（MCP Streamable HTTP），提供 10 个法律检索工具 [N7]：
法条（law_search / law_read / law_semantic_search / law_reference）、裁判文书（wenshu_search / wenshu_read）、指导案例（guide_cases_search / guide_cases_read）、权威语料（legal_docs_search / legal_docs_read）。

最快验证——把下面整段贴进终端（已带你的 key）：

```bash
KEY="wgk-5R581Q7ZrKzvXlpDNa8Np3B8u17afStutPj68L0mXFM"
URL="https://lex.finddatatech.cloud/mcp"
# 1) 握手，拿会话 ID
SID=$(curl -s -D - -o /dev/null -X POST $URL \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"trial","version":"1.0"}}}' \
  | grep -i mcp-session-id | tr -d '\r' | awk '{print $2}')
# 2) 查一条法条（注意参数名是 citation）
curl -s -X POST $URL -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"law_reference","arguments":{"citation":"劳动合同法第四十七条"}}}'
```

返回正文里即可看到条文原文与 `"provenance"` 溯源三件套。更完整的接入说明见识律官网「MCP 接入手册」。

### 第 3 步 · 壹座市场安装识律套件（约 10 分钟）

1. 打开 https://platform.finddatatech.cloud ，同一账号登录。
2. 进入「智能体」页的市场（/agents?tab=market），搜索 **识律**，六个套件任选安装：

| 套件 | 用一句话说 |
|---|---|
| 识律 · 法律检索套件 | 法条/案例/语料三件检索，快速找依据 |
| 识律 · 劳动法套件 | 劳动争议流程 + 经济补偿/人损双计算器 |
| 识律 · 民事诉讼套件 | 民事诉讼九步法全流程 |
| 识律 · 刑事辩护套件 | 罪名 exam、量刑情节与辩护思路组织 |
| 识律 · 合同审查套件 | 中文合同条款审查与风险提示 |
| 识律 · 诉讼文书套件 | 起诉状/答辩状等文书起草 |

3. 安装后对话里选对应包预设（如「法律检索」），直接发真问题，例如：
   - 「检索'民间借贷纠纷'相关的近期裁判文书，给三条带案号的」
   - 「劳动合同法第四十七条关于经济补偿是怎么规定的？」
   - 「我在广州工作三年被辞退，月工资一万二，算一下经济补偿」（计算器会取年鉴基数并标注来源与年份）

---

## 使用须知

- **数据可核验**：所有结果携带来源与数据时点；检索不命中或数据缺口会如实说明（例如省级社平工资缺项时计算器会走"中止"话术而非估算）。
- **文书检索技巧**：裁判文书按标题匹配，用宽词（如「民间借贷纠纷」）命中更好，窄组合词可换关键词重搜——套件内置了多组关键词换搜策略。
- **key 保管**：`wgk-` key 等同账户凭据，请存入密码管理器；可在控制台随时吊销重铸。
- **试用范围**：当前账号面向法律检索域（法规/文书/指导案例/语料）；商业供给面的扩展与私有化部署请与我们联系（Contact us，按需报价）。

---

## 数字口径对照

| 引用 | 口径表行 |
|---|---|
| [N1] 法规 32,296 部 | numbers.md N1 |
| [N2] 法条切片 364,916 | numbers.md N2 |
| [N3][N4] 裁判文书 37,001,012 行=篇 | numbers.md N3/N4 |
| [N5] 指导案例 278+15 | numbers.md N5 |
| [N6] 权威语料 30,249 件 | numbers.md N6 |
| [N7] 检索工具 10 个 | numbers.md N7 |
| [N8][N9] 模型评测对照 | numbers.md N8/N9 |
| [N12] 合同范本 5,000+ | numbers.md N12 |
| [N16] 预警档位 80%/95% | numbers.md N16 |

# 电子签厂商接入手册（change lex-contract-esignature，tasks 组 1/3.2）

> 现状：编排底座、状态机、适用性闸门、H5 签署面、出证包、按份计量已完成并有
> 离线测试（`ESIGN_PROVIDER=stub`）。**本手册是「凭证到位后的最后一段」**：
> 实现一家厂商 adapter → 过选型硬闸 → 沙箱全链验收。

## 一、需要拿到的凭证（三家任一家起步即可）

| 项 | 说明 |
|---|---|
| 企业实名 | 沙箱阶段个人账号通常可用；开通「本地签/出证」类接口与生产接入需企业实名（营业执照+法人信息） |
| AppId / AppSecret | 控制台创建应用后获得（沙箱环境一套、生产一套） |
| 沙箱域名 / 企业账号 ID | 各家叫法不同（沙箱环境域名、组织ID/企业ID） |
| **接口权限（需向厂商销售/技术支持明确提出）** | ①**本地签/摘要签**（合同正文不上传，仅上送 SHA256 摘要请求签名值）；②实名认证（三要素/人脸，沙箱提供模拟实名）；③出证报告/存证凭证获取 |

候选：e签宝（open.esign.cn，自助沙箱）、法大大（open.fadada.com）、契约锁
（open.qiyuesuo.com，沙箱账号+在线调试）、腾讯云电子签（微信实名底座，与
「相对方在微信里签」路线最契合，建议并列候选）。

## 二、凭证到位后的接线（代码面，约 15 分钟）

1. 在 `fd_lex_api/esignature/adapter.py` 新增厂商类，实现：

   ```python
   class XxxSignProvider:
       name = "xxx"
       def create_envelope(self, *, task_id, title, category, anchor_sha256, signers) -> str
       def verify_identity(self, *, envelope_ref, signer, claimed_name, credential) -> dict
       def sign(self, *, envelope_ref, signer, anchor_sha256, intent_method) -> dict
       def fetch_report(self, envelope_ref) -> dict
       def verify_signature(self, anchor_sha256, signature_value) -> bool
   ```

   **硬契约（D2）**：以上方法的入参只允许出现哈希与身份/元数据，**MUST NOT**
   传入合同字节；`verify_identity` 比对不符必须抛 `AdapterError(403, ...)`。

2. `get_adapter()` 白名单加入该值；配置 `ESIGN_PROVIDER=<name>` +
   `ESIGN_*_APPID/SECRET` 等环境变量（Secret 注入，不入 git）。

3. 厂商侧回调（若采用回调模式）落一个 `/internal/esignature/callback`
   端点：只接收签名结果/状态变更，落 `lex_esign_events` 与签署人状态。

## 三、选型硬闸验收（tasks 1.1，逐家做）

沙箱真跑一份测试合同，逐条打勾（**任一条不过=该家出局**）：

- [ ] 发起签署时厂商侧**只收到文件哈希**（抓包/控制台记录核对，不含合同正文）；
- [ ] 签署人完成「实名 + 意愿」后落签，`signature_value` 可被
      `verify_signature` 独立验真；
- [ ] 出证报告可下载，且报告内锚定哈希与本地锚定一致；
- [ ] 全流程完成后 `GET /api/console/esignature/tasks/{id}/evidence` 三件套
      （manifest/events/provider_report）内容对账通过，`/verify` 原件通过、
      一字改动不通过。

**全数厂商不过** → 停止实施、回桌重谈隐私口径（禁止静默降级为全文上云）。

## 四、通过后需要更新的清单

1. `tasks.md`：1.1/1.2/3.2 勾选；`design.md` D1 补记选定厂商与实测结论。
2. 生产 Secret 注入 `ESIGN_PROVIDER` 与厂商凭证 → 8.1 云端开通。
3. 私有化包：连接器配置面文档（客户自备厂商账号）→ 8.2。
4. 官网能力卡宿主标注（云=全能力；机房=需自备连接器）→ 8.3。
5. 微信真机走查（5.3）：公网域名 + 厂商真人实名通道。

## 五、法律条文核验（tasks 4.1，2026-10-29 已落地）

- **通道**：`http://cheap6.mesh.finddata.cn:31884/mcp`（fd-legal-search-mcp 4.1.1，
  MCP streamable HTTP；走 mesh 域名，裸 tailnet IP 不可达）。
- **已核验**：《中华人民共和国电子签名法》（law id 18881，status=**有效**，
  2019-04-23 版）第三条排除清单 + 第十三条可靠签名四要件 + 第十四条同等效力，
  逐字落在 `fd_lex_api/esignature/legal_basis.json`（含工具与 provenance 溯源）。
- **未收录**：人社部《电子劳动合同订立指引》——`law_search` / `legal_docs_search` /
  `law_semantic_search` 均 0 条命中，`labor_guideline.retrieved=false`；闸门因此
  **只引用已核验的第十三条**，对该指引只提名称并显式标注「未经核验」，不引用其条文。
  补齐方式：指引进入法规库后重跑脚本自动翻转该标记。
- **重跑方式**：`cd server && python scripts/verify_legal_basis.py --write`
  （重新核验并整体替换 legal_basis.json；检索不到或效力状态非「有效」时 FAIL 退出）。

> 旧口径（已过期）：曾计划由人工写入条文；实际做法已固化为可复跑脚本，
> 避免人工补写条文导致漂移。

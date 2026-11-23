# Alibaba 刊登独立验证报告（只读 + dry-run + 一次授权真实 op=1）

> 验证者：publish-verifier（独立于实现方）。工作区 `/Users/apple/Projects/alibaba-lister`。
> 报告时间：2026-11-22 01:16（Asia/Shanghai）；JSON 快照 `generated_at=2026-11-22T01:16:33+0800`。
> 数据以 `docs/publish-readiness.json` 为准；**在线商品数在验证期间持续变化**
> （2503 → 2717），草稿数稳定 28。
> 代码快照：`git HEAD 1452f6e` + 未提交工作区改动（alibaba-builder 并发修复中），
> 关键文件 sha256 前 16 位见 JSON `repo.source_sha256_16`。
>
> 安全边界：本验证只读接口 18 次调用（明细见 JSON `live_readonly.calls`）；真实写入仅 **1 次 op=1 草稿保存（用户明确授权一次，
> 由 Lead 执行，证据 `docs/alibaba-real-save-verification.json`）**；未调用任何保存/发布/重试/
> 删除接口，未调用付费 AI（AI 流水线只用桩 client + `use_ai=False` 预演），未打印/写盘任何
> Cookie 或 api_key（已对 JSON 做泄漏检查：none）。

---

## 一、结论（先看这里）

**直连 ICBU 链路已能完成「打开已有商品 → 预演校验 → 保存草稿（op=1）」，且在用户授权下
真实保存成功 1 次；但还不能据此认为「发布/审核已通」：op=2（提交审核/发布）、op=3（移入待
发布）、op=5（定时发布）没有任何真实写入证据，新建商品路径也未接通。**

| 链路 | 能否刊登 | 结论 |
|---|---|---|
| **直连 ICBU**（`integrations/alibaba.py` + `alibaba_payload.py` + `alibaba_publish.py` + `alibaba_pipeline.py`，UI 第 12 页「Alibaba 刊登」） | **部分可以** | 对**已存在的 Alibaba 商品**：读取/类目/属性/schema/校验/op=1 保存已跑通（真实写入 1 次 + 回读 0 差异）。UI 已暴露 op=1/3/2 + 预演 + 二次确认；op=5 未暴露。**不能新建商品；发布/待发布/定时未验证**。 |
| **SMT / publisher**（`services/publisher.py` → `integrations/dianxiaomi.py`，速卖通链路） | **不可以用于 Alibaba** | 保存打的是 `/api/smtProduct/add.json` + `smtEditSave.txt`（速卖通格式）；且 `store_id=9466975` 在 SMT 列表为空。Alibaba 平台若复用这套 12 页，发布队列为空，存在跨平台误写风险。 |

关键判定（`docs/publish-readiness.json → readiness_gate`）：
- 当前环境「配置正确 + 选好类目 + 商品数据完整」→ `ready_to_attempt_real_save = true`
  （7 项判据全绿，仅 `op2_publish_exposed=false` 属未验证项，不作为保存前提）。
- 但**商品数据普遍不完整**：9466975 的 28 个草稿里 **27 个 `categoryId` 是非数字脏值
  `"None"`**、13 个没有 `productSku`。这些商品即使代码正确也会被保存前校验拦下（或若绕过
  校验则提交无效类目）。真正「数据完整可直接保存」的样本目前只有少数几个，本次抽检的是
  商品 `220114598434958580`（泳池，类目 202107201）。

---

## 二、验证级别边界（严格区分，不外推）

| 级别 | 内容 | 证据 |
|---|---|---|
| **只读已验证** | ICBU 商品列表/详情（110 字段）、类目详情/必填属性/schema、草稿数据体检、列表分页修复后第 2 页确实返回第二页 | `docs/publish-readiness.json → live_readonly`；本报告 §三 |
| **dry-run 已验证（本地，不联网不花钱）** | `build_alibaba_save` + `validate_alibaba_save` 0 issues；`Publisher.run(dry_run=True)` 步骤语义；`alibaba_pipeline.run_alibaba_product(dry_run=True, use_ai=False)` 用桩 client 走完 9 步且 `save_called=false` | 本报告 §四；`docs/publish-readiness.json → live_readonly.dry_run / new_pipeline.dry_run_probe` |
| **真实写入已验证（仅 op=1，1 次，用户授权）** | 商品 220114598434958580 草稿保存：平台返回「您的产品编辑成功！」；回读 `changed_fields=0`、草稿总数 28→28、仍在草稿；10/10 步成功、0.6s | `docs/alibaba-real-save-verification.json`（Lead 执行）；本报告 §五 |
| **真实保存未验证** | **op=2 提交审核/发布、op=3 移入待发布、op=5 定时发布**、审核状态/失败原因拉取、新建 ICBU 商品 | 无任何真实调用证据；禁止用文档/常量推断 |

> 注意：op=1 保存成功只证明「编辑已有草稿不会破坏数据、接口与载荷格式正确」；
> Alibaba 的发布需要平台审核，op=2 的真实行为、失败语义、审核通过率**全部未知**。

---

## 三、只读核验证据（真实账号，2026-11-22 01:14 快照）

### 3.1 店铺与商品总量（ICBU `shopId=9466975`）

| 状态 | 数量 | 说明 |
|---|---|---|
| 草稿（`dxmState=draft`） | **28** | 验证期间稳定 |
| 在线（`dxmState=online`） | **2679** | **在变**（验证开始时 2503） |
| 已下架 / 发布失败 | 0 / 0 | 该店铺这两个桶为空 |
| 全量（不带状态） | 2725 | 快照时刻 |

- `alibabaIcbuShopInfoSync/list.json` 返回**不含** `shopInfoList`（只有 `freightTemplateList`/
  `productGroupList`），故 `AlibabaClient.shops()` 返回 `[]`；店铺以商品上的 `shopId` 为准，
  不影响刊登（`integrations/alibaba.py:92-96`，P2 只读接口缺陷）。
- 其余 3 个店铺 ID（9190736/9351129/8437964）在 ICBU 列表返回 0：它们是 SMT 速卖通店铺，
  不是 Alibaba 店铺。

### 3.2 草稿数据体检（全量 28 个，通过 pageNo 分页取全）

| 项 | 结果 |
|---|---|
| `categoryId` 为 `"None"`/非数字 | **27 / 28**（仅 220114598434958580 等 1 个合法） |
| 无 `productSku` | **13 / 28** |
| 价格模型 | ladder 14 / sku 14 |
| 结论 | 这些草稿直接保存会提交无效类目/无 SKU 报价；`validate_alibaba_save` 现已能拦截（需修复代码生效），但**必须先人工补类目/SKU**，软件无法自动猜 |

### 3.3 抽检商品 `220114598434958580`（数据完整样本）

- 详情 110 字段；状态 `draft`（`productStatusType=null`，可编辑）；类目 202107201；
  `fullCid=18,100005657,100005663,202107201`；`priceType=ladder_price`；
  主图 6 张、详情图 21 张、属性 11 条、`textDesc` 2108 字符；关键词
  `fiberglass pool, container pool, above ground pool`；阶梯价 `[{1, 4300.0}]`；
  贸易信息 MOQ=1 / 包装 300X200X150 / 重量 300 / 库存 10。
- 类目 202107201：**`isleaf=1`（末级）**、level 4、19 个属性、**required 5 项**
  （Place of Origin / Color / Material / Applicable People / Type 全部已存在）；
  schema 8 个模块（companyDesc/companyFaqDesc/companyImage/detailImage/saleType/scPrice/
  tariffsHsCode/textDesc）。

### 3.4 分页修复复核（我报告后被 Lead 修复）

- 旧代码 `integrations/alibaba.py` 的 `list_products` 用 `params["page"]`，ICBU 只认 `pageNo`：
  实测 `page=2` 返回 `pageNo=1` 的第一页（所有翻页都重复第一页）。
- 修复后实测：`list_products(2)` 首条 `220114598434958560`、原始 `pageNo=2` 首条同为
  `220114598434958560`，`pageNo=1` 首条 `220114598434958580` → **第 2 页正确**。
- 文档口径：`docs/ALIBABA_RULES.md §2` 写的参数是 `page/pageSize`，**以代码 `pageNo` 为准**
  （该文档本次未改；README/规则文档维护者请同步）。

---

## 四、dry-run 证据（本地，不保存、不调 AI）

1. **ICBU 载荷构建 + 校验**（商品 580，真实类目 required/schema）：
   `build_alibaba_save` → `validate_alibaba_save` **issues=0**；字段：op=1、categoryId/fullCid
   原样保留、关键词 3 个、主图 6、详情图 21、属性 11、textDesc 2108、MOQ/包装/重量齐全。
2. **Publisher.run(dry_run=True)**（用同一商品映射的通用模型）：
   `ok=true, preview=true`，步骤 `预演 → 标准化 → 价格计算 → 图片计划 → 图片方案 →
   保存到店小秘(dry-run)`；**未调用 dxm 保存、未下载图片、未调 AI**。
   注意：Publisher 是 SMT 语义，它的干跑**不校验 Alibaba 保存要求**（类目/关键词/属性/op），
   因此它显示「预演完成」不能作为 Alibaba 可保存的证据（见 §六-P1）。
3. **新 AI→Alibaba 流水线 dry-run**（`alibaba_pipeline.run_alibaba_product`，桩 client +
   `use_ai=False` + `dry_run=True`）：9 步全绿（连接→详情→类目规则→关键词→校验→违禁词→
   预演→完成），`save_called=false`、`saved=false`，载荷校验 0 issues。这是**本地语义验证**，
   不使用付费 AI，不代表真实 AI 文案质量。

---

## 五、真实写入验证：op=1（用户授权一次，Lead 执行）

证据文件：`docs/alibaba-real-save-verification.json`（生成 2026-11-22T01:13:13+0800）。

- 代码路径：`run_alibaba_product(product_id="220114598434958580", dry_run=False, op="1",
  use_ai=False)`（新流水线，`src/aeclone/services/alibaba_pipeline.py:240`）。
- 平台返回：`{"code":0,"msg":"Successful","data":{"msg":"您的产品编辑成功！",
  "productId":"220114598434958580"}}`。
- 保存前后回读：subject / keywords / categoryId / fullCid / priceType / bulkDiscountPrices /
  mainImage / textDesc **全部一致，changed_fields={}**；`textDesc` sha 一致；
  草稿总数 28→28；保存后仍是草稿。
- 流水线 10 步全绿、耗时 0.6s；保存前有授权校验（`core.guard.require`）。

**结论边界**：只证明「已有草稿 op=1 保存」可用。**未证明**：op=2/3/5、审核、新建商品、
批量、AI 文案实际写入（本次 use_ai=False）。

---

## 六、代码对照 `docs/ALIBABA_RULES.md` 的审查（含 file:line 与状态）

状态说明：**已修复（工作区）** = 我报告后实现方已在未提交工作区修复且我复验过本地行为；
真实平台层行为仍以「真实写入」边界为准。

| # | 规则（文档） | 代码现状 | 状态 / 风险 |
|---|---|---|---|
| 1 | §3 保存 = `POST /api/alibabaIcbuProduct/add.json`，multipart `file=zip(alibabaSave.txt)`，`op` 区分动作 | `integrations/alibaba.py:238-247`（`SAVE_NAME="alibabaSave.txt"`，endpoint、form op、zip 内 JSON op 均正确）；离线抓包断言见 `tests/test_publish_readiness.py` | ✅ 契约一致（离线验证 + 真实 op=1 间接证明） |
| 2 | §3 op 1/2/3/5 | `alibaba_publish.py:77-86` 常量 = 1/2/3/5；UI 传 op（`ui/app.py:9112-9119` 下拉 1/3/2，`:9307` AI 保存）；op=5 无 UI | ⚠️ op=1 真实已验证；**op=2/3/5 未验证**；op=5 未暴露 |
| 3 | §4 价格模型与类目选项匹配；FOB 不写 `tariffsHsCode` | `alibaba_payload.py:278-346`：ladder/sku/fob 分支；FOB 清空 `bulkDiscountPrices` 并删除 `tariffsHsCode`（`:308-318`, `:363-366`） | ✅ 已修复（工作区），本地探测 `known_gaps.fob_residual_fields=false` |
| 4 | §4 `wholesaleTrade.min_order_quantity` = 第一档起点 | `alibaba_payload.py:315-330`：MOQ 取配置（默认 1），**不与阶梯首档联动** | ⚠️ P2：配置 MOQ 与阶梯首档不一致时可能被平台判不匹配（真实 op=1 样本两者都是 1，未触发） |
| 5 | §5 只能选末级类目；`fullCid` 末段=categoryId | `alibaba_payload.py:213-249`（`resolve_category`：overrides > cfg > 商品原值，注入 categoryId/fullCid）；`validate_alibaba_save:499-514` 校验空/None/非数字类目与 fullCid 末段一致；`alibaba_publish.py:62-75` 校验/保存用同一类目 | ✅ 已修复（工作区）：修复前实测「cfg 选 100010756、payload 仍 202107201」；现在注入生效；`known_gaps.category_selection_applied=false` |
| 6 | §6 必填属性 / 关键词 / 主图 / MOQ / 包装重量 / textDesc / detailImage 硬校验 | `validate_alibaba_save:494-580` 覆盖；`sku_price` 现在用 `sku_summary` 检查 productSku 维度与有效 SKU（`alibaba_payload.py:459-492`, `:543-557`） | ✅ 已修复（工作区）；真实草稿 0 issues |
| 7 | §6 关键词 3~5 且保留商品原词 | `keywords_effective`（`alibaba_payload.py:133-155`）优先保留商品原 keywords，不再用标题词覆盖 | ✅ 已修复（工作区）：抽检商品保存前后关键词一致（changed_fields=0） |
| 8 | §2 列表参数 | 修复前 `list_products` 用 `page`（ICBU 要 `pageNo`），第 2 页永远返回第 1 页 | ✅ 已修复（Lead，`integrations/alibaba.py:81-90`）；本报告 §3.4 已复验 |
| 9 | §3 保存包必须带类目、属性、价格、图片、详情 | UI 新页 `ui/app.py:9423-9434` 复用 `_alibaba_cards`；有「预演」`ai_preview:9253`、二次确认 `ai_save:9307`/`do_save:9137`；Cookie 顺序 alibaba_cookies→manual_cookies→Chrome（`alibaba_publish.py:45-60`） | ✅ 已修复（工作区）：修复前 make_client 读不到 manual_cookies、import 不存在的 `read_chrome_cookies`，UI 保存直接报「没有 Cookie」 |
| 10 | 海关属性 | Alibaba 走 `wholesaleTrade.tariffsHsCode`；`customs_metadata.py`/`sku_recovery.py` 是速卖通 `hsCode` 版本修复，**不参与 ICBU 链路**。抽检商品 ladder 载荷仍带 `tariffsHsCode=[{toCountry:"US",hsCode:""}]`（空 HS），validate 不校验 schema 的 `tariffsHsCode` | ⚠️ P2 残留：空 HS 行当前真实 op=1 可保存，但 **发布/审核是否接受未知**；发布前应按类目补 HS 或按规则确认可空 |
| 11 | 模板依赖 | ICBU 链路**不依赖模板**（`prepare_save` 只读商品+cfg）；`template_product_id` 为空只卡 SMT `publisher._save_via_api`（`publisher.py:2326-2337`） | ⚠️ P1（仅 SMT 路径）：配置空模板时 Publisher 真实保存直接中止 |
| 12 | 图片 / AI 处理 | ICBU 链路直接使用商品现有 `mainImage`/`detailImage`/`textDesc`；`Publisher` 的 AI/白底/营销图流水线**不写入 ICBU 载荷**；新 `alibaba_pipeline` 已接入 AI 文案（标题/详情/关键词），但本次只用 `use_ai=False` 验证（未调用付费 AI） | ⚠️ P1：图片 AI 处理仍未接 ICBU；AI 文案路径存在但未做真实 AI 写入验证 |
| 13 | 新建商品 | `save_to_alibaba`/`run_alibaba_product` 均要求已有 ICBU 商品 id；无「创建新商品」路径 | ⚠️ P1：本次目标（刊登新品）**尚不支持** |
| 14 | 审核状态 / 失败原因 | 无 ICBU 审核状态拉取代码；只有发布失败列表（SMT 语义） | ⚠️ P1：Alibaba 审核闭环未实现 |
| 15 | 跨平台隔离 | `publisher.py` 保存走 `DianxiaomiClient.save_product_via_api` → `ENDPOINTS["product_save"]="/api/smtProduct/add.json"` + `smtEditSave.txt`（`dianxiaomi.py:45,703-720`）；Alibaba 平台页曾经复用 Publisher 页面 | ⚠️ P1 架构风险：Alibaba 平台下若误用发布队列，会打速卖通接口；需按平台路由客户端，禁止跨平台写 |

---

## 七、阻塞项排序（当前环境）

| 排序 | 类别 | 级别 | 阻塞项 | 证据 / 影响 | 最小动作 |
|---|---|---|---|---|---|
| 1 | 配置 | ✅ 已解除 | Cookie 读取（原 P0）：读不到 `manual_cookies`、Chrome 兜底函数名不存在 | 修复后 `make_client` 可用（本机读 Chrome 或 manual_cookies） | 保持 `manual_cookies` 可用即可 |
| 2 | 店铺 | ✅ 无阻塞 | ICBU 店铺 9466975 有 28 草稿/2717 在线（快照）；`shops()` 接口返回空但只影响「读店铺列表」 | §3.1；`integrations/alibaba.py:92-96` | 店铺仍以 `shopId=9466975` 为准 |
| 3 | 模板 | ⚠️ 仅 SMT | `template_product_id` 为空 → `publisher.py` 无法真实保存 | `publisher.py:2335-2337` | ICBU 不需要；SMT 路径需选模板 |
| 4 | **商品数据** | **P0（就具体商品）** | **27/28 草稿 `categoryId="None"`；13/28 无 `productSku`（其中 14 个 `sku_price`）** | §3.2；校验现已能拦截，但软件无法自动补 | 上线前先批量体检 → 人工补类目/SKU；只对数据完整商品执行保存 |
| 5 | 代码缺陷 | ⚠️ 剩余 P1/P2 | 新建商品未支持；op=2/3/5 未验证；op=5 无 UI；AI 图片未接 ICBU；审核闭环缺失；`shops()` 空；ladder 空 HS 行；Publisher 跨平台风险 | §六 | 见 §九 |

> 当前真正会挡住一次「保存草稿」的只剩「商品数据」：配置/Cookie/类目/载荷均已具备；
> 但「刊登新品」与「发布上线」仍分别缺代码路径和真实验证。

---

## 八、复现方式（可重复、只读）

```bash
# 1) 只读 + 本地 dry-run，输出 docs/publish-readiness.json
.venv/bin/python tools/verify_publish_readiness.py
# 2) 完全离线（不联网，只用内置合成商品；适合 CI）
.venv/bin/python tools/verify_publish_readiness.py --offline --out /tmp/readiness.json
# 3) 离线契约测试（可被 verify_regressions.py 收录；exit!=0 即失败）
AURORA_CONFIG_DIR=$(mktemp -d) .venv/bin/python tests/test_publish_readiness.py
```

- `tools/verify_publish_readiness.py`：只调用只读接口 + 本地构建/干跑；真实写入 0 次；
  JSON 内含 `config / contract / live_readonly / dry_run / real_save_evidence / known_gaps /
  readiness_gate / verification_levels / blockers`。
- `tests/test_publish_readiness.py`：12 个离线用例（op 映射、zip 传输、字段契约、类目注入、
  FOB 清理、必填校验、dry-run 不写店铺、无模板不读详情、Cookie 缺失拒绝、报告无密钥、
  AST 禁止写入类调用）。当前 **12/12 PASS**。

---

## 九、下一步建议（按优先级）

1. **商品数据修复**（P0）：对 27 个 `categoryId="None"` 草稿先跑只读体检（用本工具），
   人工补末级类目与 SKU；不要用批量保存去试。
2. **op=2/3/5 的真实验证**（需再次明确授权，每次只挑 1 个**新建的测试草稿**）：
   - 先 op=3（保存并移入待发布）→ 只读确认状态变化；
   - 再 op=2（提交审核/发布）→ 观察返回 msg、审核状态、失败原因（需补审核状态拉取代码）；
   - op=5 定时发布：先做 UI 暴露 + 调度登记，再小流量验证；
   - 每次保存前留 before/after 回读（复用 `docs/alibaba-real-save-verification.json` 格式）。
3. **新建商品路径**：Alibaba 新品来源（采集草稿？）→ 目标店铺的 op=1 创建或「复制模板」，
   当前无代码路径，需先确认 add.json 是否支持空 id 创建（未验证，勿猜）。
4. **跨平台隔离**：Alibaba 平台禁止走 `Publisher`/SMT 端点；发布队列按 `active_platform`
   路由到 ICBU 客户端，避免向速卖通店铺写入。
5. **文档更正**：`docs/ALIBABA_RULES.md §2` 列表参数改为 `pageNo`（代码为准）；
   §8「当前实现状态」可补：op=1 真实保存已验证、op=2/3/5 未验证、新建未支持。
6. **P2 收尾**：`shops()` 解析真实响应结构；ladder 模式空 HS 行在发布前按类目补齐；
   `validate_alibaba_save` 增补 schema `tariffsHsCode`/`companyDesc` 条件校验。

---

## 十、产物与快照

| 产物 | 说明 |
|---|---|
| `docs/publish-readiness.json` | 机器可读报告（本报告数字来源；含 `generated_at`、代码 sha、每次只读调用耗时） |
| `docs/alibaba-real-save-verification.json` | 真实 op=1 保存证据（Lead 执行，用户授权一次） |
| `docs/alibaba-pipeline-verification.json` | 新 AI→Alibaba 流水线 dry-run 证据（实现方并发产出，只读引用） |
| `tools/verify_publish_readiness.py` | 可重复只读/dry-run 核验脚本 |
| `tests/test_publish_readiness.py` | 12 项离线回归（当前 PASS） |

**并发说明**：本报告核验期间 alibaba-builder 正在同一工作区修复（未提交）。§六中「已修复
（工作区）」均指本快照时点的未提交代码；最终合入状态以 Lead/task-3 为准。若源码随后变化，
重跑 `tools/verify_publish_readiness.py` 即可得到新快照；`docs/publish-readiness.json` 是唯一
权威数字来源。

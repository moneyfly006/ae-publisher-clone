# TikTok Shop 刊登 · 验证报告

> 状态：**task-5 端到端验收交付**（2026-11-22，Asia/Shanghai）。
> 契约与验收标准：`docs/TIKTOK_DESIGN.md`（§0 实测事实、§1 真实接口、§6 验证标准、§7 安全、§8 口径）。
> 验证范围：`integrations/tiktok.py`、`services/tiktok_{payload,prompts,pipeline,publish}.py`、
> 页面 14 UI、契约/UI/回归/按钮审计/截图；**真实写入回环按账号事实如实标注为未验证**。

---

## 0. 结论速览（全部为本次实测）

| 项目 | 结论 | 证据 |
|---|---|---|
| 真实账号只读核验（主证据，tiktok-client） | **PASS 18/18** | `docs/tiktok-readonly-verification.json`（generated_at `2026-11-22T02:44:42+08:00`） |
| 独立复核（tiktok-verify，不 import 后端） | **PASS 18/18** | `docs/tiktok-verification-live.json`（generated_at `2026-11-22T02:55:28+08:00`） |
| TikTok 测试（6 文件 **345** 用例） | **345/345 OK**（`AURORA_TEST=1`） | `tests/test_tiktok_{client,payload,prompts,pipeline,contract,ui_page}.py` |
| 离线契约测试 | **55/55 OK**（报告写入后全跑，0 skip） | `tests/test_tiktok_contract.py` |
| 全量回归 `verify_regressions` | **66/66 PASS**（含 6 个 TikTok 模块；不带 `AURORA_TEST` 运行） | `docs/regression-verification.json` |
| 离线回归 `test_offline` | **674 tests OK**（0 skip；已接入 6 个 TikTok 模块） | `tools/test_offline.py` |
| UI 回归 | `test_ui_redesign` **20/20 OK**；`test_platform_ui_filter` PASS；`test_amazon_platform_ui_filter` **10/10 OK** | 对应测试输出 |
| 页面 14 UI 审计 | **14/14 PASS**；页面 14 回调 **53 个 / ERROR 0** | `tools/verify_tiktok_ui.py`（机器证据 `build/verification/tiktok-ui-audit.json`） |
| 全平台按钮审计 | **21 页 / 627 回调，ERROR 0**；TikTok 4 页 **114 回调 0 错**（页面 14 共 53 回调） | `docs/ui-button-audit.json` |
| 截图 | 3 张 PNG 已生成且互不重复（§3.2） | `docs/design/tiktok-listing-page*.png` |
| 真实 AI 接入（Lead 产出） | 5 个真实 API 用例（GB/DE 标题、GB 描述、US 五点/关键词，`qwen-plus`，未记录 key）；**不涉及商品写入** | `docs/tiktok-ai-verification.json` |
| 编译 | `compileall -q src` **exit 0** | — |
| **真实写入回环（§6.4）** | **未验证：账号未绑定 TikTok 店铺**；工具已实现且确认门实测拒绝（`exit 2`，零写请求） | §5 |

> 重要口径：本报告真实完成的是「真实接口**只读**验证 + 离线/沙箱验证 + 真实 AI 文本生成验证」。
> **未发生任何 TikTok 商品真实写入**；账号绑定 TikTok 店铺后才能跑写回环（§5）。

---

## 1. 真实账号只读核验（主证据 + 独立复核）

### 1.1 账号事实（两份证据一致）

- 账号 `QINGHEMAOYI` / puid `2728813`；已授权店铺 2 个：`QingHe`(amazon)、`手工订单`(our)；
  **TikTok 店铺 = 0**（账号未绑定 TikTok Shop，与 `docs/TIKTOK_DESIGN.md` §0 一致）。
- TikTok 采集箱/待发布/在线计数均为 **0**（账号无 TikTok 商品）。
- Cookie 只进内存：两份报告均 `content_logged=false` / `content_in_report=false`，
  控制台仅打印前 6 位脱敏片段；报告内无 `JSESSIONID`/`dxm_*` 等 Cookie 值。

### 1.2 检查项与结果（18/18）

| # | 检查 | 结果 | 关键实测 |
|---|---|---|---|
| 1 | `userIn` 登录 / 账号 | PASS | account=QINGHEMAOYI, puid=2728813 |
| 2 | TikTok 店铺真相 | PASS | TikTok 店铺 0 个（期望 0）；其它店铺 QingHe/手工订单 |
| 3 | `getL7ShopIdAndSite` | PASS | 23 站点，顺序与契约完全一致；`shopId=ALL` |
| 4 | `pageList` 采集箱(draft) | PASS | code=0 totalSize=0 rows=0 |
| 5 | `pageList` 待发布(offline) | PASS | code=0 totalSize=0 |
| 6 | `pageList` 在线(online) | PASS | code=0 totalSize=0 |
| 7 | `getOfflineCounts` | PASS | code=0 draftNum=0 / waitPublish=0 / publishFail=0，字段齐全 |
| 8 | `getOnlineCounts` | PASS | code=0 data={} |
| 9 | `tiktokCategory/list` GB 顶层 | PASS | **30 条**；节点字段无异常；首条 605196 Automotive & Motorcycle |
| 10 | 类目下钻（`categoryParentId`） | PASS | 605196 → Car Electronics(929928, 子 9) → Alarm Systems & Security(930568, 子 8)，叶子 isLeaf=1 |
| 11 | `searchCategory` `category="phone"` | PASS | **73 条**；证明关键词字段名是 `category`（非 keyword/searchValue） |
| 12 | `attributeList` 叶子 `type=3` | PASS | 叶子 930568 返回 **10 条**属性；对照不带 `type` 仅 1 条 → 证明必须 `type=3` |
| 13 | `getLimitBySite` 站点限额 | PASS | 15 个契约字段全部一致（标题 25–255、描述 ≤10000、主图 1–9、库存 ≤99999、价格 0.01–1.8e8） |
| 14 | `listWarehouse` | PASS | code=0 仓库 0 个（无店铺可为空） |
| 15 | `multiShop/tiktok/shopList` | PASS | `siteShop=null`、`popShop=null`（无店铺时均为 null） |
| 16 | `historyList` 最近类目 | PASS | code=0 0 条（无店铺可为空） |
| 17 | 详情端点 | PASS | `getByProductId` code=0 data=null；`gettiktokProduct` 无真实 id 返回 404（预期，工具按 best-effort 记录） |
| 18 | 保存传输格式自检（空 payload） | PASS | HTTP 200、`code=-1`「产品未选择店铺或店铺无效！」；ZIP 条目 `tiktokSave.txt`、multipart `file` 在前；**空 payload 数据不落库** |

### 1.3 主证据 vs 独立复核

- **主证据**：`tools/verify_tiktok_read.py`（tiktok-client 交付）→
  `docs/tiktok-readonly-verification.json`，18/18 PASS；19 次请求，0 拦截；
  唯一触达写入口的是文档化的「空 payload 传输自检」1 次（服务端店铺校验即拒绝）。
- **独立复核**：`tools/verify_tiktok.py`（本任务交付）→ `docs/tiktok-verification-live.json`，
  **不 import 任何 `aeclone` 业务后端**（自有 requests 会话 + 只读白名单 + 写入口硬拦截），
  18/18 PASS；19 次请求、4 次缓存命中、0 拦截、`write_performed=false`。
- **冲突处理**：两份证据结论一致，无冲突；实现差异（独立复核主详情端点 404 best-effort、
  属性检查自行下钻叶子）已记录在各自 JSON 的 `limitations`/`detail` 中，结论以真实响应为准。

---

## 2. 离线契约测试（55/55 OK）

`tests/test_tiktok_contract.py`（不联网、不发真实写）覆盖 5 组契约：

1. **设计契约（文档即契约）**：51 个 `SAVE_KEYS`（保序）；op 语义 1/3/2+`AS_DRAFT`/5；
   `tiktokSave.txt`+ZIP+multipart(`file`→`op`[→`saveMode`])；23 个 L7 站点与顺序；
   24 站点 `SITE_META`；§1.6 限额数值；类目参数（`crossBorder=1`、`categoryParentId`、
   `searchCategory` 字段名 `category`、`attributeList type=3`）；账号与「未绑定 TikTok 店铺」事实；
   §3.4 的 26 个 overrides 唯一键名；§7 安全边界。
2. **后端接口契约**：`build_tiktok_payload` 为「详情原样透传（保留未知 camelCase 键）+ 补齐
   SAVE_KEYS + 删除 `variationBos` + overrides 映射」，因此只断言**包含** 51 键（空详情时补齐
   顺序 == SAVE_KEYS），不断言键集合恰好相等；`validate_tiktok_payload` 的
   `limits/site/is_global/for_publish` 全部 keyword-only，`for_publish=True` 才阻断非 US
   五点/关键词；`apply_site` 切站点清空类目/仓库；`price_stock_overrides` 多变种保护；
   pipeline/publish 签名与默认值（`dry_run=True`、`confirm_write=False`）。
3. **传输契约**：独立实现 payload→ZIP→multipart 往返（含四字节边界、非 ASCII）；
   假 requests session 断言客户端字段顺序 `file→op→saveMode`、写接口**零自动重试**、
   店铺错误转 `TikTokNotReady`（中文可操作提示）；类目/限额请求参数字段名精确断言。
4. **防回退**：`tools/verify_tiktok.py` 无后端 import（AST 判定）、无写入口完整字面量、
   `ALLOWED_ENDPOINTS` 全为只读 `.json`、`--write-roundtrip` 必须 `--confirm-write`
   （确认门位于写函数之前）、默认输出为独立复核报告；`button_audit.py`/`test_offline.py`/
   `verify_regressions.py` 已接入 TikTok。
5. **证据结构**：主只读证据/独立复核证据/最终报告口径的结构与诚实性断言（含报告不得夸大）。

其余 TikTok 测试：client **70** / payload **69** / prompts **57** / pipeline **77** /
ui_page **17**，全部 OK；其中 pipeline 覆盖「缺 `confirm_write` 不写」「`forced_preview`
不写」「注入 client 必须复用」「op/saveMode 正确」等安全边界。

---

## 3. UI 验收（页面 14 + 全平台回归）

### 3.1 页面 14 沙箱审计（`tools/verify_tiktok_ui.py`，14/14 PASS）

- 平台注册：`TIKTOK.implemented=True`，页面集合 `[0, 14, 10, 11]`；导航 14 号落点正确。
- 页面结构与速卖通一致：`page_header + workspace_tabs`，五个分栏
  「连接与商品 / 类目与属性 / 文案与图片 / 价格与物流 / 保存与诊断」，
  内含 8 张卡片（连接与店铺/站点、采集箱商品、类目与属性、文案与 AI、图片与视频、
  价格/库存/物流、预演与保存、诊断），卡片标题不再带圈号；**不展示**营销图/定时发布/EU 资质/模板商品/批量管线。
- 关键控件 23/23：店铺与站点分离下拉、无店铺绑定引导、站点限额（25–255 / 10000 / 1–9 /
  99999）、采集箱三态、类目树/搜索/最近使用、标题/描述/五点/关键词计数、
  价格/库存/SKU/重量/长宽高/单位/仓库、保存动作下拉、预演/保存（预演模式）/保存包、诊断按钮。
- UI `_overrides()` 26 键 == 后端 `OVERRIDE_KEYS`（AST 精确取 TikTok 页，避开 Amazon 同名函数）。
- **回调遍历 53 个 / ERROR 0**；沙箱禁用线程与网络，回调状态为 DEFERRED（语义同
  `button_audit` 的沙箱语义，**非异常**）。
- 写安全：`AURORA_TEST`/`AURORA_NO_SAVE` 与缺 `confirm_write` 时 `save_to_tiktok` 均拒绝；
  AURORA_TEST 下「保存（预演模式）」**不调用 pipeline** 即返回并显示拒绝提示；
  唯一写入口 `.save_product(` 外部调用方仅 `services/tiktok_publish.py`，UI 无直接 HTTP。

### 3.2 截图（3 张，沙箱 Flet Web + Playwright/Chrome）

| 文件 | 内容 | 大小 | sha256 |
|---|---|---|---|
| `docs/design/tiktok-listing-page.png` | 顶部：连接/店铺站点、采集箱、类目、文案与 AI（可见计数） | 178,314 B | `406da79c6470347…` |
| `docs/design/tiktok-listing-page-2-save.png` | 图片视频、价格/库存/物流、预演与保存（保存按钮为「保存（预演模式）」） | 116,965 B | `0e2e9d7a…` |
| `docs/design/tiktok-listing-page-3-diagnose.png` | 预演/保存 + ⑧ 诊断卡片（未绑定店铺可操作引导） | 39,774 B | `` |

三张截图内容互不重复（脚本断言唯一哈希=3）；截图仅为沙箱静态快照，不代表真实数据渲染。

### 3.3 全平台按钮审计（`docs/ui-button-audit.json`）

```
21 页 / 627 个回调: {'OK': 401, 'DEFERRED': 203, 'NATIVE': 8, 'EXTERNAL': 15, 'ERROR': 0}
TikTok 4 页 114 回调: 75 OK / 35 DEFERRED / 4 EXTERNAL / 0 ERROR
页面 14: 53 回调 = 42 OK / 11 DEFERRED / 0 ERROR
```

DEFERRED = 回调内启动后台线程（沙箱不执行）；EXTERNAL = 打开文件/浏览器等预期系统动作；
**均非异常，ERROR=0**。其它平台（Alibaba/速卖通/Amazon）页面集合与裁剪行为未因 TikTok 改变：
`test_ui_redesign` 20/20、`test_platform_ui_filter` PASS、`test_amazon_platform_ui_filter` 10/10。

---

## 4. 真实 AI 接入证据（Lead 产出，独立于商品写入）

`docs/tiktok-ai-verification.json`（generated_at `2026-11-22T02:57:08+08:00`）：

| 用例 | 结果 |
|---|---|
| GB 标题 | 137 字符英文标题 |
| DE 标题 | 163 字符德文标题（站点语言生效） |
| GB 描述 | 1,813 字符 HTML（`<p>/<ul><li>` 白名单标签） |
| US 五点 | 5 条（`keyProductFeatures`） |
| US 关键词 | 15 个（`searchTerms`） |

- 模型 `qwen-plus`，`api_key_logged=false`（未记录/未打印 Key）。
- 口径：这是**真实 AI API 文本生成**证据，用于证明 AI 接入与站点语言/条数约束可用；
  它**不涉及商品保存/发布**，不能作为「真实写入已完成」的依据。AI 结果仍需人工复核 +
  本地 `validate_tiktok_payload` 二次校验。

---

## 5. 真实写入回环（§6.4）：**未验证（账号未绑定 TikTok 店铺）**

账号事实：`QINGHEMAOYI` 未绑定 TikTok Shop（TikTok 店铺 = 0）→ 服务端对任何真实保存返回
「产品未选择店铺或店铺无效！」。因此本次验收**没有执行任何 TikTok 真实写入**，
§6.4 的写回环（建测试草稿 → op=1 原样回写 → overrides 编辑 → 回读 → 删除）
**未验证、未执行**。

已交付的写回环工具（`tools/verify_tiktok.py`）：

- 默认 **dry-run 只读**；`--write-roundtrip` 必须同时 `--confirm-write`，否则在读取 Cookie /
  发起任何请求前直接拒绝（实测：`--write-roundtrip` 无确认 → `exit 2`，
  `write_performed=false`，零写请求）。
- 写回环预检先读 `userIn` 店铺：**TikTok 店铺为空即 SKIP** 并给出可操作指引，不发起写请求。
- 写接口零自动重试；只在 `write_roundtrip()` 内部临时开启 `write_mode`；其余只读 API 仍硬拦截。

绑定 TikTok 店铺后由 **Lead** 执行（唯一授权执行者）：

```bash
.venv/bin/python tools/verify_tiktok.py --write-roundtrip --confirm-write \
    --product-id <采集箱草稿 id>
```

> 说明：脚本会先读源商品详情、以 `AURORA-TEST-<时间>` 标题做 op=1 原样回写与回读，
> 再用 overrides 改标题/描述并回读；默认删除本次新建的测试草稿（`--keep-test-draft` 可保留）。
> 该链路真实验证后，才能把「真实写入回环」标记为已验证。

---

## 6. 限制与剩余风险

1. **真实写入回环未验证**（账号未绑定 TikTok 店铺）：这是账号前提限制，不是代码结论；
   报告与工具口径均不得表述为真实写入已通过。绑定店铺后按 §5 一条命令补验。
2. 账号无 TikTok 商品：详情/变种只能证明端点可调用与空数据行为；页面 14 的「载入详情 →
   hydrate 表单 → 保存包预览」在真实商品上的表现未验证（目前由 fake client/离线测试覆盖）。
3. 店铺维度未验证：`shopSiteList`/`marketplaceIds` 真实映射、仓库列表真实数据、
   站点×店铺限额差异（当前无店铺，`listWarehouse` 为空、限额取契约默认）。
4. 保存传输自检只证明 ZIP/multipart/字段被服务端解析到「店铺校验」阶段，
   **不能证明真实保存成功**；空 payload 不落库。
5. AI 证据为真实文本生成，不是商品写入；AI 文案合规仍依赖本地校验 + 人工复核，
   违禁词接口本轮只证明可调用（仅提示、不阻断）。
6. UI 截图与回调遍历为沙箱环境（网络 mock、线程/文件选择器禁用）：
   DEFERRED 是沙箱语义，不代表真实链路可用；真实数据渲染由只读核验间接覆盖。
7. **环境事件（非 TikTok 代码缺陷）**：验收期间 `assets/preset_defaults.json` 的
   `active_platform` 被外部导出工具写成 `amazon`，导致 `test_ui_redesign` 5 项失败；
   Lead 已改回 `alibaba` 并复跑 20/20。建议 `tools/export_preset.py` 增加「打包默认
   `active_platform` 必须为 alibaba」的守护，避免再发。
8. 只读证据有 Cookie 时效性：Cookie 过期后需重跑主证据与独立复核；本轮两份证据均为
   2026-11-22 同一窗口快照。
9. 频控：只读请求 19 次（独立复核含 4 次缓存命中）；遇 `code=2005` 仅重试一次，
   不改变「只读」语义。

---

## 7. P0 / P1 / P2

- **P0：无**（在「账号未绑定 TikTok 店铺」的既定前提下，只读/离线/UI/安全边界全部通过；
  真实写入回环未验证已如实标注，未虚报）。
- **P1：无**（按钮审计 ERROR 0；回归 66/66；离线 674 tests 全绿；契约 55/55）。
- **P2（备注，不阻塞）**：
  1. 绑定 TikTok 店铺后由 Lead 跑一次 `--write-roundtrip --confirm-write` 补真实回环。
  2. 补真实商品详情/变种回读与店铺站点映射的真实验证（需店铺 + 采集箱商品）。
  3. AI 文案草稿发布前人工复核（标题 137/163 字符、描述 1813 字符均为草稿态，价格/库存
     等仍按业务填写）。
  4. `export_preset.py` 增加默认平台守护（见 §6.7）。
  5. `verify_tiktok.py` 只读部分与主证据为两套独立实现，后续维护需同步契约变化（契约测试
     会同时守护两套脚本的静态安全边界）。

---

## 8. 复现命令

```bash
# 1) TikTok 测试（6 文件 345 用例；ui_page/contract 建议 AURORA_TEST=1）
for f in tests/test_tiktok_client.py tests/test_tiktok_payload.py \
         tests/test_tiktok_prompts.py tests/test_tiktok_pipeline.py \
         tests/test_tiktok_contract.py; do .venv/bin/python "$f"; done
AURORA_TEST=1 .venv/bin/python tests/test_tiktok_ui_page.py

# 2) 真实账号只读核验（主证据 + 独立复核，需 .tmp-probe/cookies.txt）
.venv/bin/python tools/verify_tiktok_read.py     # 主证据 → docs/tiktok-readonly-verification.json
.venv/bin/python tools/verify_tiktok.py          # 独立复核 → docs/tiktok-verification-live.json

# 3) 页面 14 审计 + 回调遍历 + 3 张截图
AURORA_TEST=1 .venv/bin/python tools/verify_tiktok_ui.py

# 4) 全平台按钮审计（含 TikTok 4 页）
AURORA_TEST=1 .venv/bin/python tools/button_audit.py --json docs/ui-button-audit.json

# 5) 回归（verify_regressions 不要带 AURORA_TEST；内部对 amazon/tiktok 模块自动注入）
.venv/bin/python tools/verify_regressions.py
.venv/bin/python tools/test_offline.py
.venv/bin/python -m compileall -q src

# 6) UI 回归
.venv/bin/python tests/test_ui_redesign.py
.venv/bin/python tests/test_platform_ui_filter.py
.venv/bin/python tests/test_amazon_platform_ui_filter.py

# 7) 写回环确认门自检（不读 Cookie、不写平台；应 exit 2）
.venv/bin/python tools/verify_tiktok.py --write-roundtrip --output /tmp/guard.json; echo $?

# 8) 绑定 TikTok 店铺后（仅 Lead 执行，真实写）：
# .venv/bin/python tools/verify_tiktok.py --write-roundtrip --confirm-write --product-id <id>
```

---

## 9. 证据文件

| 文件 | 说明 |
|---|---|
| `docs/tiktok-readonly-verification.json` | 主只读证据（tiktok-client）：18/18 PASS |
| `docs/tiktok-verification-live.json` | 独立复核（tiktok-verify）：18/18 PASS，不覆盖主证据 |
| `docs/tiktok-ai-verification.json` | 真实 AI API 文本生成证据（Lead 产出，非商品写入） |
| `docs/regression-verification.json` | 全量回归 64 模块（含 6 个 TikTok 模块） |
| `docs/ui-button-audit.json` | 21 页 / 627 回调审计（TikTok 114 回调，ERROR 0） |
| `docs/design/tiktok-listing-page.png` | 页面 14 顶部截图（178,314 B） |
| `docs/design/tiktok-listing-page-2-save.png` | 页面 14 预演/保存截图（116,965 B） |
| `docs/design/tiktok-listing-page-3-diagnose.png` | 页面 14 诊断截图（39,774 B） |
| `build/verification/tiktok-ui-audit.json` | UI 审计机器证据（14/14 + 53 回调 ERROR 0；build 已 gitignore） |
| `tools/verify_tiktok.py` | 独立只读复核 + 写回环脚本（默认 dry-run，写需 `--confirm-write`） |
| `tools/verify_tiktok_ui.py` | 页面 14 控件审计 + 回调遍历 + 截图 |
| `tests/test_tiktok_contract.py` | 离线契约/传输/防回退/证据结构测试（55 用例） |
| `tools/button_audit.py`、`tools/test_offline.py`、`tools/verify_regressions.py` | 已接入 TikTok 用例/模块 |


---

## 10. 最终集成加固（task-5 之后、Lead 合稿时）

1. **打包预设泄漏个人状态/凭据（真实回归 + 安全事件）**：`assets/preset_defaults.json` 曾被
   `tools/export_preset.py` 用本机 `config.json` 导出，带入了
   `active_platform="amazon"`、`alibaba_price_type_explicit="1"`、**`amazon_cookies`（968 字符真实
   Cookie）**、`amazon_shop_id` 等个人数据，导致 `test_ui_redesign`（默认平台）与
   `test_alibaba_payload`（价格模型被静默改成阶梯价）回归失败，并构成 Cookie 泄漏风险。修复：
   - 预设恢复 `active_platform="alibaba"`，移除 `*_cookies`/`*_shop_id`/`alibaba_price_type_explicit`；
   - `dist/` 下两份旧打包产物中的同名预设同步清洗；
   - `tools/export_preset.py` 改为**键名规则**排除：`*_cookies`/`*_cookie`/`*_token`/`*_secret`/
     `*_password`/`*_shop_id`/`*_store_id`/`*_store_name` 及 `license_*`、`api_key` 等；
   - `tests/test_preset.py` 新增 4 条硬断言：默认平台 alibaba、不得含显式定价标记、
     不得含任何 Cookie/token/店铺 ID 键、预设值不得含 Cookie 内容（JSESSIONID/dxm_*）。
2. **页面 14 文案/测试对齐**：连接按钮统一为「测试连接」（与模块内提示、验证脚本、截图一致），
   并把「后端缺失时的连接失败提示」断言改为包含匹配（兼容「检测连接失败：…」文案）。
3. 并发写入保护：本轮工作区存在并行编辑（app.py/tests 被另一进程同时改动），
   最终状态以本报告复跑结果为准：TikTok 345/345、离线 660、回归 66/66、按钮审计 ERROR 0。


---

## 附：左侧导航拆页（2026-11-22）

- TikTok 不再只有「TikTok 刊登」一个页面；左侧导航现为：
  概览 / 连接与店铺 / 采集箱商品 / 类目与属性 / 价格·库存·物流 / 文案与 AI / 图片与视频 /
  保存与发布 / 任务与日志 / 设置。
- 14 号完整工作台（5 Tab）保留为内部隐藏路由，供旧深链与全量 UI 审计使用。
- 页面号：22–28 为功能页（`navigation.Destination` 同名条目）；`App._p_tiktok_section` 每页返回
  `page_header + 当前商品/站点/店铺摘要 + 对应卡片`。
- 回归：`test_tiktok_ui_page` 18/18、`test_platform_ui_filter` PASS、`verify_tiktok_ui` 14/14。

# Amazon 刊登 · 验证报告（终版）

> 状态：**终版**（task-4 端到端验收，2026-11-22）。契约与验收标准：
> `docs/AMAZON_DESIGN.md`（§1 真实接口、§6 校验、§7 安全、§8 验收）。
> 真实写由 **Lead** 在用户授权范围内执行（写 #1 往返、写 #2 AI 文案）；
> 本报告所有验证脚本保持只读或沙箱 mock，不产生额外写操作，结论不夸大。

---

## 0. 结论速览（全部实测）

| 项目 | 结论 | 证据 |
|---|---|---|
| 真实账号只读核验 | **PASS 18/18** | `docs/amazon-readonly-verification.json`（generated_at `2026-11-22T02:13:07+08:00`） |
| Amazon 测试（8 文件 194 用例） | **194/194 OK**（`AURORA_TEST=1`） | `tests/test_amazon_*.py` |
| 离线契约测试 | **27/27 OK** | `tests/test_amazon_contract.py` |
| 平台隔离审计（task-6 独立实现） | **79/79 PASS**（含 ⌘F 修复后复验；隐藏过头=0 / 隐藏不足=0） | `docs/amazon-platform-isolation.json` |
| 全量回归（verify_regressions） | **58/58 模块 PASS** | `docs/regression-verification.json` |
| 离线回归（test_offline） | **257 tests OK** | `tools/test_offline.py`（已接入 8 个 Amazon 模块） |
| 编译 | `compileall -q src` **exit 0** | — |
| 全平台按钮审计 | **17 页 / 457 回调，ERROR 0**；Amazon 4 页 105 回调 0 错（task-7 隐藏后减少 3 个） | `docs/ui-button-audit.json` |
| UI/后端交叉审计 | **9/9 PASS** | `tools/verify_amazon_ui.py`（9 项断言） |
| 13 页按钮遍历 + 截图 | **27/27 按钮无异常**；截图已更新（顶部 + 价格/预演） | `docs/design/amazon-listing-page.png` 等 3 张（§4.1） |
| 真实写入（Lead 执行） | 证据记录 **writes=3**（#1 往返 / #2 AI 编辑 / #3 恢复），均 `code=0`；另有 1 次未授权 teammate 误写（见 §3.3） | `docs/amazon-write-verification.json`（runs=6） |
| 写后店铺计数 | 采集箱 **16** / 待发布 **5** / 在线 **142** 不变 | Lead 复核 + 只读核验一致 |

---

## 1. 真实账号只读核验（独立实现）

脚本：`tools/verify_amazon.py`（requests 自实现，**不 import 后端模块**）。

- Cookie：`--cookie-file` → `DXM_COOKIE_PATH` → `.tmp-probe/cookies.txt`；控制台仅前 6 位脱敏，
  报告 `cookie.content_logged=false`，**不写任何 Cookie 内容**。
- 只读：14 端点白名单；唯一写入口以拼接常量 + 请求守卫断言拦截；无任何写开关。
  本次 `request_count=21`、`cache_hits=1`、`blocked_attempts=[]`、`write_endpoints_never_called=true`。
- 账号 `QINGHEMAOYI` / puid `2728813`。

| # | 检查 | 结果 | 关键字段 |
|---|---|---|---|
| 1 | `userIn` 登录 / 店铺 | PASS | account=QINGHEMAOYI, puid=2728813, 店铺 QingHe, merchantId=A26OV9RTCAYJMS, 默认站点 ATVPDKIKX0DER |
| 2 | `getCountryByShopId` 站点解析 | PASS | US/USD/ATVPDKIKX0DER(en_US)、CA/CAD/A2EUQ1WTGCTBG2(en_CA)、MX/MXN/A1AM78C64UM0Y8(es_MX)、BR/BRL/A2Q3Y263D00KWC(pt_BR) |
| 3 | `pageList` 采集箱(draft) | PASS | totalSize=16（rows=18，含变种行） |
| 4 | `pageList` 待发布(offline) | PASS | totalSize=5 |
| 5 | `pageList` 在线(online) | PASS | totalSize=142 |
| 6 | `leftStat` | PASS | waitPublish=5, publishFail=21 |
| 7 | `editSp` 详情 | PASS | site=CA，**164 字段**，bulletPoint 10 段（`±`），description HTML，productAttr 30 键 |
| 8 | `getProductAttrById` | PASS | data 为 JSON 字符串，30 键（含 is_assembly_required 等） |
| 9 | 类目树逐级（内部 id） | PASS | 顶层 17 → 12 → 15 → 3；子节点 `parentId` == 请求内部 id |
| 10 | `getCategoryBySiteNodePath` | PASS | 路径 `6299024011/6256963011/6256964011/13553005011` → Backhoes(反铲)；非法路径 `1/2/3` → data=null |
| 11 | `listHistory` | PASS | 最近使用类目 5 条 |
| 12 | `gerProductTypes`（站点代码 CA） | PASS | 1604 个，含 OUTDOOR_LIVING |
| 13 | `gerProductTypes`（传 marketPlaceId） | PASS | 返回空数组（契约 §1.5 注意项） |
| 14 | `currency` | PASS | CAD |
| 15 | `logistics` | PASS | 1 个模板（legacy-template-id / Migrated Template） |
| 16 | `brand` | PASS | 端点正常，返回空数组（可为空） |
| 17 | `amazonCheckBannedWord` | PASS | code=0，data=null（契约允许；仅提示不阻断） |
| 18 | `productTypePredictCatesByItemName` | PASS | itemName="Backhoe Loader" → productType=NON_RIDING_TOY_VEHICLE |

> 证据文件为 `docs/amazon-readonly-verification.json`；`amazon_edit.json` 抓包的 `editSp`
> 字段数、`amazonSave_payload.json` 的 39 键结构均与真实接口一致（契约测试结构化断言）。

---

## 2. 离线契约测试（27/27 OK）

`tests/test_amazon_contract.py`：

1. **设计契约**：39 个 SAVE_KEYS（保序）、op `1/3/2/5/6` 语义、`±` 卖点约定、
   ZIP(`amazonSave.txt`) + multipart（`file` 在前、`op` 在后）、SITE_META 四核心站点、
   §6 校验规则、§7 安全边界、账号/计数事实（16/5/142、leftStat 5/21）。
2. **独立传输实现**：自实现 payload→ZIP→multipart，`zipfile` 解压往返；字段顺序；非 ASCII；
   假 requests session 验证传输形状（不联网不写）；真实抓包 `save_capture2.json` 结构断言。
3. **只读脚本防回退**：`tools/verify_amazon.py` 无写入口完整字面量、无后端 import、无写开关；
   `ALLOWED_ENDPOINTS` 全为只读 `.json` 端点。
4. **证据结构**：只读报告 + 写证据；兼容 `save_response` / `save_result` 两种记录格式，
   写 #2 断言 title / 5 bullets / search_terms / description_changed 回读一致。

---

## 3. 真实写入证据（Lead 执行；验证脚本保持只读）

证据：`docs/amazon-write-verification.json`（generated_at `2026-11-22T02:07:16`，
`summary={"runs":5,"writes":2,"last_ok":true}`）。商品 `220114595363182310`，站点 CA。

### 3.1 写 #1：`op=1` 原样往返（2026-11-22T01:57:56）

- payload **39 键保序**（与契约一致），`save_response.code=0`、`data=220114595363182310`；
- 写前快照 ↔ 写后回读 **`changed_count=0`**（内容零变更），采集箱 16 不变。

### 3.2 写 #2：`op=1` AI 文案编辑（2026-11-22T02:07:14）

- 路径：AI 文案由 `amazon_pipeline.ai_generate_all` 生成（CA/英语：标题 152 字符、5 条卖点、
  描述 2672 字符 HTML、Search Terms 235 字节），作为 `overrides` 传入
  `run_amazon_product(..., dry_run=False, use_ai=False, op=1) → save_to_amazon`（唯一写收口）；
- 预演先行：`preview_ok=true`、`preview_issues=[]`；warnings 仅 5 条草稿提醒
  （标题 152 字符、描述 2672 字符、库存 0、价格 0、品牌 Generic）——均非阻断；
- 写入结果：`save_ok=true`、`save_result.code=0`、`data=220114595363182310`；
- **写后回读一致**：`title` 与 AI 标题完全一致（152 字符）、5 条卖点一致、
  `search_terms` 一致（235 字节）、`description` 已变更（2672 字符）；
  `checks={title:true, bullets:true, bullets_count:5, search_terms:true,
  description_nonempty:true, description_changed:true}`；
- 写后店铺计数复核：采集箱 **16** / 待发布 **5** / 在线 **142**（与写前一致）。

> 安全边界：真实写只发生在 Lead 的授权脚本；`tools/verify_amazon.py` 与
> `tools/verify_amazon_ui.py` 不含写开关，前者由契约测试静态防回退；
> `AURORA_TEST` / `AURORA_NO_SAVE` 下 `save_to_amazon` 实测直接拒绝（见 §4.3）。

### 3.3 写 #3：恢复（2026-11-22T02:25，Lead 执行）

- 事故：02:07:28 出现一次**未经 Lead 授权**的真实 op=1 写入（执行者 teammate `amazon-core`
  的仓库外一次性脚本；根因是脚本在 `patch(make_client)` 作用域之外调用
  `run_amazon_product(dry_run=False, client=fake)`，pipeline 内部又新建了真实客户端）。
  它用测试 fixture 文案覆盖了写 #2 的 AI 文案（title 67 字符 / 5 条占位式卖点 / desc 88 字符）。
- 处置：Lead 用写 #2 的同一份 AI 文案重新 op=1 保存恢复，回读 title 152 字符 / 5 卖点 /
  Search Terms 235 B / description 2672 字符全部一致；采集箱仍 16、待发布 5、在线 142。
  证据同文件 `runs=6 writes=3`，本 run `note=restore after unauthorized fixture overwrite ...`。
- 加固（已落实）：`save_to_amazon(..., client=...)` 必须复用注入客户端；
  `run_amazon_product(dry_run=False)` 必须显式 `confirm_write=True`，否则直接返回失败且不调用保存；
  UI 保存路径传入 `confirm_write=True`，单测覆盖「缺 confirm_write 不写」。
- 结论：**平台真实写入共 4 次**（写 #1 往返、写 #2 AI 编辑、1 次未授权 teammate 写入、写 #3 恢复）；
  Lead 授权的写入为 3 次。商品最终内容为 AI 文案（已恢复），采集箱/待发布/在线计数不变。

---

## 4. UI 与交叉审计

### 4.1 13 页与截图

- `src/aeclone/platforms.py`：`PageItem(13, "Amazon 刊登", "刊登", ...)`；`_switch` 落点 `_page_idx=13`。
- `tools/amazon_ui_shots.py`（`AURORA_TEST=1` + `ui_sandbox`）：网络 mock、线程/文件选择器禁用；
  **27/27 按钮 on_click 无异常**；NOT_READY 分支保留（页面未实现/分发表未接时打印 NOT_READY 退出 0）。
- 截图（task-6 02:45 最新代码重拍，使用 Chrome `--headless=new` 修复中文方框问题）：
  `docs/design/amazon-listing-page.png`（274,162 B，sha256 `6d5a75e2…`：页面顶部：连接/采集箱/类目/标题与五点/AI）、
  `docs/design/amazon-listing-page-2-price-preview.png`（263,971 B，sha256 `8efa9363…`：价格/库存/物流/品牌/SKU + 预演/保存）、
  `docs/design/amazon-listing-page-3-save.png`（265,636 B，页面底部）。
  最新 UI 已包含「＋添加卖点」「当前 5/10 行」「卖点 n/700」计数、侧栏「去 Amazon 刊登」。
- 本轮 UI 修复：`_switch_platform` 后同步侧栏启动按钮文案（Amazon/Alibaba → 「去…刊登」）；
  价格/物流卡片 `wrap=True` Row 内的 `expand` 子控件在 CanvasKit 下会被撑成超大空白块，
  已改为固定宽度下拉（截图不再有大块空白）。

### 4.2 全平台按钮审计

`tools/button_audit.py` 已加入 Amazon 用例 (0, 13, 10, 11)：

```
17 页 / 460 个回调: {'OK': 300, 'DEFERRED': 140, 'NATIVE': 8, 'EXTERNAL': 12, 'ERROR': 0}
Amazon 4 页 114 回调 = 78 OK / 32 DEFERRED / 4 EXTERNAL / 0 ERROR
Amazon 13 页 52 回调 = 45 OK / 7 DEFERRED / 0 ERROR
```

> DEFERRED = 回调内启动后台线程（沙箱不执行线程）；EXTERNAL = 打开文件/浏览器/外部进程等
> 预期系统动作（如「打开日志文件」「购买/充值 API」）——**均非异常**；ERROR=0。
> 证据：`docs/ui-button-audit.json`。

### 4.3 交叉审计（`tools/verify_amazon_ui.py`，9/9 PASS）

1. **契约 §3 签名**：`run_amazon_product / prepare_save / ai_generate_field / ai_generate_all /
   build_amazon_payload / validate_amazon_payload / build_warnings / apply_site /
   make_client / save_to_amazon` 全部存在，关键参数名/default/keyword-only 兼容。
2. **AmazonClient 方法齐全**：17 个方法存在；`save_product(payload, op)`。
3. **overrides 17 键 == `amazon_payload.OVERRIDE_KEYS`**（AST 提取 UI `_overrides()`，
   顺序与内容完全一致）。
4. **唯一写入口**：Amazon 相关模块中 `.save_product(` 的外部调用方只有
   `services/amazon_publish.py`；写入口路径字面量只允许出现在
   `integrations/amazon.py`（AmazonClient 自身，含旧兼容别名）。
5. **UI 无直接 HTTP**：UI/平台文件无 `requests.`、无写端点字面量、无 `save_product(`；
   UI 只经 `_amazon_backend()` → `pipeline.run_amazon_product / prepare_save`。
6. **预演/验收护栏**：`AURORA_TEST` 与 `AURORA_NO_SAVE` 下 `save_to_amazon` 均实测抛
   RuntimeError「已拒绝真实保存」。
7. **UI 运行期断言**：预演模式点「保存（预演模式）」在调用 pipeline 之前返回，
   状态区显示「已拒绝真实保存」（patch pipeline 为会抛错的哨兵，未被调用）。
8. **13 页关键控件（16/16）**：店铺/站点 Dropdown、卖点 1–5 输入、「＋添加卖点」、
   预演/保存按钮、AI 生成标题/五点/描述/关键词/一键生成全部、
   n/200 · n/700×5 · n/2000 · n/250 bytes 计数。

### 4.4 功能清单（对照契约 §4.5，UI 测试覆盖）

| 功能 | 验证 |
|---|---|
| 读取本机 Cookie / 测试连接 | `test_connect_list_load_detail_and_category`；沙箱按钮遍历无异常 |
| 刷新 / 上一页 / 下一页 / 空状态 | 同上 + `test_empty_product_list_has_actionable_empty_state` |
| 状态切换 采集箱/待发布/在线 | 同上；按钮审计无错 |
| 商品行「编辑」载入详情 | `test_connect_list_load_detail_and_category` |
| 类目顶层/逐级/最近使用/路径校验 | 同上（fake client 全链路） |
| AI 生成标题/五点/描述/关键词/一键生成 | `test_ai_generation_needs_switch_then_fills_fields` |
| 10 条卖点保留 | `test_ten_bullets_are_preserved_on_save` |
| 预演不写数据 | `test_preview_never_writes_and_save_is_rejected_in_preview_mode` |
| 真实保存二次确认 + 走后端 | `test_real_save_flow_uses_confirm_and_backend` |
| 概览「去 Amazon 刊登」跳 13 | `test_overview_uses_amazon_counts_and_jumps_to_13` |
| Amazon 平台 SMT 管线守卫 | `test_amazon_platform_guards_block_smt_pipeline` |
| 后端缺失不崩溃 | `test_page_and_callbacks_survive_missing_backend` |
| 明暗主题 | `test_page_13_renders_in_dark_and_light_themes` |

### 4.5 平台隔离审计（task-6 独立实现）

工具：`tools/verify_amazon_platform_isolation.py`（**独立**用 `ui_sandbox.build_app/sandbox`
渲染 + 控件树 walk（继承祖先 visible），不 import 新测试断言；`AURORA_TEST=1`、网络禁用、
临时配置目录）。证据：`docs/amazon-platform-isolation.json`（generated_at `2026-11-22T02:46:33+08:00`，
summary **79/79 PASS**，隐藏过头=[] / 隐藏不足=[]）。

| 区块 | 结果 | 关键证据 |
|---|---|---|
| Amazon 消失项（15 项） | **15/15 PASS** | 顶部栏「选择店铺」hidden；概览无「选择商品/今日任务/改前后对比/配置冲突行+检查按钮/图片服务 tile」；page10 无「停止任务/已编辑记录/定时发布 tab」；page11 无「图片与模型 tab/ffmpeg/副标题图片服务/实例说明任务队列×定时发布/恢复图床」；首次引导不弹 |
| Amazon 保留项（26 项） | **26/26 PASS** | 13 页六张卡 + 店铺/站点下拉 + 5 卖点 + ＋添加卖点 + 预演/保存 + AI×5 + n/700；概览「成本/用量、准备状态、API 服务、授权状态」；侧栏 tooltip 去 SMT；page10 运行日志；page11 3 个设置 tab（未隐藏过头） |
| AliExpress 保留项（16 项） | **16/16 PASS** | 选择店铺/选择商品/今日任务/改前后对比/配置冲突/图片服务 tile；page10 定时发布 tab + 停止任务 + 已编辑记录；page11 4 tab + ffmpeg + 图片服务副标题 + 任务队列说明 + 恢复图床；首次引导仍弹；**12 页全部渲染** |
| Alibaba（6 项） | **6/6 PASS** | 无 SMT 价格页(6)/变种页(7)、12 号刊登页在、概览/12 页渲染、选择店铺保留、page10 无定时发布 tab（scheduled_publish=False） |
| Shopee（3 项） | **3/3 PASS** | 页面集合 {0,1,2,3,5,8,10,11} 未被 Amazon 改动；所有页面渲染不崩溃 |
| TikTok（2 项） | **2/2 PASS** | 声明页面渲染不崩溃（TikTok 由外部进程并发开发 14 号页，不计入 Amazon 结论）；Amazon 页面集合仍为 0/13/10/11 |
| 隐藏不足扫描（9 项） | **9/9 PASS** | nav 仅 4 入口；直切 1–9 号页全部重定向到 0；旧 start_page=8 重定向；8 个 SMT 搜索词不落 SMT 页；⌘R→13、⌘L→10、**⌘F→13（不再打开 SMT 搜索弹窗，Lead 修复后复验）**；`_start/_run_pipeline/_toggle_run/_publish_one/_retry_publish_failed/_run_scheduled` 全部拦截；**105 个 Amazon 可见控件逐一点击无 SMT 跳转/弹窗** |
| 截图（2 项） | **2/2 PASS** | `amazon-listing-page.png` / `amazon-listing-page-2-price-preview.png` 已更新 |

> task-7 的 5 项残留收尾（概览配置冲突行+按钮、图片服务 tile、设置副标题、实例说明、
> 恢复图床）在 Amazon 均 **PASS**；AliExpress 对应项仍全部保留（16/16），未误伤。
> 「隐藏过头」清单为空（Amazon 13 页与概览关键功能全部保留）；
> 「隐藏不足」清单为空：审计发现的唯一残留（⌘F SMT 搜索弹窗）已由 Lead 修复，
> 并新增回归 `test_amazon_ctrl_f_does_not_open_smt_search`（本工具复验 PASS）。

---

## 5. 限制（如实列出）

1. 只读核验不覆盖写链路；写证据由 Lead 单独产出（`docs/amazon-write-verification.json`）。
   期间发生过 1 次 teammate 未授权真实写（已恢复并加固确认门，见 §3.3），如实记录。
2. 违禁词接口本轮 `data=null`：只证明端点可调用，**不能证明词库命中规则**。
3. 产品类型预测 best-effort：长标题返回 `code=-1`，本轮用短词验证可用路径。
4. 品牌列表为空（未做品牌备案），空数组视为端点正常。
5. AI 文案为草稿态：标题 152 字符（>150 警告）、描述 2672 字符（>2000 建议；schema 硬上限
   20000 字节）、库存/价格为 0——均为 warnings，不阻断草稿保存；发布前需按业务补价/补库存。
6. UI 沙箱使用 fake client / mock 网络，证明「可渲染、可点击、不崩溃、不真实写」；
   真实数据渲染由只读核验 + 真实写回读间接覆盖；截图为静态快照。
7. `tests/test_amazon_ui_page.py` / `test_amazon_platform_ui_filter.py` 按文件顶部说明需在
   `AURORA_TEST=1` 下运行；`tools/verify_regressions.py` / `tools/test_offline.py` 已对
   Amazon 模块自动注入该环境变量。
8. 平台隔离审计为沙箱静态/运行时 UI 断言：证明「该平台看不到/点不到 SMT 入口」，
   不覆盖真实运行时的系统托盘等所有外部输入。键盘 ⌘R/⌘L/⌘F 已覆盖并验证；
   ⌘F 残留由本审计发现后已由 Lead 修复 + 回归用例固化。
9. 截图使用 Chrome `--headless=new`；旧 headless 在当前 Chrome 版本会把中文渲染成方框，
   已修复截图链路（`tools/amazon_ui_shots.py` / 隔离审计工具）。

---

## 6. 外部并发改动（不计入 Amazon 结论）

另有外部进程并发修改 Alibaba 文件，以下失败与 Amazon 无关，单列不计入本报告结论：

| 现象 | 原因 | 归属 |
|---|---|---|
| `tests/test_platforms.py::test_alibaba_has_trade_and_no_aliexpress_pages` 失败（Alibaba 导航中无 "FOB"） | 外部进程把 Alibaba 价格页从导航移除 | 非 Amazon（未改 Alibaba） |
| `tests/test_ui_pages.py` AI 文案页 `PermissionError: .../AuroraList/config.tmp` | 沙箱禁止写真实用户配置目录（环境限制） | 环境/沙箱 |
| `tests/test_image_plan_conflicts.py` / `tests/test_schedule_destination.py` / `tests/test_publish_repair.py` 在 task-4 最终复跑时失败 | 外部进程并发改 `services/publisher.py`/`alibaba_pipeline.py`/提示词（与 Amazon 无关；task-6 复跑时已恢复 PASS） | 外部并发 |
| `tests/test_amazon_platform_ui_filter.py::test_caps_are_amazon_only` 失败：`tiktok 不应被改动：smt_store_selector` | 外部进程并发开发 TikTok（新增 14 号页并给 `PLATFORM_CAPS["tiktok"]` 加 SMT caps）；Amazon 代码未改 TikTok | 外部并发（task-6 复跑） |
| TikTok 平台声明 14 号页但 `_p_tiktok_listing` 尚在开发 | 外部进程并发写 `ui/app.py`；Amazon 页面与 caps 未被影响 | 外部并发（不计入 Amazon） |

task-6 最终复跑（外部 TikTok 测试断言按 Lead 要求调整后）：全量回归 **58/58 模块 PASS**、
离线回归 **257/257 tests OK**、Amazon 8 个测试文件 **194/194 OK**、只读 18/18、交叉 9/9、
按钮审计 0 ERROR、compileall 通过。上述外部并发项不再造成失败，仅作为背景记录保留。

---

## 7. P0 / P1 / P2

- **P0：无。** 只读链路、真实写 #1/#2/#3、UI 13 页、唯一写入口与护栏、平台隔离全部通过。
- **P1：无。** 交叉审计 9/9、按钮审计 ERROR 0、Amazon 测试 194/194、全量回归 58/58、
  离线回归 257/257、平台隔离 79/79，全部绿。
- **P2（备注，不阻塞）**：
  1. `test_amazon_ui_page.py` / `test_amazon_platform_ui_filter.py` 单独运行需 `AURORA_TEST=1`
     （已写入验收脚本环境）。
  2. AI 文案草稿存在 5 条 warnings（标题/描述长度、价格 0、库存 0、Generic 品牌），发布前处理。
  3. 按钮审计中 DEFERRED/EXTERNAL 为沙箱语义（线程不执行/打开系统外链），非错误。
  4. 截图链路需 Chrome `--headless=new`（旧 headless 中文方框），已在工具内固定。
  5. 平台隔离审计使用沙箱 mock，不覆盖系统托盘等 OS 级入口；建议后续人工抽查。

---

## 8. 复现命令

```bash
# 1) 真实账号只读核验（需 .tmp-probe/cookies.txt 或 DXM_COOKIE_PATH）
.venv/bin/python tools/verify_amazon.py                    # 期望 18/18 PASS

# 2) Amazon 测试（8 文件，AURORA_TEST=1；写路径必须 confirm_write=True）
AURORA_TEST=1 .venv/bin/python tests/test_amazon_contract.py
for f in tests/test_amazon_*.py; do AURORA_TEST=1 .venv/bin/python "$f"; done

# 3) 13 页按钮遍历 + 截图
AURORA_TEST=1 .venv/bin/python tools/amazon_ui_shots.py     # 27 按钮 / 截图

# 4) UI/后端交叉审计（签名/overrides/唯一写入口/预演拒绝/关键控件）
AURORA_TEST=1 .venv/bin/python tools/verify_amazon_ui.py    # 期望 9/9 PASS

# 5) 平台隔离审计（独立，Amazon 消失项/保留项/其他平台/隐藏不足扫描 + 截图）
AURORA_TEST=1 .venv/bin/python tools/verify_amazon_platform_isolation.py
#    → docs/amazon-platform-isolation.json（79/79 PASS；隐藏过头/隐藏不足均为空）

# 6) 全平台按钮审计（含 Amazon 13 页）
AURORA_TEST=1 .venv/bin/python tools/button_audit.py --json docs/ui-button-audit.json

# 7) 回归 + 编译
.venv/bin/python tools/verify_regressions.py                # 58/58 PASS
.venv/bin/python tools/test_offline.py                      # 257 tests OK
.venv/bin/python -m compileall -q src
```

---

## 9. 证据文件

| 文件 | 说明 |
|---|---|
| `docs/amazon-readonly-verification.json` | 18 项只读核验证据（PASS 18/18） |
| `docs/amazon-write-verification.json` | Lead 真实写证据（runs=6, writes=3, actual_platform_writes=4；含未授权误写与恢复，§3.3） |
| `docs/regression-verification.json` | 全量回归 58 模块结果（原有 list 结构；58/58 PASS） |
| `docs/ui-button-audit.json` | 17 页 / 457 回调审计（ERROR 0） |
| `docs/amazon-platform-isolation.json` | 平台隔离独立审计（79/79 PASS；隐藏过头/隐藏不足均为空） |
| `docs/design/amazon-listing-page.png` | 13 页顶部截图（274,162 B） |
| `docs/design/amazon-listing-page-2-price-preview.png` | 13 页价格/预演截图（263,971 B） |
| `docs/design/amazon-listing-page-3-save.png` | 13 页底部截图（265,636 B） |
| `tools/verify_amazon.py` | 独立只读核验（白名单 + 写入口硬断言） |
| `tools/verify_amazon_ui.py` | UI/后端交叉审计（9 项断言，机器证据在 build/verification/） |
| `tools/verify_amazon_platform_isolation.py` | 平台隔离独立审计（消失项/保留项/其他平台/隐藏不足扫描） |
| `tools/amazon_ui_shots.py` | 13 页按钮遍历 + 截图（NOT_READY/控件清单降级） |
| `tests/test_amazon_contract.py` | 离线契约 + 防回退 + 证据结构测试 |
| `tools/button_audit.py`、`tools/verify_regressions.py`、`tools/test_offline.py` | 已接入 Amazon 用例/模块 |

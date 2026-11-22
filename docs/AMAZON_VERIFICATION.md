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
| 发布状态独立核验（task-10/11） | **PASS**：最新实时 25 个发布失败 = **A 23（92.0%）/ B 2（8.0%）/ C 0 / D 0**；发布中 0（瞬态） | `docs/amazon-publish-state.json`（generated_at `2026-11-22T03:33:42+08:00`） |
| 发布 UI / 全按钮 / 逻辑矩阵（task-11） | **27/27 PASS**；69 个可见按钮 0 异常；9 条逻辑断言全 PASS；正式版文案 prod/test 泄漏 0 | `docs/amazon-publish-ui-verify.json`（generated_at `2026-11-22T03:35:14+08:00`） |
| AI 提示词预设库（task-14） | **25/25 PASS**；50 个预设（title 10/bullets 7/description 7/search_terms 6/sku 6/image 14）；合规扫描 0 未豁免命中 | `docs/amazon-presets-verification.json`（generated_at `2026-11-22T03:47:47+08:00`） |
| 去预演 + 自动发布（task-17） | **37/37 PASS**；页面无预演痕迹；auto_publish 8 场景/政策 14 项/UI 7 项全 PASS；Lead 证据 2 商品 reviewing 已提交 | `docs/amazon-auto-publish-audit.json`（generated_at `2026-11-22T04:04:48+08:00`） |
| Amazon 登录页新 7 卡布局（task-20） | **layout 30/30 + 预设 27/27 + 隔离 82/82 PASS**；7 卡顺序/步骤条/控件归属正确；回归 399 用例全 PASS | `docs/amazon-publish-ui-verify.json`、`docs/amazon-presets-verification.json`、`docs/amazon-platform-isolation.json` |
| 左侧导航拆分（task-22） | **导航 35/35 + 预设 29/29 + 隔离 85/85 PASS**；10 可见入口/6 分组/13 隐藏路由 7 卡；7 张新页截图 | `docs/amazon-publish-ui-verify.json`、`docs/amazon-presets-verification.json`、`docs/amazon-platform-isolation.json`、`docs/design/amazon-nav-manifest.json` |
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
| `docs/amazon-publish-state.json` | 发布状态只读核验（最新 25 个发布失败：A23/B2/C0/D0，实时快照） |
| `docs/amazon-publish-ui-verify.json` | 发布 UI/全按钮/逻辑矩阵/正式版文案扫描（27/27 PASS，69 按钮 0 异常） |
| `docs/amazon-presets-verification.json` | AI 提示词预设库独立验收（25/25 PASS；50 预设/合规/功能/UI/隔离） |
| `docs/design/amazon-presets.png` | 13 页 ④b 预设卡 + ⑦ 图片预设卡截图（449,304 B） |
| `docs/amazon-auto-publish-audit.json` | 去预演+自动发布独立验收（37/37 PASS：修复场景/政策/UI/Lead 证据） |
| `docs/design/amazon-auto-publish.png` | 13 页自动发布卡截图（244,962 B） |
| `docs/design/amazon-listing-page.png` | 新 7 卡布局首屏（334,806 B） |
| `docs/design/amazon-listing-new-layout.png` | 新 7 卡整页长图（1560×5,119） |
| `docs/design/amazon-nav-*.png`（15–21） | 左侧导航拆分后 7 个功能页截图 |
| `docs/design/amazon-nav-manifest.json` | 7 页截图清单（尺寸/字节/sha256） |
| `docs/design/amazon-listing-page.png` | 13 页顶部截图（274,162 B） |
| `docs/design/amazon-listing-page-2-price-preview.png` | 13 页价格/预演截图（263,971 B） |
| `docs/design/amazon-listing-page-3-save.png` | 13 页底部截图（265,636 B） |
| `tools/verify_amazon.py` | 独立只读核验（白名单 + 写入口硬断言） |
| `tools/verify_amazon_ui.py` | UI/后端交叉审计（9 项断言，机器证据在 build/verification/） |
| `tools/verify_amazon_platform_isolation.py` | 平台隔离独立审计（消失项/保留项/其他平台/隐藏不足扫描） |
| `tools/amazon_ui_shots.py` | 13 页按钮遍历 + 截图（NOT_READY/控件清单降级） |
| `tests/test_amazon_contract.py` | 离线契约 + 防回退 + 证据结构测试 |
| `tools/button_audit.py`、`tools/verify_regressions.py`、`tools/test_offline.py` | 已接入 Amazon 用例/模块 |


---

## 10. 发布状态与失败原因核验（task-10 草案）

工具：`tools/verify_amazon_publish_state.py`（**独立 requests 实现，不 import 后端**；只读白名单
`pageList / offline / userIn`，唯一写入口以拼接常量 + 请求守卫拦截，无任何写开关）。
证据：`docs/amazon-publish-state.json`（generated_at `2026-11-22T03:09:20+08:00`，status=PASS，
`read_only=true`、`write_endpoints_never_called=true`，只读请求 6 次 / 拦截 0 次 / 无 Cookie 落盘）。

### 10.1 实时状态（以运行时为准；Lead 测试期间会变化）

| 状态 | 数量 | 读取接口 |
|---|---|---|
| 采集箱 draft | **14** | `POST /api/amazonProduct/pageList.json`（dxmState=draft） |
| 待发布 waitPublish | **5** | `POST /api/amazonProduct/offline.json`（dxmOfflineState=waitPublish） |
| 发布中 publishing | **0**（瞬态，约 40s，抓取时刻为 0 属正常） | 同上（dxmOfflineState=publishing） |
| 发布失败 publishFail | **23** | 同上（dxmOfflineState=publishFail，pageSize=200 一次取全） |
| 在线 online | **142** | `POST /api/amazonProduct/pageList.json`（dxmState=online） |

- `leftStat` 与列表总数**一致**：waitPublish=5、publishFail=23、publishing=0。
- Lead 提供「22 个发布失败」为测试时快照；本工具运行时为 **23 个**（测试新增），以实时为准，
  报告中记录了 `generated_at`，便于复跑对比。

### 10.2 失败原因分类（当前 23 个 publishFail）

| 类别 | 数量 | 占比 | 典型 errMsg / 说明 |
|---|---|---|---|
| **A 平台审核中** | **20** | **87.0%** | `code:100521,我们正在审查这个商品信息…请等待最多 48 小时…`（非软件失败，可能自动转在线） |
| **B 类目必填属性缺失** | **3** | **13.0%** | `code:90220,“型号” 为必填项，但缺失。,属性:model_number;…`（需补属性后重存/重发） |
| **C 软件校验拦截** | **0** | 0.0% | 描述含促销词/联系方式等，通常被本地预检拦在草稿/待发布，**不会出现在平台失败列表**；分类器自检可验证 C 规则 |
| **D 其它** | **0** | 0.0% | HTTP/权限/数据/未知（需人工看完整 errMsg / Trace_id） |

> 混合样本 `220114595361361938`：同时含 `100521`（审核中）与 `8560`（商品详情不完整/标识码），
> 按 **A** 归类并记录 `secondary_codes=[8560]`。分类器自检 A/B/C/D 全部 PASS。

### 10.3 必填属性缺失统计

| 商品 id | 缺失属性（`属性:xxx`） |
|---|---|
| `220114595363182310` | `model_number`, `manufacturer_contact_information`, `part_number`, `model_name`（4 项） |
| `220114595363182214` | `item_depth_width_height`, `model_number`, `part_number`, `model_name`, `country_of_origin`, `supplier_declared_dg_hz_regulation`, `list_price`, `power_source_type`, `manufacturer_contact_information`（9 项） |
| `220114595363180944` | `bullet_point`, `variation_theme`, `product_description` |
| `220114595361361938` | `externally_assigned_product_identifier`（同时属 A 类混合样本） |

聚合：`model_number / part_number / model_name / manufacturer_contact_information` 各命中 **2** 个商品；
其余属性各命中 1 个商品。证据 `required_attrs.by_attr` / `by_product`。

### 10.4 发布结果核验清单（给 Lead 的真实发布测试）

| 观察到状态 | 结论 | 下一步 | 需要看的字段 |
|---|---|---|---|
| **在线 online** | 发布成功 | 无需处理，核对在线信息 | `asin`/`listingId`/`onlineTime`/价格/库存 |
| **发布中 publishing** | op=2 已受理，平台处理中（尚无结论） | 约 40s 后重跑本工具复查 | `dxmOfflineState`、`errMsg`（暂空正常）、`updateTime` |
| **发布失败 publishFail** | 平台返回失败 | 按 errMsg 分类：A 等 48h 复查；B 补属性；C 改文案；D 人工核对 | `errMsg` 中 `code:…`、`属性:…`、末尾 `Trace_id` |
| **待发布 waitPublish** | 已保存但未提交平台 | 需要发布则用 `op=2`；`op=1/3` 不触发平台发布 | `dxmOfflineState=waitPublish`、实际 op、`errMsg`（通常空） |
| **采集箱 draft** | op=2 未生效/被拦截/尚未保存成功 | 检查保存结果与校验 issues | `save_result.code/msg`、`issues/warnings`、实际 op |
| **未找到** | id/店铺/站点不匹配或已被删除 | 确认 product_id / shopId / marketPlaceId | `product_id`、`shopId`、`marketPlaceId` |

工具支持 `--product-id a,b` 生成同样的 checklist；本次运行样例：
`220114595363182310 → 发布失败 B 类（4 属性）`、
`220114595363182214 → 发布失败 B 类（9 属性）`、
`220114595362938158 → 发布失败 A 类（审核中）`。

### 10.5 限制与安全

- `publishing` 是约 40s 的瞬态，抓取时刻可能为 0；需按清单复查，不能单次抓取就断言失败。
- A 类 100521 需等待平台审核（最多 48h），只读核验无法提前判定最终在线/失败。
- C 类被软件本地校验拦截，通常不会进入平台失败列表；本工具用分类器自检验证 C 规则（当前平台
  publishFail 中 C=0 不代表软件无该校验）。
- 全程只读：6 次请求（userIn + draft/online + 3 状态）、0 拦截、无 Cookie 落盘；
  唯一写入口被拼接常量 + 请求守卫硬拦截（源码中无完整写路径字面量）。
- 账号数据在 Lead 测试期间会变化，报告以 `generated_at` 时刻为准；复跑即可刷新。


---

## 11. 发布功能终版复核（task-11，覆盖 §10 最新实时数据）

> 本节基于 **2026-11-22T03:33:42 +08:00** 的实时只读数据 + Lead 的真实发布证据
> `docs/amazon-publish-verification.json`（generated_at `2026-11-22T03:22:13`，runs=9，
> product_writes=5）。工具：`tools/verify_amazon_publish_state.py`（只读）、
> `tools/verify_amazon_ui.py`（沙箱 mock）。证据：`docs/amazon-publish-ui-verify.json`。

### 11.1 Lead 真实发布 4 个商品时间线（证据摘要）

| 商品 | Lead 证据 | 最新实时（03:33） | 结论/处置 |
|---|---|---|---|
| `220114595363182310` | 发布 #1：B 类缺 4 属性 → 自动补齐 → 重发 → after `publishFail/reviewing` | `publishFail`，分类 **A**（100521 审核中） | 已提交平台审核，等最多 48h；无需再改数据 |
| `220114595363182214` | 发布 #2：类目 LAWN_MOWER→Backhoes + 两轮属性补齐 → after `publishFail/reviewing` | `publishFail`，分类 **A** | 类目错配已修，已进入平台审核 |
| `220114595363182036` | 发布 #3：C 类促销词被本地拦截 → AI 重写描述 → 发布 → after `publishing` | **`publishFail`，分类 B（缺 `item_name`）**（时序漂移） | 平台处理中转失败：需补 `item_name` 后重发（新处置项） |
| `220114595363181922` | 发布 #4：C 类拦截 → AI 重写描述 → 发布 → after `publishFail/reviewing` | `publishFail`，分类 **A** | 已提交平台审核，等最多 48h |

- **状态映射**：`online`=发布成功；`publishing`/`waitPublish`=已提交处理中（非失败，需复查）；
  `publishFail` + `100521/审查/审核`=**平台审核中（A 类，非软件失败）**；
  `publishFail` + `90220/属性:xxx`=**B 类真实校验失败（需补属性）**；
  `draft`=未提交/未保存成功；`unknown`=未能确认。
- **重要判定规则**：**「发布失败列表」里包含平台审核中的商品**（A 类）。读 `errMsg` 时
  必须先判 `100521/审查/审核`，不能把列表名 `publishFail` 直接当软件失败；B 类才是需补数据。
- **时序漂移**：Lead 证据快照 036 为 `publishing`，实时已转 `publishFail`（缺 `item_name`）；
  工具允许并显式标记 `needs_attention`，不掩盖为成功。

### 11.2 当前发布失败分类（25 个，03:33 实时）

| 类别 | 数量 | 占比 | 说明 |
|---|---|---|---|
| A 平台审核中（100521） | **23** | **92.0%** | 非软件失败，等 48h/复查在线；含 310/214/922 |
| B 类目必填属性缺失（90220） | **2** | **8.0%** | `220114595363182036` 缺 `item_name`；`220114595363180944` 缺 bullet/变体/描述 |
| C 软件校验拦截 | 0 | 0% | 本地预检拦截，通常不进平台失败列表（分类器自检可验证） |
| D 其它 | 0 | 0% | — |

> 分状态计数：采集箱 12 · 待发布 5 · 发布中 0（瞬态）· 发布失败 25 · 在线 142；
> `leftStat` 与列表一致。数值随 Lead 测试实时变化，以 `generated_at` 为准。

### 11.3 发布 UI / 全按钮 / 逻辑矩阵（`docs/amazon-publish-ui-verify.json`，27/27 PASS）

- **全按钮矩阵**：Amazon 0/13/10/11 共 **69 个可见按钮逐一点击**（mock 网络、沙箱隔离），
  **ERROR 0**；覆盖检测连接、刷新/分页/状态切换、编辑载入、类目/历史/路径、AI 各按钮、
  编辑/添加/清空卖点、发布前检查、保存（检查模式）、查询发布结果、自动补齐并重试发布、
  发布中/发布失败列表、设置/日志交互。
- **逻辑矩阵（9 条，全部 PASS）**：op=1/2/3 语义；发布状态 6 种映射；A/B/C/D 失败分类
  （含 100521→A、90220→B、促销词→C、其它→D 自检）；**属性自动补齐只补缺失、不覆盖用户值**；
  **confirm_write 门禁**（dry_run=False 未显式确认 → 拒绝且不调用保存）；
  UI 重试发布走 `confirm_write=True`、查询发布结果只读；5 状态列表映射；
  **发布失败列表内 100521 判定为 A 类**；`AURORA_TEST/AURORA_NO_SAVE` 拒绝真实写。
- **UI 发布状态渲染（mock）**：检测连接显示采集箱/发布中/发布失败/在线数量；
  发布结果徽标 `平台审核中 / 发布失败 / 处理中 / 发布成功` 正确；
  B 类完整失败原因 + 缺失属性（`model_number`/`part_number`）渲染；
  检查模式「自动补齐并重试发布」被拒绝（不真实写）。
- **正式版 UI 扫描**：Amazon 全页面可见文本（prod 与 AURORA_TEST 两种模式）
  **无 AURORA_ / 预演模式 / 本地验收 / 测试连接 / 沙箱 / mock / 验收预演**；
  App 级测试模式标题已清理（当前 `AuroraList · 极光上架 v1.0.6`，无测试标记）。
  5 个平台所有页面渲染无崩溃。

### 11.4 回归与 P0/P1/P2

- `tools/verify_amazon.py`：**18/18 PASS**（基线计数漂移记录，不误判）。
- `tools/verify_amazon_ui.py`：**27/27 PASS**。
- `button_audit`：**21 页 / 593 回调，ERROR 0**。
- `compileall -q src`：**exit 0**。
- Amazon 测试 9 文件 **263 用例 / 260 PASS / 3 FAIL**（见 P1）；
  `tools/verify_regressions.py` 67 模块 65 PASS / 2 FAIL（同一批 Amazon 测试期望漂移）；
  `tools/test_offline.py` 686 tests / 3 FAIL（同上）。
- **P0：无。**
- **P1**：
  1. `tests/test_amazon_payload.py`（2 个断言：发布必填属性缺失 warning 文案 / empty productAttr
     warning）与 `tests/test_amazon_pipeline.py`（1 个断言：真实写 #2 的 productAttr 现在会
     自动写入 REQUIRED_COMMON 属性）**落后于 task-8 新后端行为**，需 backend 测试 owner 更新
     断言（本 verifier 不改 src/tests 归属外文件）。
  2. **036 商品**由 `publishing` 转为 `publishFail B`（缺 `item_name`），需补属性后重发；
     Lead 证据文件对应 run 仍是 `publishing` 快照，建议补一条最新核验记录。
- **P2**：
  1. 按钮审计 DEFERRED/EXTERNAL 为沙箱语义（线程不执行/打开系统外链），非错误。
  2. 发布状态为实时快照（publishing 约 40s 瞬态），复查需按 `generated_at` 重跑。
  3. C 类被本地预检拦截，通常不出现在平台发布失败列表；分类器自检已覆盖规则。

### 11.5 Lead 最终复核（2026-11-22 03:40，真实发布测试收口）

**4 个测试商品全部从失败修复到「已提交（平台审核中）」：**

| 商品 | 首次失败原因（分类） | 修复动作 | 最终状态 |
|---|---|---|---|
| 220114595363182310 | B 类：缺 model_number / part_number / model_name / manufacturer_contact_information | 自动补齐必填属性 → 重发 | publishFail（**A 类审核中 100521**） |
| 220114595363182214 | 类目错配（LAWN_MOWER）+ 9 项属性缺失 → 改类目后又缺 5 项 | 改类目 Backhoes + 两轮错误驱动属性补齐 | publishFail（**A 类审核中**） |
| 220114595363181922 | C 类：描述含 sale/guaranteed/100%/电话（本地校验拦截） | AI 重写描述 → 发布 | publishFail（**A 类审核中**） |
| 220114595363182036 | D 类：标题 backhoe 重复 3 次（platform code:100470） | 标题去重（backhoe/loader ≤2 次）→ 重发 | publishFail（**A 类审核中**） |

- 最终实时计数：采集箱 **12** · 待发布 **5** · 发布中 **0** · 发布失败 **25** · 在线 **142**；
  25 个发布失败 = **A 类 24（平台审核中，非软件失败）+ B 类 1**（`220114595363180944` 为测试前历史数据，
  缺 bullet_point/variation_theme/product_description，非本次测试对象）。
- 新增软件校验（本轮）：**标题同一单词出现 >2 次直接阻断**（Amazon 实测 code:100470）；
  保存包级必填属性自动补齐（默认开启，`cfg.amazon_autofill_attrs=False` 可关闭）；
  错误驱动第二轮属性补齐（布尔/power_source/country/item_dimensions/list_price 等确定性映射）。
- P1 已闭环：payload/pipeline 测试断言已按新行为更新，**9 个 Amazon 测试文件全绿**；
  真实发布证据 `docs/amazon-publish-verification.json` 已含 4 商品最终 run。
- 结论：**软件发布链路（op=2）真实可用**；「发布失败」列表需先看 errMsg——100521 是平台审核中，
  不是软件失败；B 类按缺失属性补齐、C 类改文案、D 类按平台规则改标题后均可重发成功。



---

## 12. AI 提示词预设库独立验收（task-14）

工具：`tools/verify_amazon_presets.py`（离线/只读：公开 API 实测 + 沙箱 UI，不联网、不真实写）。
证据：`docs/amazon-presets-verification.json`（generated_at `2026-11-22T03:47:47+08:00`，
**25/25 PASS**，PRESET_VERSION `2026-11-22.1`）。截图：
`docs/design/amazon-presets.png`（449,304 B，13 页 ④b 预设卡 + ⑦ 图片预设卡）。

### 12.1 六类预设清单（共 50 个）

| 类别 | 数量 | 最低要求 |
|---|---|---|
| title | **10** | ≥6 ✅ |
| bullets | **7** | ≥5 ✅ |
| description | **7** | ≥5 ✅ |
| search_terms | **6** | ≥5 ✅ |
| sku | **6** | ≥5 ✅ |
| image | **14** | ≥10 ✅ |

- **title（10）**：工业机械 B2B 参数型、工程机械高转化、农机 / 园林、包装 / 食品机械、数控 / 金属加工、激光 / 焊接、通用工业品高转化 SEO、品牌 + 型号 + 规格 + 场景、移动端短标题（≤80 字符）、型号 / 规格优先
- **bullets（7）**：Benefit-first（默认）、参数规格优先、场景化（施工 / 农场 / 仓储）、B2B 采购关注（耐用 / 维护 / 配件）、差异化对比（不贬低竞品）、合规安全型、FAQ 式卖点
- **description（7）**：标准工业结构（用途→参数→结构→服务）、参数表导向、场景应用型、B2B 信任型（质保 / 配件 / 售后）、移动端短描述、中性方案对比、A+ 风格分段
- **search_terms（6）**：核心词 + 属性 + 场景、长尾组合、同义词 / 拼写变体、采购意图词、型号系列词、应用场景词
- **sku（6）**：品牌-型号-规格、型号-变体属性、站点-类目-序号、简码-尺寸-功率、纯字母数字短码、型号-颜色-数量
- **image（14）**：主图纯白底、主图浅灰棚拍、工业场景图、细节特写、尺寸 / 规格信息图（仅副图）、配件 / 包装清单图、去水印 / Logo / 二维码、图片文字翻译（站点语言）、老图修复清晰化、A+ 生活方式图、作业动作主视觉、包装箱 / 托盘展示（仅副图）、多张细节拼合（仅副图）、多角度展示

### 12.2 内容合规审计

- **命名/描述/prompt 质量**：50 条名称、desc 非空，prompt 长度 ≥120 字符；全部含明确约束
  （长度上限/同词频次/禁止项/输出格式）。
- **占位符白名单**：仅 `{country}{code}{language}{lang}{title_limit}{bullet_limit}
  {bullet_suggest}{count}{desc_bytes}{st_bytes}{title}{subject}`；50 条预设**0 个未知占位符**；
  `render` 只替换已知占位符，未知 `{unknown_ph}` 原样保留且不报错。
- **促销/联系方式扫描**：非 image 类 36 条 prompt 扫描 `best / No.1 / 100% / free shipping /
  sale / guarantee / http / @ / 电话 / WhatsApp`，**0 条未豁免命中**。
  白名单规则：命中词若所在分句含「禁止/不得/不要/不含/避免/切勿/无」等否定语境则豁免
  （避免把「禁止促销」的合规指令误判为示例）。
- **SKU 预设约束**：6 条均包含 ≤40、字母/数字/-/_、不得含空格、只输出 SKU。
- **图片主图约束**：`white_bg_main` 同时包含「纯白、文字、水印、产品本体、Logo」约束。

### 12.3 功能验收（全 PASS）

- `list_presets / preset_options / get_preset / apply_to_cfg / resolve_selected /
  reset_to_default / render` 六类行为一致；apply 写入 prompt+选择 key+版本 key；
  resolve 合法回显、非法回落默认；reset 回默认。
- **build_*_prompt 确实使用新 prompt**：title / bullets / description / search_terms / sku
  五类套用后构建的 prompt 均包含预设文本；cfg 自定义提示词覆盖也生效。
- **`ai_generate_field("sku")`**：FakeApi 返回非法脏文本 `Seller SKU: hw20 28/CA_01` →
  清洗为 `hw20-28-CA_01`（合法 ≤40、[A-Za-z0-9_-]）。
- **`parse_sku` 边界**：空串、代码围栏+标签、超长 60 字符（截断 ≤40）、非法字符/中文/空格
  全部清洗为合法字符集。
- **UI**：六类预设下拉（10/7/7/6/6/14 选项）+ 套用×6 + 恢复内置×6 + 复制提示词 +
  5 类文本提示词可编辑框 + 图片提示词可编辑框 + 当前预设名 +「AI 生成 SKU」按钮；
  实测点「套用」写入 `amazon_title_prompt` 成功。
- **后端缺失降级**：`amazon_presets` 模块不可用时 13 页仍渲染（下拉禁用/提示未就绪），不崩溃。

### 12.4 平台隔离

- AliExpress `TEXT_PROMPT_PRESETS` 仍存在；Alibaba `services/alibaba_prompts.py` 行业预设模块仍在；
  AliExpress AI 文案页 / Alibaba 12 页 / Amazon 13 页均可渲染。
- Amazon 预设只写 `amazon_*_prompt/amazon_*_preset/amazon_presets_version` 独占键；
  实测 apply 六类后 Alibaba/AliExpress 提示词键**完全未被修改**。
- Amazon 页面正式版文案扫描：**0 泄漏**（AURORA_/预演模式/本地验收/测试连接/沙箱/mock/验收预演）。

### 12.5 回归与 P0/P1/P2

- `tools/verify_amazon_presets.py`：**25/25 PASS**；截图 `docs/design/amazon-presets.png`（449,304 B）。
- 全部 `tests/test_amazon_*.py`（12 文件）：**全部 PASS**（含新增 `test_amazon_presets_audit` 11 用例、
  `test_amazon_presets` 25 用例、`test_amazon_ui_page` 25 用例）。
- `tools/verify_regressions.py`：**71 模块 70 PASS / 1 FAIL**；唯一 FAIL 为
  `test_ui_redesign`（外部 Alibaba 页新增 `Switch(bool)` 导致测试 join 报错，非 Amazon，单列）。
- `tools/test_offline.py`：**754 tests OK**（已接入 `test_amazon_presets_audit`）。
- `button_audit`：**21 页 / 618 回调，ERROR 0**。
- `verify_amazon.py`：18/18 PASS；`verify_amazon_ui.py`：27/27 PASS；`compileall`：exit 0。
- **P0：无。**
- **P1：无（Amazon）。** 唯一回归失败 `test_ui_redesign` 属外部 Alibaba 并发改动（非 Amazon）。
- **P2**：
  1. 图片提示词输入框 label 文案重复：「图片提示词提示词（可编辑，留空=内置默认）」→
     建议改为「图片提示词（可编辑…）」（UI 文案小瑕疵，不影响功能）。
  2. 「复制提示词」在未载入商品时复制原始占位符文本（设计如此，提示用户可用
     `{title}/{subject}/{language}` 替换）；载入商品后走后端 render 替换。
  3. 图片预设 v1 仅作提示词备用（Amazon 图片只读，不上传/不替换），截图中的 ⑦ 卡为提示词预览。
- **外部问题单列**：`test_ui_redesign` 1 ERROR 来自 Alibaba 页新增 `Switch(bool)`（外部并发改动），
  非 Amazon，已按 Lead 要求单列不计。



---

## 13. 去预演 + 真实自动发布验收（task-17）

工具：`tools/verify_amazon_auto_publish.py`（离线 FakeClient + patch 写收口，不联网/不真实写）。
证据：`docs/amazon-auto-publish-audit.json`（generated_at `2026-11-22T04:04:48+08:00`，
**37/37 PASS**）。截图：`docs/design/amazon-auto-publish.png`（244,962 B，13 页自动发布卡）。

### 13.1 用户可见预演逻辑已删除（Amazon 13 页）

- 页面可见文本/按钮扫描：**无「预演」「发布前检查」「检查模式」「发布前检查与保存」**
  （0 泄漏）；保存按钮为 **「保存到 Amazon」**（真实提交），新增 **「自动发布（自动修复）」**。
- AURORA_TEST / AURORA_NO_SAVE 下：保存与自动发布均安全拒绝，提示
  **「当前运行环境禁止写入平台数据，请在正式环境操作」**，且**不出现预演/检查模式字样**。
- 二次确认弹窗标题 **「确认自动发布（自动修复）」**，内容含商品/标题/站点/动作
  （op=2 保存并发布·真实提交）/最多轮数/发布后等待/自动修复项/「这会真实写入/发布到平台」。
- 价格或库存为空时直接阻断（提示缺少价格/库存），不弹确认、不发请求（不会编造价格库存）。

### 13.2 auto_publish 自动修复审计（8 场景全 PASS）

| 场景 | 结果 |
|---|---|
| 首轮成功 | `ok=True saved=True rounds=1`；reviewing 视为已提交非失败 |
| B 类缺属性（90220 属性:item_name） | 第 2 轮补齐属性后提交；`rounds=2 saves=2` |
| 100470 标题重复 | 自动去重（标题 `loader` ≤2 次）后第 2 轮提交 |
| 描述含促销/联系方式/中文电话 | `sanitize_description` 清洗/兜底后第 2 轮提交（电话/WhatsApp/free shipping 已清除） |
| 价格/库存缺失 | 阻断且 **0 次真实写**（不自动编造，需用户填写） |
| max_rounds 用尽 | 达到上限安全停止（`rounds=max_rounds`，不无限重试） |
| CI 护栏（AURORA_TEST） | auto_publish 直接拒绝真实发布（blockers 含 AURORA/拒绝），0 次写 |
| 唯一写入口 | auto_publish 仅经 `save_to_amazon` 写，源码无 `client.save_product` 直连 |

### 13.3 policy_preflight 平台政策（14 项全 PASS，无编造）

- 干净 payload 0 阻断；标题同词>2 → `dedupe_title_words`；标题促销/联系方式/URL、全大写、
  emoji → `sanitize_title`；五点 HTML/促销 → `sanitize_bullets`；
  描述促销/联系方式 → `sanitize_description`；ST 超长/标点/促销 → `sanitize_search_terms`；
  SKU 非法字符 → `sanitize_sku`；schema 必填（strict）→ 阻断；主图缺失 → 阻断。
- **价格缺失/库存为 0 → 阻断（不编造）**；修复动作集合中**无价格/库存/认证生成**动作。
- 预检只做「阻断/清洗/去重/截断」；参数/认证只能来自原文。

### 13.4 UI 自动发布控件 / 确认 / 按轮结果（7 项全 PASS）

- 控件：自动发布按钮 + 3 下拉（自动发布动作 / 最多自动修复轮数 1/2/3/5 / 发布后等待 30/60/90/180）
  + 4 开关（自动补齐必填属性 / 自动修正标题 / 自动清理描述 / 自动 AI 重写付费）；
  当前值从 cfg `amazon_auto_*` 读取并回写。
- 二次确认 → 真实执行；价格库存为空阻断；AURORA_TEST 拒绝（无预演文案）。
- 结果区渲染：`自动发布结果` + `轮数/耗时` + 每轮明细（第 N 轮 · 状态 · 修复/动作 · err）
  + `自动修复记录` + `阻断原因` + `发布详情`；Lead UI 文本已核对（含 100521 审核中说明）。
- 13 页可见按钮矩阵：46 个控件逐一点击 **ERROR 0**；全平台 button_audit 21 页/627 回调 ERROR 0。

### 13.5 Lead 真实发布证据交叉核对

`docs/amazon-auto-publish-verification.json`（3 runs，generated 04:02:08）：

| 商品 | 记录 | 结论 |
|---|---|---|
| `220114595363181900` | run1 blocked（描述含中文「电话」；autofill 4 属性）；run2 `reviewing` | 修复 sanitize/fallback 后**重跑成功，已提交**；run1 是修复前记录 |
| `220114595363181652` | run1 `reviewing`，修复记录 autofill 4 属性 | 已提交，平台审核中 |
- **reviewing = 已提交非失败**（实时列表显示 `publishFail/100521`，分类 A，不是软件失败）。
- 两商品均有 `saved=True` 与 `code:100521`；实时只读复核 181900/181652 均 `publishFail/A`，
  与 Lead 证据一致。自动修复记录（autofill / sanitize_description）在证据中可查。

### 13.6 回归与 P0/P1/P2

- `tools/verify_amazon_auto_publish.py`：**37/37 PASS**；页面/截图见上。
- 全部 `tests/test_amazon_*.py`（14 文件，**382 用例**）：**全部 PASS**（含新增
  `test_amazon_auto_audit` 10 用例、`test_amazon_auto_publish` 35 用例、`test_amazon_ui_page` 31）。
- `tools/verify_regressions.py`：**79/79 模块 PASS**（已接入 `test_amazon_auto_audit`）。
- `tools/test_offline.py`：**941 tests OK**。
- `button_audit`：**21 页 / 627 回调，ERROR 0**。
- `tools/verify_amazon.py`：18/18 PASS；`compileall -q src`：exit 0。
- 平台隔离：AliExpress AI 文案 / Alibaba 12 / Amazon 13 均正常渲染；Amazon 页面正式版文案
  0 泄漏（AURORA_/预演/本地验收/测试连接/沙箱/mock）。
- **P0：无。**
- **P1：无。**
- **P2**：
  1. `tools/verify_amazon_ui.py`（task-11 旧工具）仍按「预演/发布前检查」旧文案断言，
     去预演后 22/27（5 项预期失效）；属验收工具待随新 UI 更新，不影响产品功能。
  2. 本环境当前 Chrome headless 截图偶尔出现中文字形缺失（布局正确）；此前 task-14 截图正常，
     属截图链路/环境问题，已在工具中固定 `--headless=new`，如复现建议重跑截图脚本。
  3. 自动发布为真实写操作：生产使用前需确认价格/库存已填、二次确认弹窗人工核对。

### 13.7 Lead 真实发布与构建验证（2026-11-22 04:00）

- **构建**：`tools/build_verified.py amazon-autopublish` → `dist/verified-amazon-autopublish-20261122-040319/AuroraList.app`；
  codesign **PASS**，打包程序 `--verify-local` **PASS**，sha256 `d5023a55…d20407f8bb`。
- **真实发布（程序 UI 事件链路：真实 App 页面 ⑥ → 自动发布按钮 → 二次确认 → amazon_auto，真实网络/真实写）**：
  | 商品 | 过程 | 结果 |
  |---|---|---|
  | 220114595363181900 | 首跑本地预检拦截（描述含中文「电话」）→ 修复中文联系方式/促销词清洗 + 描述过短 fallback → 重跑：第1轮 autofill 4 属性 + sanitize_description → saved=True | **publishFail/A 审核中（100521，非失败）** |
  | 220114595363181652 | 第1轮 autofill 4 属性 → saved=True | **publishFail/A 审核中（100521，非失败）** |
- 证据：[docs/amazon-auto-publish-verification.json](docs/amazon-auto-publish-verification.json)（3 条 run：1 条修复前 blocked + 2 条 submitted）。
- 结论：**「预演」用户功能已删除**；真实发布走 op=2 + 自动修复闭环；失败可自动补属性/清洗标题描述并重发；
  两商品均成功提交平台审核。

> 全平台最终验收总报告：`docs/ALL_PLATFORMS_FINAL_VERIFICATION.md`（2026-11-22，含构建/AI/图片/视频/逐平台）。



---

## 14. Amazon 刊登页布局重构（按功能分组，task-20）

> 背景：task-19 将 13 页从旧 6 卡（④b/⑥/⑦ 混排）重排为按功能分组的 **7 卡**；
> 本 verifier 独立渲染复核 + 更新过时审计工具/截图/文档。

### 14.1 新 7 卡顺序与标题（渲染实测）

| # | 卡标题 | 主要内容（迁移后归属） |
|---|---|---|
| ① | 连接与店铺 | Cookie 输入/读取本机 Cookie/检测连接/店铺/站点/连接状态 |
| ② | 采集箱商品 | 5 状态 tab（采集箱/待发布/发布中/发布失败/在线）/搜索/刷新/上一下一页/商品列表 |
| ③ | 站点与类目 | 顶层类目/最近使用/加载类目/nodePath 校验/推荐产品类型 + **类目属性（productAttr 只读）**（原 ⑤/⑥ 迁移） |
| ④ | 商品信息（价格 / 库存 / 物流 / 品牌 / SKU） | 标准价/促销价/数量/配送/时效/运费模板+刷新/品牌/制造商/SKU+AI 生成 SKU/**4 必填属性+自动补齐** |
| ⑤ | 文案与 AI | 标题+AI/卖点数量+AI 生成五点/＋添加/清空/5 行/描述+AI/ST+AI/一键生成全部 + **5 组文案预设（下拉/套用/恢复/可编辑）**（原 ④b 合并） |
| ⑥ | AI 图片（图片只读，v1 备用） | 图片数量/缩略图 + **图片提示词预设（14 个/套用/恢复/复制/编辑）**（原 ⑦ 合并） |
| ⑦ | 发布与结果 | 保存动作/保存到 Amazon/查询发布结果/自动补齐并重试发布/**自动发布（自动修复）+3 下拉+4 开关**/结果区 |

- 步骤条（页头）：`① 连接店铺/站点 → ② 选采集箱商品 → ③ 站点与类目 →
  ④ 价格/库存/品牌/SKU → ⑤ 文案与 AI → ⑥ AI 图片 → ⑦ 发布与结果`，
  与卡片实际顺序**完全一致**（位置断言 10→27→48→75→115→259→298）。
- 控件归属检查：7 卡全部关键控件均在对应卡范围内（无丢失/错位）；
  原「④b AI 提示词预设」「⑦ AI 图片提示词预设」「⑥ 保存与发布」已合并/改名。

### 14.2 审计工具更新点

| 工具 | 更新 |
|---|---|
| `tools/verify_amazon_ui.py` | 新增 4 项新布局断言：7 卡标题/编号顺序、步骤条与卡顺序一致、7 卡控件归属；发布区检查改为新卡「保存到 Amazon/自动发布」；结果 30/30 PASS |
| `tools/verify_amazon_presets.py` | 新增 2 项归属断言：5 组文案预设必须在 ⑤；图片预设/复制/编辑必须在 ⑥；截图改为整页长图（裁空白），结果 27/27 PASS |
| `tools/verify_amazon_platform_isolation.py` | 卡片标签更新为新 7 卡；保留项「测试连接/预演」改为「检测连接/保存到 Amazon/自动发布」；结果 82/82 PASS |
| `tests/test_amazon_ui_page.py` / `test_amazon_platform_ui_filter.py` | UI 团队已先更新断言（32/32、10/10），本 verifier 复核其已含新卡顺序/步骤条/控件归属断言，未重复改冲突 |

### 14.3 截图

- `docs/design/amazon-listing-page.png`：新布局首屏（1560×2200，334,806 B）。
- `docs/design/amazon-listing-new-layout.png`：**整页长图**（1560×5,119，706,416 B，未截断：
  含 ①–⑦ 全部卡片；底部空白已裁掉）。
- `docs/design/amazon-presets.png`：更新为整页长图（1560×5,119，706,416 B，⑤ 文案预设 + ⑥ 图片预设）。
- 正式版文案：`verify_amazon_ui` 正式版扫描 0 泄漏（无 AURORA_/预演/检查模式/测试字样）。

### 14.4 回归（全部绿）

- 全部 `tests/test_amazon_*.py`（15 文件，**399 用例**）：**全 PASS**
  （含 `test_amazon_ui_page` 32、`test_amazon_platform_ui_filter` 10、`test_amazon_live_smoke` 16）。
- `tools/verify_amazon_ui.py`：**30/30 PASS**（含新布局 4 项）。
- `tools/verify_amazon_presets.py`：**27/27 PASS**（含新布局 2 项）。
- `tools/verify_amazon_platform_isolation.py`：**82/82 PASS**（新 7 卡 + 其它平台隔离）。
- `button_audit`：**21 页 / 630 回调，ERROR 0**。
- `tools/test_offline.py`：**943 tests OK**；`python -m compileall -q src`：exit 0。
- 其它平台（Alibaba/AliExpress/TikTok/Shopee）页面在隔离审计中均可渲染，未被 Amazon 布局改动影响。

### 14.5 P0/P1/P2

- **P0：无。** 新布局渲染/步骤条/控件归属全部通过，回归全绿。
- **P1：无。**
- **P2（备注）**：
  1. 整页长图较高（5,119px），部分 Markdown 预览器会自动缩略；文件本身未截断，可按需查看原图。
  2. ⑤ 文案卡内 6 个预设「套用/恢复」按钮同名，自动化按控件位置断言归属；人工操作按卡片分区无误。
  3. 图片提示词输入框 label 仍为「图片提示词提示词（可编辑…）」（历史小瑕疵，建议改「图片提示词（可编辑…）」）。



---

## 15. 左侧导航按功能拆分（task-22）

### 15.1 新导航结构（渲染实测）

| 分组 | 页面 |
|---|---|
| 工作空间 | 0 概览 |
| 商品准备 | **15 连接与店铺 / 16 采集箱商品 / 17 站点与类目 / 18 商品信息** |
| 内容处理 | **19 文案与 AI / 20 AI 图片** |
| 发布与运行 | **21 发布与结果** |
| 运行 | 10 任务与日志 |
| 工具 | 11 设置 |

- 左侧导航共 **10 个可见入口**（顺序：0→15→16→17→18→19→20→21→10→11），6 个分组归属正确。
- **13「Amazon 刊登」不在左侧导航**：`PageItem(13, group="__internal__")`，`Platform.groups()` 过滤；
  `_switch(13)` 仍渲染完整 7 卡工作台（旧测试/深链兼容）。
- **15–21 每页只渲染对应单卡** + 页头引导（如 `① 连接与店铺：…`）+ 上下文行
  （`当前商品 / 当前站点 / 当前类目`）；7 页均无其它卡标题泄漏。
- 其它平台（Alibaba/AliExpress/TikTok/Shopee）页面集合/导航未变，全部页面可渲染。

### 15.2 审计工具更新

| 工具 | 更新 | 结果 |
|---|---|---|
| `tools/verify_amazon_ui.py` | 新增 6 项导航断言：10 入口/顺序、6 分组归属、13 不在导航（`__internal__`）、15–21 每页单卡+页头+上下文、隐藏路由 13 完整 7 卡；正式版扫描扩展到新导航页 | **35/35 PASS** |
| `tools/verify_amazon_platform_isolation.py` | Amazon 页面清单改为 0/15–21/10/11 + 隐藏 13；新增导航/分组/单卡断言；其它平台隔离断言不变 | **85/85 PASS** |
| `tools/verify_amazon_presets.py` | 新增：19 页仅文案预设（无图片预设）、20 页仅图片预设（无文案预设）；13 深链兼容保留 | **29/29 PASS** |
| `tools/button_audit.py` | 复核 Lead 动态 Amazon 可见页实现：`_amz_visible = pages where not group.startswith("_")`，覆盖 0/10/11/15–21（13 不入导航）；无重复/漏页 | **27 页/629 回调 ERROR 0** |
| `tests/test_amazon_ui_page.py`（34）/`test_amazon_platform_ui_filter.py`（11） | UI 团队已先更新导航断言；本 verifier 复核其含入口顺序/分组/隐藏 13/每页单卡/过滤搜索断言，未重复改冲突 | 34/34、11/11（其中 ui_page 1 项见 P1 外部配置） |

### 15.3 截图（7 张新页 + 清单）

| 页面 | 截图 |
|---|---|
| 15 连接与店铺 | `docs/design/amazon-nav-15-connection.png` |
| 16 采集箱商品 | `docs/design/amazon-nav-16-products.png` |
| 17 站点与类目 | `docs/design/amazon-nav-17-category.png` |
| 18 商品信息 | `docs/design/amazon-nav-18-product-info.png` |
| 19 文案与 AI | `docs/design/amazon-nav-19-copy-ai.png` |
| 20 AI 图片 | `docs/design/amazon-nav-20-ai-image.png` |
| 21 发布与结果 | `docs/design/amazon-nav-21-publish.png` |

- 7 张均 1560×2200 非空；清单/字节/尺寸/sha256 前缀见
  `docs/design/amazon-nav-manifest.json`（generated_at `2026-11-22T13:25:57+08:00`）。
- 13 内部 7 卡工作台的整页长图仍为 `docs/design/amazon-listing-new-layout.png`（task-20）。
- 正式版文案扫描（含新导航页）：无 AURORA_/预演/本地验收/测试连接/沙箱/mock 泄漏。

### 15.4 回归与 P0/P1/P2

- 导航专项：`verify_amazon_ui` **35/35**、`verify_amazon_presets` **29/29**、
  `verify_amazon_platform_isolation` **85/85**、button_audit **27 页/629 回调 ERROR 0**。
- `test_ui_redesign`：20/20 PASS；`test_amazon_platform_ui_filter`：11/11 PASS。
- `tests/test_amazon_*.py`（15 文件，402 用例）：**400 PASS / 2 FAIL**（见 P1，根因同一外部配置）；
  `tools/test_offline.py`：959 tests / 3 FAIL（上述 2 项 + TikTok 外部 WIP 1 项）。
- **P0：无。**
- **P1（外部配置问题，非导航缺陷）**：
  `assets/preset_defaults.json` 把 `amazon_use_ai` 设为 `true`，覆盖了 `config.py` DEFAULTS 的
  `False`，导致：
  1. `tests/test_amazon_pipeline.py::test_config_defaults_present` 断言 `cfg.amazon_use_ai is False`
     失败；
  2. `tests/test_amazon_ui_page.py::test_ai_generation_needs_switch_then_fills_fields` 因 AI 默认
     开启，点「AI 生成标题」不再弹「开关」提示而失败。
  建议：由 preset/测试 owner 对齐（改 preset 回 false，或更新测试期望），与本任务导航改动无关。
- **P2**：
  1. `test_offline` 另 1 个失败 `test_tiktok_ui_page` 属外部 TikTok 并发 WIP（非 Amazon）。
  2. 13 号页保留为内部深链（不在导航），旧链接/测试仍可访问；后续若无深链需求可考虑移除。
  3. 导航搜索/快捷键仍按当前平台可见页工作，隐藏 13 不参与搜索。

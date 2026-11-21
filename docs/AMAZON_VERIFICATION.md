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
| Amazon 测试（7 文件 171 用例） | **171/171 OK**（`AURORA_TEST=1`） | `tests/test_amazon_{adapter,client,contract,payload,pipeline,prompts,ui_page}.py` |
| 离线契约测试 | **27/27 OK** | `tests/test_amazon_contract.py` |
| 全量回归（verify_regressions） | **55/55 模块 PASS**（含 Amazon 7 模块） | `docs/regression-verification.json` |
| 离线回归（test_offline） | **234 tests OK** | `tools/test_offline.py`（已接入 7 个 Amazon 模块） |
| 编译 | `compileall -q src` **exit 0** | — |
| 全平台按钮审计 | **17 页 / 460 回调，ERROR 0**；Amazon 4 页 114 回调 0 错 | `docs/ui-button-audit.json` |
| UI/后端交叉审计 | **9/9 PASS** | `tools/verify_amazon_ui.py`（9 项断言） |
| 13 页按钮遍历 + 截图 | **27/27 按钮无异常**；截图已更新 | `docs/design/amazon-listing-page.png`（275,673 B） |
| 真实写入（Lead 执行） | **writes=2**，两次 `code=0` | `docs/amazon-write-verification.json`（runs=5） |
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

---

## 4. UI 与交叉审计

### 4.1 13 页与截图

- `src/aeclone/platforms.py`：`PageItem(13, "Amazon 刊登", "刊登", ...)`；`_switch` 落点 `_page_idx=13`。
- `tools/amazon_ui_shots.py`（`AURORA_TEST=1` + `ui_sandbox`）：网络 mock、线程/文件选择器禁用；
  **27/27 按钮 on_click 无异常**；NOT_READY 分支保留（页面未实现/分发表未接时打印 NOT_READY 退出 0）。
- 截图：`docs/design/amazon-listing-page.png`（1560×2200，275,673 B，
  sha256 `d8f62036978749141371fadc01510bd19bd86e2776702f780515ba630d6cdedf`）；
  最新 UI 已包含「＋添加卖点」「当前 5/10 行」「卖点 n/700」计数。

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

---

## 5. 限制（如实列出）

1. 只读核验不覆盖写链路；写证据由 Lead 单独产出（`docs/amazon-write-verification.json`）。
2. 违禁词接口本轮 `data=null`：只证明端点可调用，**不能证明词库命中规则**。
3. 产品类型预测 best-effort：长标题返回 `code=-1`，本轮用短词验证可用路径。
4. 品牌列表为空（未做品牌备案），空数组视为端点正常。
5. AI 文案为草稿态：标题 152 字符（>150 警告）、描述 2672 字符（>2000 建议；schema 硬上限
   20000 字节）、库存/价格为 0——均为 warnings，不阻断草稿保存；发布前需按业务补价/补库存。
6. UI 沙箱使用 fake client / mock 网络，证明「可渲染、可点击、不崩溃、不真实写」；
   真实数据渲染由只读核验 + 真实写回读间接覆盖；截图为静态快照。
7. `tests/test_amazon_ui_page.py` 按文件顶部说明需在 `AURORA_TEST=1` 下运行；
   `tools/verify_regressions.py` / `tools/test_offline.py` 已对 Amazon 模块自动注入该环境变量。

---

## 6. 外部并发改动（不计入 Amazon 结论）

另有外部进程并发修改 Alibaba 文件，以下失败与 Amazon 无关，单列不计入本报告结论：

| 现象 | 原因 | 归属 |
|---|---|---|
| `tests/test_platforms.py::test_alibaba_has_trade_and_no_aliexpress_pages` 失败（Alibaba 导航中无 "FOB"） | 外部进程把 Alibaba 价格页从导航移除 | 非 Amazon（未改 Alibaba） |
| `tests/test_ui_pages.py` AI 文案页 `PermissionError: .../AuroraList/config.tmp` | 沙箱禁止写真实用户配置目录（环境限制） | 环境/沙箱 |

其余全量回归（55 模块）与离线回归（234 tests）均 PASS；`tests/test_ui_redesign.py` 20/20 OK。

---

## 7. P0 / P1 / P2

- **P0：无。** 只读链路、真实写 #1/#2、UI 13 页、唯一写入口与护栏均通过。
- **P1：无。** 交叉审计 9/9、按钮审计 ERROR 0、全量回归 55/55、离线 234 tests 全绿。
- **P2（备注，不阻塞）**：
  1. `test_amazon_ui_page.py` 单独运行需 `AURORA_TEST=1`（已写入验收脚本环境）。
  2. AI 文案草稿存在 5 条 warnings（标题/描述长度、价格 0、库存 0、Generic 品牌），发布前处理。
  3. 按钮审计中 DEFERRED/EXTERNAL 为沙箱语义（线程不执行/打开系统外链），非错误。

---

## 8. 复现命令

```bash
# 1) 真实账号只读核验（需 .tmp-probe/cookies.txt 或 DXM_COOKIE_PATH）
.venv/bin/python tools/verify_amazon.py                    # 期望 18/18 PASS

# 2) Amazon 测试（7 文件 171 用例，AURORA_TEST=1）
AURORA_TEST=1 .venv/bin/python tests/test_amazon_contract.py
for f in tests/test_amazon_*.py; do AURORA_TEST=1 .venv/bin/python "$f"; done

# 3) 13 页按钮遍历 + 截图
AURORA_TEST=1 .venv/bin/python tools/amazon_ui_shots.py     # 27 按钮 / 截图

# 4) UI/后端交叉审计（签名/overrides/唯一写入口/预演拒绝/关键控件）
AURORA_TEST=1 .venv/bin/python tools/verify_amazon_ui.py    # 期望 9/9 PASS

# 5) 全平台按钮审计（含 Amazon 13 页）
AURORA_TEST=1 .venv/bin/python tools/button_audit.py --json docs/ui-button-audit.json

# 6) 回归 + 编译
.venv/bin/python tools/verify_regressions.py                # 55/55 PASS
.venv/bin/python tools/test_offline.py                      # 234 tests OK
.venv/bin/python -m compileall -q src
```

---

## 9. 证据文件

| 文件 | 说明 |
|---|---|
| `docs/amazon-readonly-verification.json` | 18 项只读核验证据（PASS 18/18） |
| `docs/amazon-write-verification.json` | Lead 真实写证据（runs=5, writes=2, 两次 code=0） |
| `docs/regression-verification.json` | 全量回归 55 模块结果（原有 list 结构） |
| `docs/ui-button-audit.json` | 17 页 / 460 回调审计（ERROR 0） |
| `docs/design/amazon-listing-page.png` | 13 页沙箱截图（275,673 B） |
| `tools/verify_amazon.py` | 独立只读核验（白名单 + 写入口硬断言） |
| `tools/verify_amazon_ui.py` | UI/后端交叉审计（9 项断言，机器证据在 build/verification/） |
| `tools/amazon_ui_shots.py` | 13 页按钮遍历 + 截图（NOT_READY/控件清单降级） |
| `tests/test_amazon_contract.py` | 离线契约 + 防回退 + 证据结构测试 |
| `tools/button_audit.py`、`tools/verify_regressions.py`、`tools/test_offline.py` | 已接入 Amazon 用例/模块 |

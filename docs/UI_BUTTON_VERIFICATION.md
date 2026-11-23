# AuroraList · 全页面 / 全按钮 / 全控件验证与截图审计

> 验证者：ui-button-verifier（独立验证，**未改产品代码**；仅新增本报告、`docs/design/ui-verify/` 下截图/清单/harness 脚本与结果 JSON）
> 验证时间：2026-11-22 01:36–01:52（Asia/Shanghai）
> **代码快照（本报告所有 `file:line` 以此为准）**：
> - `src/aeclone/ui/app.py` — **11014 行**，sha256 **`c5e1dbe22461aee4e6de8a5250fa16343c66a6ae67966c344f3ac05a34a6c85f`**（Lead 确认的定稿版）
> - `src/aeclone/platforms.py` 148 行 sha256 `1dcaf10d…`；`src/aeclone/services/alibaba_prompts.py` sha256 `f8ad3620…`；`tools/ui_sandbox.py` sha256 `73c74ff0…`
> - `tools/button_audit.py` / `tools/verify_ui_options.py` 已由 Lead 扩到 13 页（sha256 `1ac18dbe…` / `8e3a7710…`），本报告独立重跑并交叉验证
> - 环境：macOS，`.venv`（Python 3.14.7），Flet 0.25.2；隔离沙箱 `tools/ui_sandbox.py`（`sandbox()/build_app()/walk()/label()`，全平台授权覆盖、网络/进程禁用、临时配置目录）
> **边界**：只读 + 沙箱；未联网、未真实保存商品、未调用付费 AI、未读取/打印 Cookie/api_key；**没有把「测试环境限制」当缺陷**（见第五节）。

---

## 一、结论摘要

1. **13 个页面（含 12 号「Alibaba 刊登」）在 dark / light 两主题下全部可构建**：共 **1662 个页面控件**（按钮 147 / 开关 53 / 下拉 35 / 输入 106 / 分栏 10），页面内回调 **316 个**；另有壳层（平台栏+侧栏+顶栏）控件 **304 个 / 回调 44 个**。
2. **全量回调执行 486 次，0 异常、0 超时**（`OK 313 / DEFERRED 153 / NATIVE 11 / EXTERNAL 9`，`ERROR 0`）。官方 `tools/button_audit.py` 独立重跑 **411 个回调 0 error**；`tools/verify_ui_options.py` 重跑 **224 个开关/下拉两态 0 error、0 假保存**。
3. **无 P0**。上一轮报告（`docs/UI_INCOMPLETE_AUDIT.md` P0-1）指出的「Alibaba 平台侧栏启动/⌘R 会误跑速卖通 SMT 管线」在当前快照**已修复并经独立验证**：`_start` 统一守卫 → toast + 跳 12 号页，AliExpress 平台行为不变（`app.py:2362-2372`）。
4. 本次新发现 **P1 3 项 + P2 11 项**（其中 6 项 P2 为对既有清单的当前快照复核），集中在：12 号页空输入/错误归因提示、选商品后类目路径不同步、预设下拉「选了不生效需点套用」的误导模式、页面 9/10/11 的入口一致性。**另：既有清单的 2 项 P1 在当前快照已修复并复核通过**——FOB 定价默认值覆盖（复核见第六节）、侧栏启动/⌘R 平台守卫（见 3.4）。
5. **Lead 重点核验的 4 项全部通过**（详见第三节专项）：AI 预演 = 后台线程 + 分步结果 + 失败原因可见；保存 = 先弹二次确认、取消不写入（mock 验证、无联网）；4 个新控件均有回调且点击有明确反馈；Alibaba 侧栏启动/⌘R = 跳 12 号页 + toast，AliExpress 仍走原流程。
6. 截图 **21 张**（13 页 + 12 号页 5 个 Tab + 双主题/跨平台/锁定态证据）已更新到 `docs/design/ui-verify/`，清单见第七节。

**给 Lead 的一句话**：当前 UI 可交付；建议只在发布前修 P1 前 3 项（12 号页空搜索反馈、选商品类目同步、FOB 默认值），其余为体验/一致性项，可排后续版本。

---

## 二、总览统计

### 2.1 逐页控件与回调（Alibaba 平台，dark=light，两主题控件数完全一致）

| # | 页面 | 控件总数 | 按钮 | 开关 | 下拉 | 输入 | 分栏 | 回调 | 空回调下拉 | 空回调输入 | 禁用 | 执行数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 概览 | 181 | 16 | 0 | 0 | 1 | 0 | 19 | 0 | 0 | 0 | 19 |
| 1 | 店铺与模板 | 80 | 9 | 0 | 3 | 0 | 0 | 12 | 0 | 0 | 0 | 11 |
| 2 | 类目与属性 | 87 | 7 | 1 | 3 | 11 | 1 | 21 | 0 | 1 | 0 | 21 |
| 6 | 价格 / 阶梯 / 批发 | 162 | 11 | 3 | 4 | 16 | 1 | 30 | 1 | 3 | 4 | 26 |
| 7 | SKU · 库存 · 套装 | 76 | 3 | 5 | 0 | 6 | 1 | 14 | 0 | 0 | 0 | 28 |
| 8 | 发布队列 | 39 | 4 | 0 | 0 | 0 | 1 | 4 | 0 | 0 | 0 | 4 |
| **12** | **Alibaba 刊登** | **143** | **15** | **1** | **5** | **30** | **1** | **45** | **2** | **3** | **0** | **45** |
| 3 | AI 文案 | 243 | 11 | 8 | 10 | 8 | 1 | 30 | 7 | 0 | 0 | 60 |
| 5 | AI 图片 | 214 | 15 | 27 | 5 | 17 | 1 | 64 | 0 | 0 | 0 | 143 |
| 4 | 内容审查 | 54 | 1 | 5 | 0 | 5 | 0 | 11 | 0 | 0 | 0 | 22 |
| 10 | 任务与日志 | 84 | 12 | 1 | 1 | 1 | 1 | 15 | 0 | 0 | 0 | 32 |
| 9 | 图片工作台 | 132 | 23 | 2 | 2 | 4 | 1 | 25 | 2 | 4 | 0 | 50 |
| 11 | 设置 | 167 | 20 | 0 | 2 | 7 | 1 | 26 | 0 | 3 | 0 | 25 |
| | **合计** | **1662** | **147** | **53** | **35** | **106** | **10** | **316** | **12** | **14** | **4** | **486** |

> 「执行数」= 默认场景 + 有内容的 populated 场景（页 3/4/5/7/9/10 额外跑一次），每个 on_click/on_change/on_submit 各执行一次。
> 空回调「下拉 12 / 输入 14」不代表死控件，逐条判定见 2.3。

### 2.2 回调执行结果

| 指标 | 数值 | 说明 |
| --- | --- | --- |
| 执行次数 | **486** | 13 页 × 默认场景（+6 页 populated 场景） |
| `OK` | 313 | 同步完成 |
| `DEFERRED` | 153 | 启动后台线程完成（沙箱 patch 了 `Thread.start`，**最终网络结果不可见**，见第五节） |
| `NATIVE` | 11 | 调用原生文件选择器（沙箱 patch，属预期） |
| `EXTERNAL` | 9 | 打开外部 URL/文件（打开店小秘/日志/导出，属预期） |
| `ERROR` / `TIMEOUT` | **0 / 0** | 无异常、无卡死 |
| 可观测反馈类型 | CONFIG 173 / DEFERRED 137 / TOAST 90 / UI 32 / DIALOG 29 / SILENT 11 / NATIVE 11 / EXTERNAL 3 | `SILENT` 11 条逐条判定见 2.4 |

### 2.3 空回调清单（不是「死控件」）

| 页面 | 控件 | 数量 | 判定 |
| --- | --- | --- | --- |
| 3 AI 文案 | 「提示词预设」下拉（4 个能力卡 + 3 个预设库） | 7 | **设计为「选中→点套用」**；下拉本身无 label、无 on_change → 有误导风险，见 P2-5 |
| 6 价格 | 区域倍率模板下拉 `lib_dd` | 1 | 设计为「选中→点应用/删除」；无 label → P2-10 |
| 9 图片工作台 | 「商品状态」「本页排序」下拉 | 2 | 值在点「加载」时才读取（无 on_change）→ P2-6（提示方式） |
| 12 Alibaba 刊登 | 「商品状态」「保存动作」下拉 | 2 | 值在点「读取商品 / 保存到 Alibaba」时才读取 → P2-2 |
| 12 Alibaba 刊登 | AI 开关 `ai_use` | 1 | 值在点 AI 按钮时读取；属正常设计（P2-2 一并说明） |
| 2/6/12/9/11 | 搜索/名称类 TextField | 14 | 由旁边按钮读取值，属正常设计 |

**结论**：没有「有按钮但完全无回调」的控件；`switch/dropdown` 全部可用；「空回调」均为「值读取型」设计，其中 11 处存在提示不足的体验问题（已分级）。

### 2.4 `SILENT` 11 条逐条判定（既不漏报也不误报）

| 页面/控件 | 判定 | 依据 |
| --- | --- | --- |
| p0 刷新数量 | **非缺陷** | 沙箱把 `_refresh_store_counts` patch 为 no-op；真实环境会刷新 KPI |
| p6 计算 | **非缺陷** | 进页已自动算过一次，重复点击输出相同（幂等） |
| p7 预览（套装） | **非缺陷** | 进页已自动渲染，重复点击输出相同 |
| p3 清空（详情本地图） | **非缺陷** | 初始列表为空，清空无可视变化 |
| p9 清理已完成 | **P2-6** | 队列为空时无提示（可接受但建议 toast） |
| p9 上一页 | **P2-6** | 第 1 页点击无提示，与「下一页」的「已经是最后一页」不一致 |
| **p12 搜索（类目）** | **P1-1** | 空关键词点击完全无反馈（唯一确认的真实「点不动」） |

### 2.5 壳层（全局导航/平台/顶栏/快捷键）——31 项检查 0 失败

| 项 | 结果 |
| --- | --- |
| 13 个导航项逐个点击 | 每个都切到对应页且面包屑正确 |
| 导航搜索「库存」 | 只保留 SKU 页（7），回车直接打开；无匹配显示「未找到功能，请换个关键词」 |
| 平台栏 5 个徽标 | Alibaba/AliExpress/Amazon/TikTok/Shopee 逐个点击均正确切换 |
| 顶栏「选择店铺」/「任务与日志」/主题按钮 | 跳页/开页正确；主题 dark↔light 切换并落配置、toast 提示、切换后导航与页面仍可用 |
| 侧栏「日志监控」 | 打开/关闭/再次打开均正常（无「关掉后再也打不开」） |
| ⌘L / ⌘F / ⌘R / 无修饰键 R | ⌘L→页 10；⌘F→全局搜索弹窗；⌘R 受平台守卫；无修饰键不触发 |
| 平台切换（→AliExpress→回 Alibaba） | 导航 12↔13 项、label 随平台、面包屑/平台栏高亮/锁定图标全部刷新；页 8 在 Alibaba=只读 ICBU 队列、AliExpress=SMT 队列（含批量发布/重试入口） |

> 平台/锁定专项脚本共 **90 项检查 0 失败**（含未授权平台的平台级锁定页、功能包锁定页、切回后锁定态刷新）。

### 2.6 交叉验证（与官方工具）

| 工具 | 本次结果 | 与 Lead 数据对比 |
| --- | --- | --- |
| `tools/button_audit.py`（13 页） | **411 回调**：`OK 267 / DEFERRED 127 / NATIVE 9 / EXTERNAL 8 / ERROR 0` | Lead：404 回调（本快照新增 4 个页 12 控件后为 411），ERROR 均为 0 ✅ |
| `tools/verify_ui_options.py`（13 页） | **224 态**：`CONFIG_SAVED 165 / NO_CONFIG_CHANGE 59`，ERROR 0、假保存 0 | Lead：210 态 153/57（新增控件后 224/165/59），趋势一致 ✅ |
| 本报告 harness | 486 次执行 0 ERROR | 与上面两项互相印证 |

`NO_CONFIG_CHANGE` 59 条 = 「把值设回当前值」的无效变更；逐控件检查后**没有任何开关/下拉在所有取值下都不落盘**（仅 3 个纯本地状态控件：图片工作台「全选本页」「只看有图」、日志页「级别过滤」），即**不存在死开关**。

---

## 三、Lead 重点核验项（独立复现结果）

复现脚本：`docs/design/ui-verify/scripts/ui_page12_deep.py`（63 项检查，62 PASS / 1 FAIL）+ `ui_platform_switch.py`（90/90）+ `ui_shell.py`（31/31）。结果 JSON 在 `docs/design/ui-verify/results/`。

### 3.1 12 号页「AI 预演（不保存）」行为 —— ✅ 通过

- 控件：`app.py:9849-9852`（按钮）→ `ai_preview` `app.py:9761-9786`。
- 实测：点击后状态立即变为「AI 预演中（不保存）…」，随后 `线程启动`（`DEFERRED`），结果区经 `_render_pipeline`（`app.py:9723-9749`）展示：完成标签（如「AI 预演（失败）」）、逐步骤行（图标+步骤名+详情）、失败原因文本；**不弹保存确认、不写商品**。
- 沙箱内（无 Cookie）实测结果区：步骤 `连接 Alibaba` + `失败原因：创建客户端失败：没有 Alibaba/店小秘 Cookie：请在「设置 → Alibaba 刊登」粘贴 Cookie…` → 失败原因可见、不是静默。
- 注意：AI 开关默认关闭，此时**不调用付费 API**；开关文案明确写「会调用付费 API，默认关闭」（`app.py:9717-9721`）。

### 3.2 12 号页 4 个新控件 —— ✅ 全部有回调且可点

| 控件 | 定义/回调 | 实测反馈 |
| --- | --- | --- |
| 「按 ID 打开」 | 按钮 `app.py:9880-9882`，`open_by_id` `app.py:9492-9516` | 空 ID → toast「填一个商品 ID 再打开」；填 ID → 立即显示「正在读取商品…」，失败显示「打开失败：<原因>」 |
| 「生成属性预设模板」 | `app.py:9891-9893`，`gen_attr_template` `app.py:9316-9341` | 未选类目 → 「生成模板失败：先搜索并选择一个类目」（可操作提示） |
| 「AI 建议缺失属性」 | `app.py:9894-9896`，`ai_suggest_attrs` `app.py:9343-9420` | 未选类目 → 「AI 建议失败：先搜索并选择一个类目」；成功路径只接受类目候选值并写「属性预设」 |
| 「Alibaba 行业预设 / 套用预设」 | `app.py:5912-5970`（AI 文案页与 12 号页共用），12 号页入口 `app.py:9828-9834` | 下拉含 6 类行业预设；点「套用预设」写入 `title_prompt/detail_prompt/sku_prompt/review_text_prompt/alibaba_keyword_prompt` 5 个键并 toast「已套用 Alibaba 预设：集装箱泳池 / 泳池设备（主推）…」 |

### 3.3 「保存到 Alibaba」二次确认与取消 —— ✅ 通过（mock 验证，未联网）

- 控件：按钮 `app.py:9965-9967` → `do_save` `app.py:9622-9714`。
- 流程（实测）：`prepare_save` → 校验 issues（失败则红字「校验未通过」且不弹窗）→ 通过后创建 `AlertDialog`（`app.py:9694-9704`）：商品 ID / 动作（op=1 保存草稿，op=2/3 警告色）/「预演校验：必填项全部满足」/「保存会写入店小秘/平台，不是本地预演。」+「取消 / 确认保存」。
- 取消实测：弹窗关闭、**未调用保存 pipeline**；`run_alibaba_product(dry_run=False)` 的真实保存另有三重护栏（`services/alibaba_publish.py:83-86 _forced_preview`、`:89-95 _require_license`、UI 侧仅确认后才启动线程）。
- **按 Lead 要求未点击「确认保存」**：确认路径的静态审查显示会走 `run_alibaba_product(dry_run=False, op=op, use_ai=False)`（`app.py:9662-9670`），受 `AURORA_NO_SAVE/AURORA_TEST` 与授权 guard 拦截；真实写入风险未在本验证中触发。

### 3.4 Alibaba 侧栏「启动任务」/ ⌘R —— ✅ 已修复并验证

| 入口 | 代码 | 实测 |
| --- | --- | --- |
| 侧栏按钮 | `app.py:809-818` → `_toggle_run` `app.py:2184` → `_start` 守卫 `app.py:2362-2372` | Alibaba 下点击：toast「Alibaba 商品请在「Alibaba 刊登」页选择并保存（批量管线仅用于速卖通）」+ 日志 WARN + 跳 12 号页；`_running` 未置位 |
| ⌘R | `app.py:8174-8189` `_on_key` → `_toggle_run` | 同上（跳 12 + toast） |
| 概览页入口 | `app.py:1549-1560`（「去 Alibaba 刊登」/选择商品/搜索全部商品均重定向） | 「去 Alibaba 刊登」→ 12 号页 ✅ |
| AliExpress 平台 | 守卫条件 `active_platform.id == "alibaba"` 不成立 | 点击后不被 Alibaba 守卫拦截，走原 `_start`（跳页 10，随后按授权状态提示；沙箱授权覆盖下不启动真实任务） |
| 服务层兜底 | `_start` 守卫在 UI 与快捷键之外；批量管线入口另有平台断言（见 `docs/UI_INCOMPLETE_AUDIT.md` 残留说明已消解） | ✅ |

### 3.5 截图更新 —— ✅ 21 张（含 12 号页 5 Tab + 概览 Alibaba 当前代码）

---

## 四、分级问题

> 每条含：页面 / 控件 label / file:line / 现象 / 期望 / 验收。`file:line` 基于首部快照；若 app.py 后续再改，行号可能偏移。
> 分页标签：P1 = 用户会被误导、操作无效或可能写错数据；P2 = 体验/文案/一致性。

### P0（0 项）

- 上轮 `docs/UI_INCOMPLETE_AUDIT.md` 的 P0-1（侧栏启动/⌘R 在 Alibaba 平台误跑 SMT 管线）**在本快照已修复**：`app.py:2362-2372` 统一守卫 + `app.py:8174` 快捷键同路径，实测通过（见 3.4）。**本次无 P0。**

### P1（3 项新发现 + 2 项既有复核）

#### P1-1 12 号页「搜索」在关键词为空时完全无反馈（本次新发现）

- **页面**：Alibaba 刊登（12）→ 类目 Tab
- **控件 label**：`搜索`
- **file:line**：按钮 `app.py:9888-9889`；处理函数 `do_search` `app.py:9132-9135`（`kw = ...; if not kw: return`）
- **现象（实测）**：不填「搜索类目（中文/英文）」直接点「搜索」→ 无 toast、无状态文案、无任何变化；用户无法判断是「没搜到」还是「按钮失效」。同页其他入口（读取必填属性/生成模板/AI 建议）都有明确提示，唯独搜索静默。
- **期望**：空关键词时 toast「请输入类目关键词（中文/英文）」并保持不请求；或输入框为空时禁用按钮。
- **验收**：空输入点击 → 出现可见提示且不发任何网络请求；非空点击 → 「搜索类目中…」→ 结果/失败提示。

#### P1-2 12 号页「展开子类目」未选类目时错误归因（本次新发现）

- **页面**：Alibaba 刊登（12）→ 类目 Tab
- **控件 label**：`展开子类目`
- **file:line**：按钮 `app.py:9897-9899`（lambda 直接传空 `categoryId`）；`_load_children` `app.py:9186-9223`，失败文案 `:9223`
- **现象（实测）**：未选类目点「展开子类目」→ 状态显示「加载子类目失败：没有可用 Cookie：请先…」——真正原因是**没选类目**，却把锅甩给 Cookie，用户会去反复粘贴 Cookie。对比同排「读取必填属性」`app.py:9229-9238` 会先提示「先搜索并选择一个类目」。
- **期望**：与「读取必填属性」一致，先校验 `alibaba_category_id`，为空则提示「先搜索并选择一个类目」，不发起请求。
- **验收**：未选类目点击 → 提示「先搜索并选择一个类目」；已选类目 → 加载子类目/失败原因准确。

#### P1-3 选商品后类目路径 / 名称不同步（复核 `UI_INCOMPLETE_AUDIT` P1-11，当前快照仍存在）

- **页面**：Alibaba 刊登（12）→ 连接与商品 Tab
- **控件 label**：商品行 `选择`（`load_products` 内）
- **file:line**：`set_product` `app.py:9479-9491`（只写 `alibaba_test_product_id`、`alibaba_category_id`）；类目展示 `cat_now` `app.py:9171`；`_pick_category` 才同步 `alibaba_full_cid/name`（`app.py:9174-9184`）；payload 侧 `services/alibaba_payload.py resolve_category`（约 213-251）
- **现象（静态复核）**：先搜类目 A（写 fullCid=A 路径）→ 再从列表「选择」类目 B 商品：`categoryId` 变 B，但 `alibaba_full_cid`/顶部「当前类目」仍显示/沿用 A → 预演可能通过但保存包 fullCid 与 categoryId 不同源。
- **期望**：选商品后同步展示并写入该商品类目路径（列表无 fullCid 时清空并提示重选）；`prepare_save` 检测冲突时提示。
- **验收**：选商品后顶部类目立即更新；预演展示的 fullCid 末段与 categoryId 一致。

#### P1-4 区域倍率模板「删除」空选中假成功（复核 `UI_INCOMPLETE_AUDIT` P2-6，升级为 P1）

- **页面**：价格（6）→ 区域倍率模板
- **控件 label**：`删除`（`lib_dd` 未选中）
- **file:line**：`app.py:6814`（`lib_dd` 初始空）、`app.py:6850-6856`（`lib_del` 过滤无匹配后仍 toast「已删除」）
- **现象（实测）**：模板列表为空/未选择时点「删除」→ 弹出「已删除」，实际什么都没删；「应用」未选中则提示「未找到模板」（提示正确）。
- **期望**：未选中时 toast「请先选择模板」；成功删除后刷新选中态与列表。
- **验收**：空选中删除 → 无「已删除」假成功；有选中删除 → 列表与下拉同步移除。

#### P1-5 发布队列单条「发布」无二次确认（复核 `UI_INCOMPLETE_AUDIT` P1-10，静态复核）

- **页面**：发布队列（8）→「查看待发布/发布失败」弹窗（速卖通平台）
- **控件 label**：行内 `发布`
- **file:line**：`app.py:3020-3023`（`trailing=ft.TextButton("发布", on_click=lambda … self._publish_one(pid)`)`）；`_publish_one` `app.py:2881`；同页批量发布/重试走 `_confirm` `app.py:7137-7151`
- **现象（静态）**：批量有确认，单条一点即提交发布；发布后进入「发布中 → 审核中」，短时间不可撤销。
- **期望**：单条发布同样弹确认（商品 ID/标题/动作）。
- **验收**：单条点击先出确认弹窗，取消不发请求。
- **说明**：该控件在需联网加载的弹窗内，本次沙箱未实测点击（列表为空），属**静态复核**，见第五节。

### P2（11 项）

#### P2-1 12 号页「套用预设」后页面重建并跳回第 1 个 Tab（上下文丢失）

- **页面**：Alibaba 刊登（12）→ 任一 Tab（在「预演 / 保存」Tab 使用）
- **控件 label**：`套用预设`
- **file:line**：`app.py:5940-5963`（apply 末尾 `self._switch(self._page_idx)`）
- **现象（实测）**：在「预演 / 保存」Tab（第 5 个）套用预设 → 配置写入成功、toast 正常，但页面重建后回到「连接与商品」Tab，用户需重新切回。
- **期望**：套用后直接刷新相关控件（或重建时保留当前 Tab）。
- **验收**：套用后仍停留原 Tab，相关提示词输入已更新。

#### P2-2 12 号页三个「值读取型」控件改完必须再点按钮才生效（无即时反馈）

- **页面**：Alibaba 刊登（12）
- **控件 label**：`商品状态`、`保存动作`、AI 开关
- **file:line**：`app.py:9465-9472`（`prod_state` 无 on_change）、`app.py:9597-9604`（`op_dd` 无 on_change）、`app.py:9717-9721`（`ai_use` 无 on_change）
- **现象（实测）**：把「商品状态」从草稿切到在线，列表不刷新也没有提示（必须再点「读取商品」）；切换「保存动作」无任何反馈；AI 开关切换无即时说明。均为设计上的「按钮读取值」，但缺少「改动后需点哪个按钮生效」的提示。
- **期望**：状态切换自动重载（或行旁提示「点读取商品生效」）；保存动作切换显示影响提示；AI 开关切换显示「将调用付费 API」确认。
- **验收**：改状态后列表自动刷新或出现明确提示；其它两项改动有可见反馈。

#### P2-3 12 号页「测试连接」用硬编码商品 ID 兜底

- **页面**：Alibaba 刊登（12）→ 连接与商品 Tab
- **控件 label**：`测试连接`
- **file:line**：`app.py:9114-9122`（`product_detail(... or "220114598434958580")`）
- **现象（实测）**：未选择/填写商品 ID 时点「测试连接」会去读固定 ID `220114598434958580`；该商品在别的店铺/被删除后会显示「连接失败」，误导为 Cookie/网络问题。
- **期望**：无商品 ID 时用轻量只读接口（列表 counts）验证连接，或提示「先选择商品再测试」。
- **验收**：无商品 ID 时测试连接不依赖固定商品，失败文案准确。

#### P2-4 12 号页两组「预演/保存」按钮并列，语义重复

- **页面**：Alibaba 刊登（12）→ 预演 / 保存 Tab
- **控件 label**：`预演（不保存）`+`保存到 Alibaba` / `AI 预演（不保存）`+`AI 生成并保存`
- **file:line**：`app.py:9962-9967`（手动组）、`app.py:9849-9853`（AI 组）
- **现象（实测）**：四个按钮并列，差别只在「是否用 AI 生成文案（付费）」；用户需自己读说明。
- **期望**：合并为一组（保存前勾选「用 AI 生成文案（付费）」）或分组标题明确区分。
- **验收**：12 号页只有一条明确的保存入口（或两组有清晰分组与文案）。

#### P2-5 AI 文案页 7 个「提示词预设」下拉：选中不生效、需另点「套用」，且下拉无 label

- **页面**：AI 文案（3）
- **控件 label**：4 个能力卡的「提示词预设」下拉 + 3 个「标题/详情/SKU 预设」下拉
- **file:line**：`app.py:6117-6145`（能力卡 ×4）、`app.py:6151-6170`（预设库 ×3）
- **现象（实测）**：下拉无 `label`（只有旁边 muted 文本「提示词预设」），且无 `on_change`——用户选了行业后输入框不变，必须再点「套用」才写入；对应「空回调下拉 7 个」。
- **期望**：选中即套用（或下拉旁明确「选好后点套用生效」）；补 `label`。
- **验收**：选中后 2 秒内提示词输入框更新，或出现明确操作提示；下拉有可读标签。

#### P2-6 图片工作台「上一页」「清理已完成」空态无提示

- **页面**：图片工作台（9）
- **控件 label**：`上一页`、`清理已完成`
- **file:line**：`prev_page` `app.py:7269-7274`（无 else 分支）、按钮 `app.py:7432-7433`（lambda 直接 clear_done，无空队列提示）；对比 `next_page` `app.py:7276-7282` 有「已经是最后一页」
- **现象（实测）**：第 1 页点「上一页」无任何反馈；队列为空点「清理已完成」无反馈。
- **期望**：与「下一页」一致：`toast("已经是第一页")`；空队列时 `toast("没有已完成的任务", False)`。
- **验收**：两种空态点击都有可见 toast。

#### P2-7 任务与日志页「清空」与「清空日志」两个入口行为不一致

- **页面**：任务与日志（10）
- **控件 label**：`清空`（工具条）、`清空日志`（筛选行）
- **file:line**：`app.py:8029`（直接 `clear_logs`，无确认）vs `app.py:8044-8050`（确认弹窗后 `_clear_logs_ui`）
- **现象（实测）**：两个入口文案近似、行为不同：一个直接清、一个二次确认；误点「清空」不可恢复。
- **期望**：合并为一个入口并统一二次确认（或把「清空」改名「清空当前筛选」并说明）。
- **验收**：只有一组清空入口，或两个入口的文案/行为明确区分。

#### P2-8 设置「启动时打开」下拉缺 12 号页

- **页面**：设置（11）→ 界面与实例
- **控件 label**：`启动时打开`
- **file:line**：`app.py:8971-8976`（`_PAGES` 仅 0-11）
- **现象（实测）**：Alibaba 平台用户无法把「Alibaba 刊登」(12) 设为启动页；选项里的「价格/变种与库存」还是速卖通旧名。
- **期望**：按当前平台 `active_platform.pages` 动态生成；至少补 12 号页。
- **验收**：下拉包含当前平台全部页面（Alibaba=13 项）。

#### P2-9 设置页图床地址/密钥输入框 `visible=False`，但文案说「改这里」

- **页面**：设置（11）→ 图片与模型 → 图床
- **控件 label**：`图床地址`、`图床密钥`
- **file:line**：`app.py:8716-8724`（两个 TextField `visible=False`）；文案 `app.py:8828`「图片上传失败、或想换成自己的图床时改这里」
- **现象（实测）**：控件树中存在但不可见，用户按文案找不到可改位置。
- **期望**：显示输入框；或改文案为「联系管理员/暂不支持自定义」。
- **验收**：文案与实际可操作项一致。

#### P2-10 区域倍率模板下拉无 label / 无占位提示

- **页面**：价格（6）→ 区域倍率模板
- **控件 label**：（无 label 的 Dropdown，`lib_dd`）
- **file:line**：`app.py:6814`、`app.py:6854`
- **现象（实测）**：下拉初始为空且无 label，用户不知道这里是选模板；与旁边「模板名称」输入框语义混淆。
- **期望**：加 `label="选择模板"`，空列表时显示占位。
- **验收**：下拉有可读标签；无模板时显示空态提示。

#### P2-11 Alibaba 平台侧栏「启动任务」按钮文案/tooltip 与行为不一致

- **页面**：全局侧栏（Alibaba 平台）
- **控件 label**：`启动任务`（tooltip「启动 / 停止本次任务（⌘R / Ctrl+R）」）
- **file:line**：`app.py:809-818`；实际行为 `app.py:2362-2372`（toast+跳 12）
- **现象（实测）**：守卫已生效且 toast 清楚，但按钮本身仍写「启动任务/启动·停止」，用户以为会启动批量任务，实际是跳页；可发现性差（需要先点错一次）。
- **期望**：Alibaba 平台下按钮改「去 Alibaba 刊登」（图标同步）、tooltip 改「选择商品并保存到 Alibaba」；速卖通保持原样。
- **验收**：Alibaba 平台侧栏按钮文案=跳页语义；点击行为与文案一致。

---

## 五、不是缺陷：测试环境限制与「未实测」边界（避免误报）

| 现象 | 判定 | 说明 |
| --- | --- | --- |
| `DEFERRED` 153 次（后台线程） | **不是缺陷** | 沙箱 patch `threading.Thread.start`，无法看到线程最终网络结果；只验证了「点击会触发后台任务 + 有即时状态提示」。真实网络结果需真账号，见 `docs/ALIBABA_VERIFICATION.md` |
| `NATIVE` 11 次（文件选择器） | **不是缺陷** | 沙箱 patch `FilePicker.pick_files`；已验证回调接线与落配置（`verify_ui_options`/`UI_INCOMPLETE_AUDIT`），真实系统对话框需真机 |
| `EXTERNAL` 9 次（打开店小秘/日志/导出/充值） | **不是缺陷** | 沙箱把外部进程/`webbrowser.open` 记为 external；符合预期 |
| 后台任务在沙箱内报「没有可用 Cookie」 | **不是缺陷** | 隔离环境无真实配置；错误文案本身清晰可操作（这正是在验证「失败是否有提示」） |
| 未点击「确认保存」/「确认 AI 保存」 | **未实测，非缺陷** | 按 Lead 要求只验证弹窗出现与取消；真实写入由 publish-verifier/icbu-e2e 负责 |
| op=2/3/5、定时发布、失败重试到点 | **未实测** | 无真实写入证据，不在 UI 控件验证范围 |
| 授权状态 | **沙箱覆盖** | `license_platforms` 全平台覆盖仅用于隔离审计；锁定态另用部分/空 entitlement 单独验证（90 项检查） |
| 主题切换后的原生窗口 | **未实测** | Web 预览验证了配色与重建，原生窗口标题栏需真机 |
| 页 8 单条发布确认（P1-5） | **静态复核** | 弹窗需联网加载商品列表，沙箱内列表为空，未实测点击 |

---

## 六、已完整 / 已验证通过（避免重复改）

- **应用壳**：13 项导航（逐项点击/面包屑）、导航搜索（含空态）、平台栏 5 平台切换、顶栏（店铺/日志/主题）、侧栏日志监控（开→关→再开）、⌘L/⌘F/⌘R/无修饰键。
- **平台矩阵**：Alibaba↔AliExpress 切换后导航/页面/面包屑/高亮/锁定态全部刷新；Alibaba 页 8 = 只读 ICBU 队列（草稿/在线/合计 + 查看草稿/在线 + 去 Alibaba 刊登，无批量发布按钮）；AliExpress 页 8 = SMT 队列（含批量发布/重试）；未授权平台/未开通功能包 → 平台锁定页/功能包锁定页 + 「去授权网站开通」。
- **授权守卫**：Alibaba 平台侧栏启动/⌘R → toast + 跳 12，不启动批量任务；AliExpress 不触发该守卫。
- **12 号页主链路**（离线可验证部分）：商品状态两态/搜索框/读取商品/上一页/下一页（空态 toast 正确）/按 ID 打开/Cookie 提示（「没有可用 Cookie：请先…」可操作）/类目搜索（失败提示）/读取必填属性（未选类目提示正确）/生成属性预设模板/AI 建议缺失属性/类目预设/定价方式 4 态写显式标记/销售方式/保存动作 3 态/预演（不保存）空商品提示/保存二次确认+取消/AI 预演（后台线程+分步+失败原因）/AI 生成并保存确认弹窗（含付费提示+取消）/预设资料 5 类输入落盘/多行公司介绍。
- **定价方式默认值（复核既有 P1-13：已修复）**：`services/alibaba_payload.py:298-322` 增加「隐式默认」判定——未显式选择（`alibaba_price_type_explicit` 非 1）且未填阶梯价时，`priceType` 跟随商品原值；实测（隔离沙箱桩商品，默认 `config.DEFAULTS`）：原 `fob_price` → 载荷 `fob_price` 且 `sourcingTrade` 完整、`bulkDiscountPrices=[]`；原 `sku_price` → `sku_price`；只有显式把下拉切到 `ladder_price` 才改写（`app.py:9916-9928`）。**原 P1-13 不再成立。**
- **空回调结论**：无「有按钮无回调」控件；无死开关/死下拉（options 审计 224 态 0 异常，59 个 NO_CHANGE 均为设回原值或纯本地状态）。

---

## 七、截图清单（`docs/design/ui-verify/`）

> 全部由隔离沙箱 + `tools/preview_ui.py` 同款 Web 预览渲染，Playwright(chrome, headless) 截图；viewport 1560×2200；无真实配置/无联网。清单与 sha256 见 `manifest.json`。

| 文件 | 页面 | 说明 |
| --- | --- | --- |
| `00-overview-alibaba-dark.png` | 0 概览 | Alibaba 模式（ICBU 草稿/在线、去 Alibaba 刊登入口）**（当前代码，已更新）** |
| `01-store-templates-dark.png` | 1 店铺与模板 | |
| `02-category-attributes-dark.png` | 2 类目与属性 | |
| `03-price-tier-wholesale-dark.png` | 6 价格 / 阶梯 / 批发 | |
| `04-sku-inventory-dark.png` | 7 SKU · 库存 · 套装 | |
| `05-publish-queue-alibaba-dark.png` | 8 发布队列 | Alibaba=只读 ICBU 队列 |
| `06-ai-text-dark.png` | 3 AI 文案 | 含 Alibaba 行业预设入口**（当前代码，已更新）** |
| `07-ai-image-dark.png` | 5 AI 图片 | |
| `08-content-review-dark.png` | 4 内容审查 | |
| `09-tasks-logs-dark.png` | 10 任务与日志 | |
| `10-image-workbench-dark.png` | 9 图片工作台 | |
| `11-settings-dark.png` | 11 设置 | |
| `12-alibaba-listing-tab1-connect-products.png` | 12 Alibaba 刊登 | Tab1 连接与商品**（已更新）** |
| `12-alibaba-listing-tab2-category.png` | 12 | Tab2 类目（含生成属性预设模板/AI 建议缺失属性）**（已更新）** |
| `12-alibaba-listing-tab3-presets.png` | 12 | Tab3 预设资料**（已更新）** |
| `12-alibaba-listing-tab4-price-trade.png` | 12 | Tab4 价格与贸易**（已更新）** |
| `12-alibaba-listing-tab5-preview-save.png` | 12 | Tab5 预演 / 保存（含 Alibaba 行业预设 + AI 组）**（已更新）** |
| `13-overview-alibaba-light.png` | 0 概览 | light 主题 |
| `13-alibaba-listing-tab1-light.png` | 12 | light 主题 Tab1**（已更新）** |
| `14-overview-aliexpress-dark.png` | 0 概览 | AliExpress 平台（平台切换证据） |
| `15-category-locked-dark.png` | 2 | 未开通功能包的锁定页（授权锁定证据） |

辅助产物：
- `docs/design/ui-verify/manifest.json`：截图清单（页面/Tab/主题/平台/是否锁定/大小/sha256 前缀）。
- `docs/design/ui-verify/scripts/`：本次使用的 harness 脚本（`ui_full_audit.py` 13 页控件+回调、`ui_page12_deep.py` 12 号页专项、`ui_platform_switch.py` 平台/锁定、`ui_shell.py` 壳层、`capture_screens.py`+`shot_ui.py` 截图）。
- `docs/design/ui-verify/results/`：`final-ui-audit.json`（486 次执行明细）、`ui-page12-deep.json`（63 项）、`ui-platform-switch.json`（90 项）、`ui-shell.json`（31 项）、`ui-button-audit.json`（官方工具重跑）、`ui-options-verification.json`（官方开关/下拉重跑）、`final-audit.log`。

---

## 八、复现命令（工作区根目录，macOS）

```bash
# 0) 确认快照（应与本报告首部一致）
shasum -a 256 src/aeclone/ui/app.py     # c5e1dbe22461aee4e6de8a5250fa16343c66a6ae67966c344f3ac05a34a6c85f
wc -l src/aeclone/ui/app.py             # 11014

# 1) 官方工具（隔离临时配置）：13 页回调审计 + 开关/下拉两态
.venv/bin/python tools/button_audit.py --json /tmp/button-audit.json
mkdir -p /tmp/ui-verify-cwd && cd /tmp/ui-verify-cwd && \
  /Users/apple/Projects/alibaba-lister/.venv/bin/python \
  /Users/apple/Projects/alibaba-lister/tools/verify_ui_options.py   # 输出 ./docs/ui-options-verification.json

# 2) 本报告 harness（脚本已归档到 docs/design/ui-verify/scripts/）
.venv/bin/python docs/design/ui-verify/scripts/ui_full_audit.py --out /tmp/ui-audit.json
.venv/bin/python docs/design/ui-verify/scripts/ui_page12_deep.py       # 63 项（含 mock 弹窗，不联网）
.venv/bin/python docs/design/ui-verify/scripts/ui_platform_switch.py  # 90 项
.venv/bin/python docs/design/ui-verify/scripts/ui_shell.py            # 31 项

# 3) 截图（需 build/ui-preview-deps + 本机 Chrome；沙箱内起 127.0.0.1 预览）
.venv/bin/python docs/design/ui-verify/scripts/capture_screens.py
```

> 沙箱要点：`tools/ui_sandbox.py` 会把配置目录重定向到临时目录、patch 网络/进程/文件选择器/授权；所有脚本 **不会读写 `~/Library/Application Support/AuroraList/config.json`**，也不打印 Cookie/api_key。

---

## 九、未验证 / 需真机或真实账号（不夸大）

1. 真实网络下的后台任务结果：读取商品/类目搜索/子类目/必填属性/AI 预演/保存（本次只验证「点得动、有即时提示、失败原因可见」）。
2. 真实保存/发布（op=1/2/3/5）、定时到点、失败重试：不在本任务范围；按 Lead 要求只验证确认弹窗与取消。
3. 原生文件选择器真机行为（权限/取消/重复选择）与 Windows 平台间距/字体。
4. 原生窗口（标题栏、窗口尺寸设置、dark/light 原生渲染）。
5. 真实授权 token 下的平台/功能包组合（本次用 `license_platforms` 覆盖模拟）。
6. 页 8 弹窗内单条发布（需联网加载列表）。

---

## 十、给 Lead 的修复排序（发布前只修前 3 项）

1. **P1-1** 12 号页「搜索」空关键词提示（1 行 toast，成本最低、用户体感明显）。
2. **P1-2** 「展开子类目」未选类目先校验并提示（与「读取必填属性」对齐，避免误导用户去折腾 Cookie）。
3. **P1-3** 选商品后同步 `fullCid/名称/cat_now`（数据正确性风险；FOB 默认值已修复，本条独立）。
4. P2-1/P2-8/P2-9（Tab 跳转、启动页缺 12、图床控件隐藏）作为下一轮体验项。
5. P2-11（Alibaba 平台把侧栏按钮改名「去 Alibaba 刊登」）可顺手做，与已生效的守卫文案一致。

---

*本报告仅新增 `docs/UI_BUTTON_VERIFICATION.md` 与 `docs/design/ui-verify/` 下截图/脚本/结果；未修改 `src/`、`tests/`、配置或任何产品行为。*

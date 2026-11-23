# AuroraList / Alibaba 国际站 · UI 未完善功能审计

> 审计者：ui-auditor（独立审计，未改产品代码）
> 审计时间：2026-11-22 01:16–01:29（Asia/Shanghai）；**最终冻结版**（Lead 于 01:26 确认 UI 冻结）。
> 审计对象：`git HEAD 9a50a72` + 工作区未提交改动（本轮平台化 / Alibaba 刊登 UI 改造）。
> 冻结快照（下文所有 `file:line` 均以此为准）：
> - `src/aeclone/ui/app.py` — **10688 行**，sha256 `9d7dbae063e2883cdf7a6bac323f9150a69df7a26a995d93a75ec4baea275886`
> - `src/aeclone/platforms.py` — 148 行，sha256 `1dcaf10dae67760e0fc9f452dc3d4f20df45cdff3aaeeb8bb21fef338e1f434b`
> - `src/aeclone/services/alibaba_payload.py` 607 行 sha256 `d055f51d…`；`alibaba_publish.py` sha256 `03408866…`；`alibaba_pipeline.py` 481 行 sha256 `c7649090…`（新增）
> - 环境：macOS、`.venv` Python 3.14.7、Flet 0.25.2（无 pytest，脚本用 `.venv/bin/python` 运行）
> 范围：12 个页面构建器 + 12 号「Alibaba 刊登」页、导航/平台/授权锁定、顶部栏、图片工作台、设置页各 Tab。只读 + 隔离沙箱，未联网写操作，未读取/打印真实配置与密钥。**只新增本文件，未改 src/tests/config。**

---

## 一、总览

| 级别 | 数量 | 含义 |
| --- | --- | --- |
| **P0** | **1** | 用户会卡住/误解/数据风险，建议发布前修（原 2 项中 1 项已修复） |
| **P1** | **9** | 入口存在但没接线、常失效或平台错配（原 12 项中 3 项已修复/降级） |
| **P2** | **16** | 文案/一致性/空状态/死代码/体验问题 |
| 已完整 | 16 组 | 关键功能已可用，避免重复改（见第四节） |

**一句话结论**：12 个页面 + 12 号 Alibaba 刊登页在隔离沙箱中全部可构建、**550 个回调 0 异常**、无 TODO/未实现占位；Lead 本轮已把「概览主入口指向 Alibaba 刊登页」「Alibaba 保存失败语义」「商品列表搜索/分页」「价格与贸易控件」「速卖通定时发布 UI」修好。**当前唯一 P0 是残留绕过路径：侧栏全局「启动任务」按钮与 ⌘R 仍直接调用 `_toggle_run`，在 Alibaba 平台会启动店小秘 SMT（速卖通）批量管线**，与页面显示的平台/数字不一致，存在跨平台误操作风险；其次是 Alibaba FOB 定价被默认配置覆盖、旧授权缓存/锁定页、设置页锁、区域调价模板、单条发布无确认等 P1。

### 关键前提与边界

- 本轮已修复（审计**不再计为缺陷**，保留记录见第四节）：导航标签与页头对齐、新增 12 号页并移除设置页重复 Alibaba Tab、`_load_children` NameError、计数失败可见、发布页 Alibaba 只读化、原生文件选择器接线、保存 op 选择 + 二次确认、Alibaba 类目写入 payload、保存护栏、AI 刊登流水线、**概览 Alibaba 入口重定向、Alibaba 保存统一失败语义、商品列表搜索/分页/空态、FOB/批发/运费模板控件、速卖通定时发布 UI**。
- **真实保存/发布边界以独立验证者报告为准**：`docs/ALIBABA_VERIFICATION.md` 已证明「ICBU 直连链路对已存在商品 dry-run 通过；用户授权下 op=1 草稿真实保存 1 次成功」；**op=2/3/5、新建商品、定时到点、商品删除均无真实写入证据**。本报告不把「未实测」当「代码缺陷」，见第五节。
- 沙箱通过 `tools/ui_sandbox.py` 提供全平台授权覆盖（仅隔离环境，不代表服务端授权状态）；锁定页另用空 entitlements 单独验证。

---

## 二、分级清单

### P0（1 项，部分修复后的残留）

#### P0-1 Alibaba 平台仍存在绕过重定向的启动入口：侧栏「启动任务」/⌘R 直接跑 SMT 管线

- **页面**：全局侧栏、概览（0）、快捷键；服务层
- **控件/功能**：左下角「启动任务」按钮、⌘R / Ctrl+R、`_toggle_run`→`_start`→`_run_pipeline`
- **证据（file:line）**：
  - 本轮已修：概览卡 `app.py:1516-1595` 在 Alibaba 平台把「启动/选择商品/搜索全部商品」改为跳转 12 号页（`_start_action` 1549-1560、`_pick_or_search` 1557-1560、工作流 03 目标 1587-1595）、tiles 改为 ICBU 草稿/在线；图片工作台入队/处理已在 Alibaba 阻断（`:7304, :7335`）
  - **残留**：`app.py:810-815` 侧栏 `btn_run` 仍 `on_click=self._toggle_run`；`_toggle_run`（`:2183-2191`）**没有平台分支**，直接 `_start()`；`_start`（`:2361`）→`_run_pipeline`（`:3246`）→ SMT `product_page`（`:3381` 附近）→ `Publisher`（`:3420`）；图片工作台队列同样 `Publisher`（`:7650`）
  - SMT 端点为 `/api/smtProduct/*`：`src/aeclone/integrations/dianxiaomi.py:37,45-46`；`services/publisher.py` 无 `active_platform`/ICBU 分支（`grep` 0 命中）
  - 快捷键 `_on_key` 的 `"r"` 分支（`:8095` 附近）也直接 `_toggle_run`
- **现象与证据**：Alibaba 平台已经不再显示 SMT 批量卡，但**最显眼的全局启动按钮和 ⌘R 仍会走 SMT 管线**：当前 `store_id=9466975` 在 SMT 列表为空（只读核验值），用户点「启动任务」只会看到「共 0 个产品」；若配置的是真实 SMT 店铺（如 9190736），则会在 Alibaba 平台外观下读取/编辑/发布速卖通商品，属跨平台误写风险（与 `docs/ALIBABA_VERIFICATION.md` 的判定一致）。
- **期望行为**：`_toggle_run`/`_start` 层面即做平台分支：Alibaba → 提示并跳转 12 号页；非 Alibaba → 原 SMT 流程。服务层入口加平台断言，避免以后从其它路径误入。
- **修复建议**：在 `_toggle_run` 开头判断 `active_platform.id == "alibaba"` → `self.toast(...); self._switch(12); return`；`_start`/`_run_pipeline` 同样加断言；侧栏按钮在 Alibaba 平台改名「去 Alibaba 刊登」。
- **验收标准**：Alibaba 平台下点击侧栏启动、按 ⌘R，均不发出任何 `/api/smtProduct/*` 请求，而是跳转 12 号页并提示；切到速卖通平台后按钮/快捷键行为不变。

> **已修复记录（原 P0-2）**：Alibaba「保存到 Alibaba」已改走 `run_alibaba_product(dry_run=False, use_ai=False)`（`app.py:9362-9401`），统一 `is_failure_msg` 判定（`services/alibaba_pipeline.py:466-470`），`code=0` 但 msg 失败不再显示成功；成功/失败均展示分步结果。验收：桩 client 返回失败 msg 时 UI 红色失败、日志 ERROR。

---

### P1（9 项；P1-2/P1-3/P1-4 已在本轮修复或降级，见第四节）

#### P1-1 ICBU 侧「定时发布 op=5」仍未接通（速卖通链路的 UI 已补齐）

- **页面**：Alibaba 刊登（12）「保存动作」、任务与日志（10）「定时发布」、发布队列（8）
- **控件/功能**：ICBU `op=5 定时发布`、发布时间
- **证据（file:line）**：
  - 已修：速卖通运行设置新增定时发布开关/时间输入（`app.py:5046-5053`，`_card_run` `:5032`），写 `schedule_publish/schedule_time`；启动校验（`:2135-2147`）与日志页队列（`:7975-8018`）可读；**仅速卖通链路**（文案已注明）
  - 未接通：12 号页保存动作仍只有 op=1/3/2（`app.py:9307-9313`）；`services/alibaba_publish.py:80` 定义 `OP_SCHEDULE="5"`，`services/alibaba_pipeline.py:37,250` 接受 op=5，但 `build_alibaba_save` 载荷里**没有任何定时时间字段**（`alibaba_payload.py` 中 `schedule/publishTime` 0 命中）
- **现象与证据**：Alibaba 用户无法设置定时发布；即使直接传 op=5，也没有时间可提交，平台只能得到「立即」或未知状态。速卖通侧 UI 已可用，但「到点真实发布」仍属未实测（见第五节 B）。
- **期望行为**：若要支持 ICBU 定时，新增发布时间控件并在载荷中写入平台要求的定时字段；若不支持，从 pipeline/常量中移除 op=5 或明确标注「未支持」。
- **修复建议**：查 `docs/ALIBABA_RULES.md` 的 op=5 定时字段并在 `build_alibaba_save` 实现；12 号页保存动作下拉增加 op=5 + 时间选择；否则删除 `OP_SCHEDULE` 以免误用。
- **验收标准**：12 号页不出现未实现的 op=5（或出现后载荷含时间字段且预演可展示）；日志页能显示 ICBU 定时任务。

#### P1-5 旧授权缓存被误清 + 授权刷新后不重渲染当前页 → 已授权/离线用户可能停在「功能未开通」页

- **页面**：全局（启动/授权锁定）
- **控件/功能**：启动授权检查、锁定页、后台授权刷新
- **证据（file:line）**：
  - `app.py:10367` 异步启动 `_refresh_platform_license`；`:10371-10379` 刷新 entitlements 并重建导航，但**不重新渲染当前页**；`_page_idx` 只赋值不使用（`:1082,1091`）
  - `app.py:10380-10382`：`else` 实际挂在刷新 entitlements 的 `try` 上，**成功刷新也会执行** `license_cache_ok=False / license_cache_ts=0`（即使 `:10360-10362` 刚置 True）→ 下次启动旧授权（legacy）用户 entitlements 为空
  - `app.py:10179-10241` 无授权时弹「登录授权账号」；`licensing.py:96-99,138-141` legacy 授权依赖 `license_cache_ok`
- **现象与证据**：旧授权用户启动时 `load_entitlements` 读不到缓存 → 首页渲染锁定卡 + 弹登录框；后台校验成功后只重建导航、不重渲染，页面仍停在「Alibaba 国际站 · 功能未开通」，需手动点一次导航才恢复；离线时缓存被清 → 下次启动再次被锁。
- **期望行为**：授权刷新成功后重新渲染当前页；`license_cache_ok` 只在「确认无平台/无授权」时清除。
- **修复建议**：把 `else` 改为 `if not plats: clear cache`；在 `_check_license_inner`/`_refresh_platform_license` 末尾调用 `self._switch(self._page_idx or self._current)`。
- **验收标准**：离线+legacy 缓存用户第二次启动仍可进入已授权页面；在线校验成功后不点任何按钮，当前页自动从锁定卡变为正常内容。

#### P1-6 设置页被 `listing` 功能包锁定：部分功能包用户无法进入设置（无法粘贴 token/配置图床）

- **页面**：设置（11）、导航锁
- **控件/功能**：设置页访问权限
- **证据（file:line）**：`platforms.py:75`（设置 PageItem feature=`listing`）；`app.py:925-926` `_page_allowed`；实测（空/部分 entitlements）：只有 `ai_image` 时设置页渲染「功能未开通 / 当前授权未包含「listing」功能包」，导航上锁
- **现象与证据**：购买「AI 图片包」而非 listing 包的用户无法打开设置，也就无法在应用内粘贴平台 token（`app.py:8913,8942`）补充授权；启动登录弹窗需要邮箱密码，手动 token 用户被卡死。
- **期望行为**：设置页始终可访问（或拆「授权/连接」为独立 feature）。
- **修复建议**：`platforms.py` 设置 feature 改 always，或 `_page_allowed` 对 item.index==11 直接 True。
- **验收标准**：任意授权组合都能进设置；只有具体功能页被锁；token 保存后立即解锁。

#### P1-7 未实现平台（TikTok/Shopee）可被授权与切换，`implemented` 字段无人使用

- **页面**：左侧平台栏、锁定页
- **控件/功能**：平台切换、平台级锁定
- **证据（file:line）**：`platforms.py:51,116,130` 定义 `implemented`（TikTok/Shopee=False）；`grep implemented src/aeclone/ui/app.py licensing.py` 0 命中；`licensing.py:25-31` 仍为 tiktok/shopee 定义功能包；`app.py:967-1010` 平台栏按 entitlements 显示锁/可点
- **现象与证据**：TikTok/Shopee 在平台栏可见可点；未授权时 `_render_locked` 给「去授权网站开通」CTA（购买引导），但代码没有任何对应实现页面——用户购买后只会看到 Alibaba/速卖通页面或永久锁定。Amazon 也只有部分页面且沿用 Alibaba 文案。
- **期望行为**：未实现平台显示「即将支持/暂不可用」并禁止购买入口；`implemented` 参与平台栏与锁定页判定。
- **修复建议**：`_build_platform_rail`/`_render_locked`/`_switch_platform` 检查 `pf.implemented`；授权网站同步下架未实现平台套餐。
- **验收标准**：TikTok/Shopee 点击只提示「暂未支持」，无购买 CTA；`implemented=False` 有实际效果。

#### P1-8 区域调价模板：选择后不参与定价（配置只写不读）

- **页面**：价格（6）→「区域调价」→「店小秘区域调价模板」
- **控件/功能**：搜索/选用店小秘调价模板
- **证据（file:line）**：`app.py:6796-6829` `_search_templates` 仅写 `adjust_price_template_id/name`（`:6816-6817`）；卡片文案（`:6694-6697`）称「记录到配置里用于对账」；`services/pricing.py:174-177` 实际只读 `adjust_price_template_detail`；后者全仓只在 `config.py:229` 出现（默认 `"{}"`），无任何 UI/代码写入
- **现象与证据**：选用模板后 `pricing.compute_price` 不会读取该模板，区域倍率/区域价仍空；功能入口存在但无定价效果。
- **期望行为**：选用模板时拉取明细并写 `adjust_price_template_detail`，或删除入口/明确只读展示。
- **修复建议**：`list_detail` 后 `cfg.set("adjust_price_template_detail", json)`；若接口不支持，把按钮改为「仅记录」并禁用「选用」。
- **验收标准**：选模板后试算出现对应区域价；`adjust_price_template_detail` 非 `{}`。

#### P1-10 发布队列单条「发布」无二次确认（批量有），误点即提交发布

- **页面**：发布队列（8）→「查看待发布/发布失败」弹窗（SMT 平台）
- **控件/功能**：每行 trailing「发布」
- **证据（file:line）**：`app.py:3009-3011` `trailing=ft.TextButton("发布", ... self._publish_one(...))` 直连 `_publish_one`（`:2870-2888`，无 confirm）；同页批量发布/重试走 `_confirm`（`:7054-7072`）
- **现象与证据**：列表 100 个商品时，单条「发布」一点即调用 `batch_publish([pid])` 提交平台；批量反而有确认。发布后进入审核，短时间不可撤销。
- **期望行为**：单条发布也弹确认（商品 ID/标题/动作）。
- **修复建议**：复用 `self._confirm(...)`；或行内加 checkbox 走批量。
- **验收标准**：单条发布必出确认弹窗；取消不发请求。

#### P1-11 从商品列表选商品后，类目 fullCid/名称未同步，可能沿用上次选择的路径上下文

- **页面**：Alibaba 刊登（12）→「连接与商品」
- **控件/功能**：商品行「选择」
- **证据（file:line）**：`app.py:9234-9243` `set_product` 只写 `alibaba_test_product_id` 与 `alibaba_category_id`，**不更新** `alibaba_full_cid/alibaba_category_name`，也不更新顶部 `cat_now`（`:9085-9099` 的 `_pick_category` 才同步）；`services/alibaba_payload.py:213-251` `resolve_category` 在 cid 与 fullCid 末段不一致时用**旧 fullCid 前缀** + 新 cid 拼接；`validate_alibaba_save` 只校验末段（`:526-530`）
- **现象与证据**：先搜类目 A（写入 fullCid=A 路径），再从列表选择类目 B 商品（只改 categoryId）→ 载荷 `categoryId=B` 但 `fullCid=A前缀,B`，校验可通过；平台可能按错误路径处理或拒绝，且 UI 仍显示旧类目。
- **期望行为**：选商品后同步展示/写入该商品类目路径；列表无 fullCid 时清空并要求重选。
- **修复建议**：`set_product` 同时清空/回填 `alibaba_full_cid`、更新 `cat_now`；`prepare_save` 检测商品类目与 cfg 冲突时提示。
- **验收标准**：选商品后顶部类目立即变化；载荷 fullCid 末段与 categoryId 同源；无陈旧路径混拼。

#### P1-12 AI 图片「检查图片尺寸」在 Alibaba 平台必失败且提示不存在的「上架」页

- **页面**：AI 图片（5）
- **控件/功能**：「检查图片尺寸」
- **证据（file:line）**：`app.py:5763-5766` `run_audit` 依赖 `self.products`，为空时 toast「还没有加载商品，请先在**「上架」页**拉取产品」；`self.products` 仅由 SMT `_run_pipeline` 填充（`:3381`），Alibaba 商品不在其中；导航不存在「上架」页
- **现象与证据**：Alibaba 平台用户点该按钮永远得到「请先在「上架」页拉取产品」，应用里没有此页面，也没有从 12 号页商品喂给该检查的路径。
- **期望行为**：使用当前平台商品来源（ICBU）或明确「该检查仅速卖通可用」；文案指向真实页面。
- **修复建议**：`run_audit` 在 Alibaba 平台改读 ICBU 商品图或隐藏按钮；旧页名改为当前页名。
- **验收标准**：Alibaba 平台有可用结果或明确不可用提示；不出现不存在的页名。

#### P1-13 商品原 priceType=fob_price 会被默认配置覆盖为 ladder_price（SKU 有保护，FOB 没有）

- **页面**：Alibaba 刊登（12）→「价格与贸易」/预演/保存
- **控件/功能**：定价方式下拉、FOB 商品保存
- **证据（file:line）**：
  - `services/alibaba_payload.py:301-315`：`cfg_ptype` 默认 `"ladder_price"`；`prod_ptype=="sku_price"` 有专门保护，而 `prod_ptype=="fob_price"` 会走 `ptype = cfg_ptype or prod_ptype` → 被改成 `ladder_price`；`:317-327` 仅当 ptype 仍为 fob 时才写 sourcingTrade。实测（桩商品 `priceType=fob_price`）：`build_alibaba_save` 输出 `priceType=ladder_price`，FOB 区间/币种被弃用
  - 新 UI 下拉第一项是「保留商品原定价（推荐）」（`app.py:9616-9621`），但 `config.py:383` 默认值仍是 `"ladder_price"`，所以初始值实际选中 ladder（覆盖 FOB），不是「保留原定价」
- **现象与证据**：FOB 商品在预演/保存时被静默改成阶梯价（与代码注释「不能把商品原本定价模型静默改掉」自相矛盾）；用户必须手动把下拉切到「保留商品原定价」才正确，默认行为错误。
- **期望行为**：默认保留商品原 priceType（sku/fob/ladder 一视同仁），只有用户显式选择才切换。
- **修复建议**：`config.py` 的 `alibaba_price_type` 默认改 `""`（或 `_alibaba_cards` 下拉把空值作为实际值）；payload 对 fob 与 sku 一样加保护；切换模型时提示「会改写定价结构」。
- **验收标准**：商品原 `fob_price` → 默认预演/保存载荷仍为 `fob_price` 且 sourcingTrade 完整；只有用户选 ladder 才切换。

---

### P2（16 项）

| # | 页面/控件 | file:line | 现象/证据 | 期望/修复 | 验收 |
| --- | --- | --- | --- | --- | --- |
| P2-1 | 概览·余额显示 | `app.py:2035-2054`（`_apply_balance_state`）、`:2041` | `ready_balance` 从未创建，函数永远提前 return；概览看不到余额（仅设置「检测余额」toast） | 创建余额控件或删除死引用 | 概览显示「余额/未知」 |
| P2-2 | 概览·图片服务卡 | `app.py:1621` | 固定显示「约 15 秒/张」当状态值，无真实探测 | 改为静态说明或接入真实耗时 | 不把常量显示为状态 |
| P2-3 | 启动前检查弹窗 | `app.py:2419-2425` | 错误列表 `height=200` 无滚动，多条被截断；「去修改」固定 `_switch(1)`，错误属于页 2/5/7/8 时跳错 | 加滚动；按错误来源跳转 | 多错误可完整查看并到对应页 |
| P2-4 | 日志页·工具条 | `app.py:7908-7943`、`:7954-7958` | `清空`（直接清）与 `清空日志`（确认后清）两个入口；`导出日志` 写 `/tmp/aurora-logs.txt` 不打开，`导出筛选结果/CSV` 写配置目录并自动打开 | 合并为一组清空/导出入口并统一行为 | 只有一组入口，行为一致 |
| P2-5 | 日志监控侧栏 | `app.py:8139-8145`、`:8186-8191`、`:8228` | Esc/点击背景关闭弹窗时无 `on_dismiss` 兜底，`log_panel_open` 仍为 True → 再次点「日志监控」可能直接 return（需真机确认） | 弹窗加 `on_dismiss=self._close_log_panel` | Esc 关闭后可再次打开 |
| P2-6 | 价格·价格模板库 | `app.py:6764-6770`（`lib_del`） | 未选择模板时点「删除」仍 toast「已删除」；「应用」未选中提示「未找到模板」 | 空选中提示「先选择模板」；删除后刷新选中态 | 空选中不再假成功 |
| P2-7 | AI 图片·四合一 | `app.py:5865-5872` | 文案「四合一主图（主图1-4合成）」但只要求 ≥2 张，2~3 张生成留白拼图 | 文案改「2~4 张拼图」或强制 4 张 | 文案与行为一致 |
| P2-8 | Alibaba 刊登页·杂项 | `app.py:9031`、`:9549`、`:8885-8890` | 测试连接硬编码兜底商品 `220114598434958580`；AI 卡标题暴露开发语「（新链路）」；设置页「启动时打开」列表仍只有 0–11、缺 12 号页 | 去硬编码、去掉开发语、补 12 号页/按当前平台动态生成 | 页面无开发语；下拉含 Alibaba 刊登 |
| P2-9 | Alibaba 保存载荷 | `services/alibaba_payload.py:295-296,390-392`；`integrations/alibaba.py:238-246` | `_autofilled/_unresolved/_company` 内部字段随 `save` 写进 `alibabaSave.txt`（`save_product` 不过滤 `_` 前缀；真实 op=1 保存 1 次成功，未证伪影响） | 保存前剥离 `_` 前缀键；`_company` 走真实 schema 字段 | zip 内 JSON 只含协议字段 |
| P2-10 | 设置·图床 | `app.py:8631,8635`（`visible=False`）与 `:8742` | 文案「想换成自己的图床时改这里」，但地址/密钥输入框 `visible=False`，用户无法修改 | 显示输入框或改文案说明 | 文案与实际可操作项一致 |
| P2-11 | 设置·多开 | `app.py:9845-9858` | 实例列表只展示名称/端口/路径，已存在实例没有「启动」按钮；只能创建并启动新的 | 每个实例加「启动」或说明启动方式 | 已有实例可从 UI 启动 |
| P2-12 | 全应用·旧页名/文案 | `app.py:2100`（店铺设置）、`:2154`（高级设置）、`:4007/4013`（店铺设置）、`:4876/4881`（去店铺设置切换）、`:5766`（上架页）；`services/alibaba_publish.py:57`（设置 → Alibaba 刊登） | 导航已改名（店铺与模板/类目与属性/Alibaba 刊登），多处文案仍指旧页名 | 统一为当前页名；Cookie 错误文案改为「Alibaba 刊登」导航页 | 全应用无旧页名 |
| P2-13 | 导航·其它平台 | `platforms.py:87-141`（Amazon/TikTok/Shopee） | 标签仍「店铺与登录/草稿与待发布」，页头「店铺与模板/发布队列」；TikTok/Shopee 未实现但沿用 Alibaba 页面 | 与实现同步改名；未实现平台标注 | 各平台导航标签与页头一致 |
| P2-14 | 商品删除/下架 | 无 | 全应用无「删除商品/下架」入口，只有相册图片删除（`app.py:4710-4862`）；`docs/ui-redesign-validation.md` 提到「删除待实测」但 UI 无可测入口 | 确认产品范围；需要则补 ICBU/SMT 删除，不需要则更新文档 | 文档与 UI 能力一致 |
| P2-15 | 死代码/未使用 | `app.py:2656`（`_scope_dialog` 内未被调用的 `_load_children`，引用未定义 `client`）、`:9013`（`run_bg` 未用）、`:4686-4697`（`_close_dialog` 重复调用自身）、`:1082/1091`（`_page_idx` 只赋值） | 死函数/重复调用/无用属性；`_scope_dialog._load_children` 若被误调用会 NameError | 删除死代码；`_page_idx` 用于授权刷新后重渲染（见 P1-5） | 静态检查无未使用私有函数/属性 |
| P2-16 | Alibaba 刊登·「预演/保存」区重复入口 | `app.py:9332-9401`（手动 do_save）与 `:9467-9546`（AI ai_save）；按钮 `:9644-9656` 与 `:9548-9560` | 两条保存路径现在共用同一 pipeline、行为已一致，但 UI 仍并列「预演/保存」与「AI 预演/AI 生成并保存」两组四个按钮；用户需自行判断差别（是否用 AI） | 合并为一组：保存前勾选「用 AI 生成文案（付费）」；或重命名并加说明 | 12 号页只有一条保存入口/明确勾选，无重复按钮 |

---

## 三、Alibaba 12 号页（刊登工作台）专项审计

> 依据 Lead 要求与 publish-verifier 的发现重点核查。整体评价：**该页是当前唯一正确的 Alibaba ICBU 保存入口，主链路已可用；本轮又补齐了商品搜索/分页、定价方式与贸易参数、保存失败语义；剩余缺口集中在 ICBU op=5、选商品后的类目路径同步、FOB 默认覆盖、重复保存入口与载荷清理。**

### 3.1 已核实可用（本地/沙箱/桩 client）

| 项 | 证据 | 说明 |
| --- | --- | --- |
| 入口与导航 | `platforms.py:69`（PageItem(12)）、`app.py:9664-9674` | 导航「刊登」组可见；设置页已移除重复 Alibaba Tab（`app.py:8947-8954`） |
| 商品选择/搜索/分页 | `app.py:9219-9306` | 标题/ID 搜索（`:9229-9241`）、上一页/下一页（`:9295-9306`）、页码/总数、空状态提示（`:9265-9266`）；本轮已修复原 P1-4 |
| 类目搜索/子类目/必填 | `app.py:9046-9186` | `_load_children` 在本页实现（`:9100-9141`）；非末级可继续展开；`required_attributes`/`category_schema` 正常 |
| 定价/贸易控件 | `app.py:9611-9643` | 定价方式（保留原定价/阶梯/SKU/FOB）、销售方式、批发起批量、运费模板、FOB 币种/最低/最高、MOQ/单位/包装/重量/库存/HS、阶梯价 JSON；公司/贸易条款已明确标注「本地资料，未写入保存包」 |
| 预演（不保存） | `app.py:9180-9217`、`services/alibaba_pipeline.py:432-435` | 离线桩 client + `dry_run=True, use_ai=False` 实测 9 步通过、`saved=False` |
| 保存确认 | `app.py:9332-9425`、`:9521-9546` | 两次确认弹窗；op=2/3 警告色；默认 op=1；服务层 `AURORA_NO_SAVE/AURORA_TEST` + `core.guard` 双保险（`alibaba_publish.py:98-114`） |
| 失败语义 | `app.py:9364-9401`、`services/alibaba_pipeline.py:466-470` | 手动保存已统一走 pipeline，检查 code + `is_failure_msg`，失败红色显示分步原因（本轮已修复原 P0-2） |
| Cookie 沿用 | `app.py:8960-8980,9561-9565`、`alibaba_publish.py:45-74` | 本页 Cookie → 店铺与登录 Cookie → 本机 Chrome，错误文案明确 |
| 回调审计 | 最终快照 page 12：123 控件 / 40 回调，逐一执行 `{OK:38, DEFERRED:2, ERROR:0}` | 无无回调按钮、无禁用误导按钮 |

### 3.2 该页仍缺/不一致

- **P1-13**：商品原 `fob_price` 被默认配置改成 `ladder_price`（下拉默认值问题）。
- **P1-11**：选商品后 fullCid/名称/cat_now 不同步。
- **P1-1**：ICBU op=5 未暴露且载荷无时间字段。
- **P1-12**（入口在页 5）/ **P2-8/9/16**：旧页名提示、硬编码测试商品、开发语、内部字段进载荷、重复保存入口。
- 与 publish-verifier 结论一致：**对已存在 ICBU 商品的 op=1 保存已真实成功 1 次；op=2/3/5、新建商品、审核状态拉取均未验证**（见 `docs/ALIBABA_VERIFICATION.md`），不要把这些标成「已验证可用」。

---

## 四、已完整 / 本轮已修复（避免重复改）

### 4.1 本轮已修复（原 P0-2、P1-2、P1-3、P1-4 等）

| 原问题 | 修复证据 | 验证 |
| --- | --- | --- |
| 原 P0-2：手动保存 code=0 失败误报成功 | `app.py:9364-9401` 改走 `run_alibaba_product(dry_run=False, use_ai=False)`；`alibaba_pipeline.py:466-470` 检查 `is_failure_msg`；失败/成功都渲染分步结果 | 代码核对 + 桩 client 失败 msg 路径；真实失败回执待真实账号验证 |
| 原 P1-2：Alibaba 表单与载荷不对齐 | 新增 `app.py:9616-9643` 定价方式/销售方式/批发起批量/运费模板/FOB 币种/最低/最高；公司/贸易条款标注「本地资料，未写入保存包」；payload 读取键（`alibaba_payload.py:298-392`）均有对应控件 | 控件审计 + payload 键对照；FOB 默认值问题见 P1-13 |
| 原 P1-3：两条保存路径行为不一致 | 手动保存复用 AI pipeline（同一实现），失败语义/分步结果一致；残留重复入口降级 P2-16 | 代码核对 |
| 原 P1-4：Alibaba 商品列表只有 60 条 | `app.py:9229-9306` 标题/ID 搜索 + 分页 + 页码/总数 + 空态 | 控件审计（回调 0 异常） |
| 概览 Alibaba 主入口指向错平台 | `app.py:1516-1595`：启动/选择商品/搜索全部商品/工作流 03 均跳 12 号页，tiles 改 ICBU 草稿/在线；`7304/7335` 图片工作台队列阻断 | 控件审计 + 断点残留见 P0-1 |
| 速卖通定时发布无入口 | `app.py:5046-5053` 开关 + 时间输入；校验 `:2135-2147`；日志页队列 `:7975-8018` | 控件审计；到点真实发布未实测 |
| 原生文件选择器接线 | NATIVE 11 次（水印/参考图/详情本地图/本地视频/本地图片）；回调合成事件验证落配置（第六节） | 系统对话框真机行为待验证（第五节 B） |
| 原 `_load_children` NameError / 计数静默失败 / 导航标签错位 | 见 Lead 冻结说明与 `git diff`；本轮 12 页 + 12 号页 550 回调 0 异常 | 回调执行 |

### 4.2 已完整功能清单

1. **应用壳/导航**：13 项侧栏、搜索别名、切页、面包屑、主题切换、⌘R/L/F、平台栏与平台切换基础逻辑。
2. **授权锁定页**：未授权/缺功能包显示「功能未开通 + 需要什么 + 去授权网站开通」；导航锁图标/tooltip（`app.py:925-1032`）；问题只在 P1-5/P1-6 判定与刷新。
3. **概览**：按平台的状态数字（Alibaba=ICBU 草稿/在线；速卖通=SMT 5 桶）、准备状态 4 项、配置冲突检查、今日统计、成本卡、断点续跑卡、改前/改后对比空态、工作流按钮。
4. **店铺与模板**：Cookie 检测/重新检测/粘贴、读取并同步、店铺下拉（含「无商品」标注）、「切换到有商品的店铺」（`app.py:3609-3612,4223,4242-4269`）、模板选择/预览/读取参数/保存到本地/模板库（含空态、重命名、删除）、类目映射与历史学习。
5. **类目与属性**：类目来源、原箱/重量/尺寸/货值、化学品/带电、属性模板搜索应用、自定义属性清理、欧盟资质与缺失校验。
6. **AI 文案**：4 能力卡（开关+提示词折叠）、标题/详情/SKU/估重预设、详情文字/本地图/插入位置/合规过滤/复制 SKU/删除原文。
7. **内容审查**：文本/主图/详情图三卡、审查词、前后 N 张、拦截开关（services 有消费者）。
8. **AI 图片**：主图/描述图/SKU 图白底/去水印/翻译、精细任务增删/批量/参考图、尺寸校验、营销图、四合一、水印选择/预览/测试、视频来源/轮播参数、本地视频选择。
9. **价格**：4 种模式与参数联动、实时试算、阶梯编辑/示例、批发、区域价国家表、价格模板库保存/应用/删除。
10. **SKU·库存·套装**：编码前缀/分隔/示例、库存、属性映射说明、SKU 图替换表、套装**预览**（保存被明确阻断：`app.py:5377-5421`（`:5420` 提示）与 `services/preflight.py:200`、`publisher.py:2578-2582` 三处一致）。
11. **发布队列**：速卖通批量发布/重试/只重试/重跑失败/清空已编辑（均有确认或空态）；Alibaba 平台只读 ICBU 数量 + 查看草稿/在线 + 「去 Alibaba 刊登」（`app.py:6946-7085`）。
12. **图片工作台**：连接/Cookie、产品加载/勾选、取图、白底/去水印/加水印/翻译/编辑、四合一、生成视频（ffmpeg 检测+提示）、上传相册、清理旧图、任务队列（Alibaba 平台已阻断入队/处理）、导入本地图片。
13. **任务与日志**：运行状态/停止、上报、导出（三格式）、清空、筛选/级别/跟随、定时队列查看/取消/立即发布、已编辑记录清空。
14. **设置**：API（保存/测试/余额/充值）、授权（机器码/旧 ID/重新检测）、平台 token、图床（保存/测试上传/清理/恢复）、模型与价格、多开创建、版本更新、诊断（API/价格/店小秘/ffmpeg/磁盘）、ffmpeg 下载、推荐配置恢复、界面启动页/窗口。
15. **Alibaba 12 号页主链路**：见第三节 3.1。
16. **速卖通定时发布 UI**：开关 + 时间输入 + 启动校验 + 日志页队列（`app.py:2110-2147,5046-5053,7975-8018`）。

**隔离审计数据（最终快照）**：12 个主页面在沙箱中构建控件 **1503** 个（181/80/87/227/54/214/162/76/39/132/84/167），可交互回调 **550** 个，全授权下逐一执行 **0 异常**；官方 `tools/button_audit.py` 重跑 364 回调 `{OK:224, DEFERRED:123, NATIVE:9, EXTERNAL:8, ERROR:0}`；12 号页 123 控件 / 40 回调，逐回调执行 `{OK:38, DEFERRED:2, ERROR:0}`；无无回调按钮、无禁用误导按钮；全文件无 `TODO/FIXME/NotImplementedError`。

---

## 五、「真正未接通」vs「测试环境限制」（请勿混为一谈）

### A. 代码层面确定未接通（应修/应标注）

| 项 | 判定 | 证据 |
| --- | --- | --- |
| Alibaba 平台仍有启动入口走 SMT | **部分修复，残留 P0** | P0-1；概览已重定向，但 `btn_run`/⌘R/`_toggle_run`/`_run_pipeline` 无平台分支 |
| ICBU op=5 定时发布 | **未接通** | P1-1；op 下拉无 5，载荷无时间字段 |
| 商品原 FOB 定价被覆盖 | **缺陷** | P1-13；`alibaba_payload.py:301-315` + `config.py:383` |
| 区域调价模板 | **未接通** | P1-8；`adjust_price_template_detail` 从未写入 |
| 公司信息/贸易条款写入平台 | **未支持（已标注本地资料）** | `app.py:9595-9604,9636-9643` 文案已明确；companyDesc 模块未提交 |
| 套装 SKU 平台新增变种 | **明确阻断（可接受）** | `app.py:5377-5421`、`preflight.py:200`、`publisher.py:2578-2582` 三处一致；预览可用 |
| 商品删除/下架 | **未实现（待产品确认）** | 全仓无商品删除接口/入口 |
| TikTok/Shopee 平台 | **未实现但可购买** | `platforms.py:51,116,130`；`implemented` 无消费 |

### B. 测试环境限制（本次无法判缺陷，需真机/真实账号）

| 项 | 本次做到什么程度 | 需如何验证 |
| --- | --- | --- |
| 原生文件选择器（水印/参考图/详情本地图/本地视频/本地图片） | 回调已用合成事件验证：`_on_watermark_file`、`_on_files`、`_on_video_file`、`_dl_picker.on_result`、`_ref_picker.on_result` 均正确落配置；沙箱中 11 次 NATIVE 调用被 patch，未打开真实对话框 | 真机分别选 png/jpg/mp4，确认路径、权限、取消、重复选择 |
| 真实保存/发布/重试/定时到点 | publish-verifier：ICBU 只读 + dry-run；**用户授权下 op=1 真实保存 1 次成功**；op=2/3/5 无真实证据；速卖通定时到点未实测 | 用小号+可回滚商品逐项验证 op=2/3/5、失败重试、定时到点并记录平台回执 |
| Windows 行为 | 未验证（macOS 环境） | Windows 构建后跑同一套审计与真机交互 |
| 弹窗 Esc/背景关闭 | 沙箱不模拟 Flet 原生弹窗关闭事件 | 真机验证日志侧栏/模板库关闭后状态复位 |
| 平台切换后真实授权 | 沙箱用 `license_platforms` 覆盖，未连授权服务 | 用真实 token 验证平台/功能包组合下发 |
| AI/付费调用 | 未调用任何付费 AI（pipeline 用 `use_ai=False` 桩） | 用户授权余额下测一次标题/详情并按报告标注 |

---

## 六、附：审计方法与复现命令

### 6.1 方法

1. **静态梳理**：逐页阅读 12 个 builder + 12 号页 + `platforms.py`/`navigation.py`/`licensing.py`/`alibaba_*` 服务，按「按钮/开关→回调→服务层消费者」追踪，区分「有回调」≠「真接线」。
2. **隔离构建**：`tools/ui_sandbox.py` 的 `sandbox()/build_app()/walk()`，临时配置 + 全平台授权覆盖，逐页枚举控件（类别、文案、可见/禁用、on_click/on_change、空态）与回调源位置。
3. **回调执行**：对 12 页 + 12 号页每个可交互回调传合法事件对象执行，记录 `OK/DEFERRED/NATIVE/EXTERNAL/ERROR` 与 toast；最终快照 550 + 40 回调，**0 异常**。
4. **语义核对**：对 `config.DEFAULTS` 键做「UI 控件 ↔ services 消费」交叉检查；对 Alibaba 保存协议逐键核对 `build_alibaba_save/validate_alibaba_save`（对照 `docs/ALIBABA_RULES.md`）。
5. **平台/锁定矩阵**：5 平台逐页比较导航 label 与真实页头；用空/部分 entitlements 渲染锁定页，检查提示与可恢复路径。
6. **逻辑验证**：用桩 client 跑 `run_alibaba_product(dry_run=True/False, op=1/5, use_ai=False)`，确认预演不保存、护栏生效、op=5 缺时间字段；对文件选择器回调注入合成事件验证落盘；对 FOB/SKU 商品验证 priceType 保留行为。
7. **交叉验证**：与 `docs/ui-redesign-validation.md`「尚未完成」、`docs/ALIBABA_VERIFICATION.md`、`docs/publish-readiness.json`、`docs/ui-button-audit.json` 对照，避免重复或夸大。

### 6.2 复现命令（工作区根目录）

```bash
# 0) 版本与哈希（确认与报告快照一致）
git rev-parse --short HEAD
shasum -a 256 src/aeclone/ui/app.py src/aeclone/platforms.py \
  src/aeclone/services/alibaba_payload.py src/aeclone/services/alibaba_publish.py
wc -l src/aeclone/ui/app.py            # 期望 10688（最终冻结快照）

# 1) 官方控件回调审计（隔离临时配置，不会碰真实配置；不写 docs）
AURORA_CONFIG_DIR=$(mktemp -d) .venv/bin/python tools/button_audit.py
# 可选输出：--json /tmp/ui-button-audit.json

# 2) 开关/下拉落盘审计
AURORA_CONFIG_DIR=$(mktemp -d) .venv/bin/python tools/verify_ui_options.py

# 3) 页面/锁定/导航矩阵 + 回调执行（本报告用的临时脚本，未入库）
#    等价实现：sandbox() 内 build_app() → _switch(page) → walk(content.controls)
#    with sandbox(): app = build_app(); app.entitlements = <FULL>; app._switch(i)
#    注意：sandbox 的 license_platforms 覆盖只用于隔离审计，不代表服务端授权。

# 4) Alibaba 直连链路验证（独立验证者提供，只读 + dry-run）
.venv/bin/python tools/verify_publish_readiness.py     # 输出 docs/publish-readiness.json
.venv/bin/python tools/verify_alibaba_pipeline.py      # AI 流水线 dry-run

# 5) 回归 / 离线套件
.venv/bin/python tools/test_offline.py
.venv/bin/python tools/verify_regressions.py
```

> 注：本报告未改动 `src/`、`tests/`、`config`，未联网写操作，未读取真实用户配置/密钥；`docs/UI_INCOMPLETE_AUDIT.md` 是本轮唯一新增文件。临时审计脚本在 `/tmp`（`final_audit.py` 等），未入库。

---

## 七、最值得先修的 5 项（给 Lead 的排序建议）

1. **P0-1** 给 `_toggle_run`/`_start`/`_run_pipeline` 加平台分支：Alibaba 平台侧栏启动/⌘R 一律跳 12 号页，服务层加断言（消除最后的跨平台误写入口）。
2. **P1-13** `alibaba_price_type` 默认值改空/「保留原定价」，并像 sku 一样保护 fob，否则 FOB 商品默认被改成阶梯价。
3. **P1-1** ICBU op=5 要么补时间字段与入口，要么移除；同时把速卖通定时发布做一次到点实测。
4. **P1-5** 修 `license_cache_ok` 成功路径误清 + 授权刷新后 `_switch` 重渲染；**P1-6** 设置页不再被 listing 锁死（用户自救入口）。
5. **P1-11** 选商品后同步 fullCid/名称（或清空强制重选），避免按旧类目路径保存；配合 **P1-10** 单条发布加确认。

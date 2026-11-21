# AuroraList / Alibaba 国际站 · UI 未完善功能审计

> 审计者：ui-auditor（独立审计，未改产品代码）
> 审计时间：2026-11-22 01:16–01:20（Asia/Shanghai），代码已冻结。
> 审计对象：`git HEAD 9a50a72` + 工作区未提交改动（Lead 本轮 UI 改造）。
> 冻结快照（下文所有 `file:line` 均以该快照为准）：
> - `src/aeclone/ui/app.py` — 10560 行，sha256 `46b2887b4a597e91ee19307d8eb1a59f5f59466458523cd9539e323380ee55e6`（审计期间末尾追加了 Dock 图标/`run()` 等模块级函数，类方法行号未变）
> - `src/aeclone/platforms.py` — 148 行，sha256 `1dcaf10dae67760e0fc9f452dc3d4f20df45cdff3aaeeb8bb21fef338e1f434b`
> - `src/aeclone/services/alibaba_payload.py` 607 行 sha256 `d055f51d…`；`alibaba_publish.py` sha256 `03408866…`；`alibaba_pipeline.py` 481 行 sha256 `c7649090…`（新增）
> - 环境：macOS、`.venv` Python 3.14.7、Flet 0.25.2（无 pytest，脚本用 `.venv/bin/python` 运行）
> 审计范围：12 个页面构建器 + 新增 12 号「Alibaba 刊登」页、导航/平台/授权锁定、顶部栏、图片工作台、设置页各 Tab；只读 + 隔离沙箱，未联网写操作，未读取/打印真实配置与密钥。

---

## 一、总览

| 级别 | 数量 | 含义 |
| --- | --- | --- |
| **P0** | **2** | 用户会卡住/误解/数据风险，建议发布前修 |
| **P1** | **12** | 入口存在但没接线、常失效或平台错配，影响核心流程 |
| **P2** | **15** | 文案/一致性/空状态/死代码/体验问题 |
| 已完整 | 15 组 | 关键功能已可用，避免重复改（见第四节） |

**一句话结论**：12 个页面在隔离沙箱中全部可构建、566 个回调 0 异常、无 TODO/未实现占位；但 **Alibaba 平台的主任务入口（概览启动/选择商品/搜商品）仍指向店小秘 SMT（速卖通）管线，而数字已改成 ICBU 计数**——这是当前最需要修的产品级断点；其余问题集中在「Alibaba 刊登页表单与保存载荷不对齐（FOB/批发/运费模板/公司信息/贸易条款）」「定时发布完全无创建入口」「旧授权/部分功能包用户可能被锁在设置页外」以及一批 P2 文案与重复入口。

### 关键前提与边界

- 本轮 Lead 已修复（本次审计**不再计为缺陷**，仅记录）：导航标签与页头对齐、Alibaba 页「速卖通专属文案/标题预设」已改为中性表述、新增 12 号页并移除设置页重复 Alibaba Tab、`_load_children` NameError、`_refresh_store_counts` 失败可见、发布页 Alibaba 只读化、原生文件选择器接线、保存 op 选择 + 二次确认、Alibaba 类目写入 payload（`resolve_category`）、保存前授权/预演护栏、AI 刊登流水线。
- **真实保存/发布边界以独立验证者报告为准**：`docs/ALIBABA_VERIFICATION.md` 已证明「ICBU 直连链路对已存在商品 dry-run 通过；用户授权下 op=1 草稿真实保存 1 次成功」；**op=2/3/5、新建商品、定时到点、商品删除均无真实写入证据**。本报告不把「未实测」当「代码缺陷」，详见第五节。
- 沙箱审计环境通过 `tools/ui_sandbox.py` 提供全平台授权覆盖（`license_platforms` 覆盖仅存在于隔离环境，不代表服务端授权状态）；锁定页另用空 entitlements 单独验证。

---

## 二、分级清单

### P0（2 项）

#### P0-1 Alibaba 平台主任务入口仍走 SMT/速卖通管线，与 ICBU 计数/商品不互通（跨平台误操作风险）

- **页面**：概览（0）、发布队列（8）、图片工作台队列（9）、顶部/工作流入口
- **控件/功能**：「启动」「选择商品」「搜索全部商品」「03 发布商品」工作流按钮、图片工作台「产品入队/处理队列」
- **证据（file:line）**：
  - `src/aeclone/ui/app.py:1544` 概览写「默认只处理采集箱商品」；`:1547` 启动 → `_toggle_run`；`:1550` 选择商品 → `_scope_dialog`；`:1553` 搜索全部商品 → `_global_search_dialog`
  - `src/aeclone/ui/app.py:2592-2682` `_scope_dialog` 用 `DianxiaomiClient.product_page(1, 500, ..., "draft")`（SMT 列表，`:2678`）；`:3314` 主流程同样取 SMT 草稿；`:3393-3400` 交给 `Publisher`（SMT 保存）
  - `src/aeclone/integrations/dianxiaomi.py:37,45-46` 保存/详情端点均为 `/api/smtProduct/*.json`
  - `src/aeclone/services/publisher.py` 全文无 `alibaba/active_platform` 分支（`grep` 0 命中）；ICBU 直连只被 12 号页调用
  - `src/aeclone/ui/app.py:3693,3727-3760` 概览数字在 Alibaba 平台改走 `_refresh_alibaba_counts()`（ICBU 草稿/在线）
  - 发布页（`app.py:6911-7060`）已按平台改为「只读 ICBU 数量 + 去 Alibaba 刊登」，说明团队已确认 SMT 写操作不能用于 Alibaba；但概览入口尚未同步
- **现象与证据**：Alibaba 平台概览显示 ICBU 草稿 28 / 在线 2679（真实只读核验值），但点「启动/选择商品」会去 SMT 接口取商品；当前配置 `store_id=9466975` 在 SMT 列表为空（`docs/ALIBABA_VERIFICATION.md` §3.1），用户会看到「共 0 个产品」却不知道为什么。若 `store_id` 指向真实 SMT 店铺（如 9190736），同样的按钮会在 Alibaba 平台外观下编辑/发布 **SMT 速卖通商品**，存在跨平台误写风险（独立验证者亦标注）。图片工作台「产品入队/处理队列」同样走 `Publisher`（`app.py:3393/7606`）。
- **期望行为**：Alibaba 平台下「启动/选择商品/搜索/工作流入队」必须指向 ICBU 商品或直接跳转 12 号「Alibaba 刊登」；SMT 相关入口在 Alibaba 平台应隐藏或明确标注「仅速卖通」。
- **修复建议**：给 `_p_overview`、`_scope_dialog`、`_global_search_dialog`、`_run_pipeline`、图片工作台队列加 `active_platform.id == "alibaba"` 分支：读取 `AlibabaClient.list_products`，或直接把「启动」改为打开 12 号页并提示「Alibaba 商品请在 Alibaba 刊登页保存」；服务层入口加平台断言，避免以后误接。
- **验收标准**：在 Alibaba 平台，概览「启动/选择商品/搜索全部商品」不发出任何 `/api/smtProduct/*` 请求；选择商品弹窗只出现 ICBU 商品；「发布队列」只显示只读 ICBU 数字且带「去 Alibaba 刊登」；切到速卖通平台时原 SMT 流程保持不变。

#### P0-2 Alibaba 手动保存路径对 «code=0 但 msg 为失败» 误报成功

- **页面**：Alibaba 刊登（12）→「预演 / 保存」
- **控件/功能**：「保存到 Alibaba」（legacy 手动保存）
- **证据（file:line）**：
  - `src/aeclone/ui/app.py:9250` `do_save`；`:9283-9287` `save_to_alibaba(...)` 返回后**无条件** `status.value = f"已提交（op={op}）：{msg}"`、`status.color = SUCCESS_C`；未调用 `is_failure_msg`，也未区分 `data.code`
  - 对照同页新 AI 保存链路：`src/aeclone/services/alibaba_pipeline.py:464-467` 明确检查 `is_failure_msg(msg)` 并 `_fail`；`services/publisher.py:27-44` 定义了该判定
  - `services/alibaba_publish.py:98-114` `save_to_alibaba` 只做校验/返回原始结果，不判定 msg 语义
- **现象与证据**：店小秘存在「HTTP/外层 code=0，但 `data.msg` 是『操作失败，只有处在采集箱中的产品…』」的返回（`publisher.is_failure_msg` 注释即为此）；legacy 按钮会把这种情况显示成绿色「已提交」，用户以为刊登成功，实际未保存。新 AI 保存链路已正确处理，形成同页两套判定。
- **期望行为**：两条保存路径统一使用 `is_failure_msg` + `data.code` 判定；失败时红色显示失败原因，不得写「已提交」。
- **修复建议**：`do_save` 复用 `alibaba_pipeline.run_alibaba_product(dry_run=False, use_ai=False, op=op)`，或至少在 `save_to_alibaba` 内统一加 `is_failure_msg` 判定（更推荐唯一收口）。
- **验收标准**：用桩 client 返回 `{"code":0,"data":{"msg":"操作失败，只有处在采集箱中的产品可以发布"}}` 时，UI 显示红色失败、日志记为失败；返回「您的产品编辑成功！」时显示成功；两条路径行为一致。

---

### P1（12 项）

#### P1-1 定时发布整体未接通：没有创建入口，Alibaba op=5 也没有时间参数

- **页面**：任务与日志（10）「定时发布」Tab、发布队列（8）「运行与发布设置」、Alibaba 刊登（12）「保存动作」
- **控件/功能**：定时任务创建/编辑、`op=5 定时发布`
- **证据（file:line）**：
  - `src/aeclone/ui/app.py:2110-2117` 启动校验读取 `schedule_publish/schedule_time`，但全文件 **没有任何控件写入这两个键**（`grep "schedule_publish"` 仅命中 `_validate` 两处）
  - `src/aeclone/ui/app.py:7931-7974` 日志页只有「定时发布队列」列表 + 取消/立即发布，空态「暂无定时任务」（`:7974`）——用户无法从 UI 让队列里有任务
  - `src/aeclone/ui/app.py:9225-9231` 12 号页保存动作只有 op=1/3/2；`services/alibaba_publish.py:80` 定义了 `OP_SCHEDULE="5"`，`services/alibaba_pipeline.py:37,250` 接受 op=5，但 `build_alibaba_save` 生成的载荷里 **没有任何定时时间字段**（`grep schedule/publishTime` 在 `alibaba_payload.py` 0 命中）
- **现象与证据**：定时发布是三条互不衔接的半成品：SMT 调度器 + 校验已存在、UI 无入口；日志页展示队列但永远空；Alibaba 直连侧接受 op=5 却无法指定发布时间。任务要求重点核查的「定时」属于**真正未接通**。
- **期望行为**：在发布队列/12 号页提供「定时发布时间」控件；op=5 时把时间写入载荷（按 `docs/ALIBABA_RULES.md` 的平台字段）；日志页队列可看到真实任务。
- **修复建议**：先明确定时属于 SMT 链路还是 ICBU 链路：SMT 侧加 `schedule_publish` 开关 + 时间输入（复用 `parse_run_at`）；ICBU 侧在 `alibaba_payload` 增加 schedule 字段并允许 op=5，否则从 pipeline/常量中移除 op=5 以免误用。
- **验收标准**：用户可在 UI 创建一条定时任务 → 日志页显示倒计时/取消 → 到点触发（预演模式只记录不写入）；载荷中能看到时间字段；op 下拉不出现未实现的 5。

#### P1-2 Alibaba 表单字段与保存载荷不对齐：多个 UI 字段无人消费，多个载荷键无 UI 入口（FOB/批发/运费模板/公司信息不可配置）

- **页面**：Alibaba 刊登（12）→「预设资料」「价格与贸易」
- **控件/功能**：公司名称/网站/FAQ、贸易条款/港口/付款方式/交期、分组名称、店铺 ID；价格类型/FOB/批发/运费模板
- **证据（file:line）**：
  - UI 字段：`app.py:9498`（公司名称）、`:9499`（公司网站）、`:9501`（公司 FAQ）、`:9511-9513`（贸易条款/港口/付款方式/交期/HS）、`:9496`（分组名称）、`:9464`（店铺 ID）
  - 载荷侧 **完全不读**这些键：`services/alibaba_payload.py` 只读 `alibaba_group_id`、`alibaba_company_desc`（且仅写入内部 `_company` 痕迹，`:387-392`）、MOQ/unit/package/weight/hs/stock（`:354-385`）；`alibaba_trade_terms/port/payment_terms/lead_time/company_name/company_website/company_faq/group_name/shop_id` 在 `src/aeclone` 除 UI/config 外 0 命中
  - 载荷支持但 **UI 无任何控件** 的键：`alibaba_price_type`（ladder/sku/fob）、`alibaba_sale_type`、`alibaba_batch_number`、`alibaba_fob_currency/min_price/max_price`、`alibaba_freight_template_id`（`config.py:383-389`；在 `app.py` 中 `grep` 计数均为 0）；`build_alibaba_save` 读这些键（`alibaba_payload.py:305-325`（priceType/FOB））
- **现象与证据**：用户在「价格与贸易」填的贸易条款/港口/付款方式/交期不会进入保存载荷；「公司名称/网站/FAQ」也无任何保存效果（`companyDesc` 仅作为 `_company` 内部字段，见 P2-9）。反过来，FOB 报价所需的 min/max/币种、批发 saleType/batch_number、运费模板 ID 没有任何 UI，Alibaba 的「三种价格模型」实际只能走默认 ladder；`docs/ALIBABA_RULES.md` 明确 FOB 需 min/max/币种。这是表单与协议层「没接线」，不是测试环境限制。
- **期望行为**：UI 字段与 `alibabaSave` 字段一一对应；FOB 模式显示并校验 FOB 价格/币种；批发模式显示 batch_number；运费模板提供下拉（`AlibabaClient.freight_templates` 已存在）；公司信息落到 schema 的 `companyDesc/companyFaqDesc`（按类目 schema 字段）。
- **修复建议**：以 `build_alibaba_save` 的读取键为准补充 UI（priceType 三选一 + 对应字段、saleType/batch、freight_template_id 下拉）；删除或接线无消费的字段；用 `category_schema` 的 `fieldId` 决定显示哪些公司/模块字段。
- **验收标准**：在 Alibaba 页选择 FOB 后，保存载荷 `priceType=fob_price` 且 `sourcingTrade` 含 min/max/currency；选「批发」后 `saleType=batch`+`batch_number`；选运费模板后 `wholesaleTrade.shipping_line_template_id` 非空；不再出现「填了但 grep 不到消费者」的字段。

#### P1-3 Alibaba 刊登页存在两条保存路径（手动保存 vs AI 生成保存），职责与行为不一致

- **页面**：Alibaba 刊登（12）→「预演 / 保存」
- **控件/功能**：「预演（不保存）」「保存到 Alibaba」与「AI 预演（不保存）」「AI 生成并保存」
- **证据（file:line）**：`app.py:9250-9320`（legacy `do_save`，preview 走 `build_and_check` `:9122-9159`）；`app.py:9366-9450`（`ai_preview`/`ai_save`/`_confirm_ai_save`，走 `services/alibaba_pipeline.py`）；两套 confirm 弹窗（`:9303`、`:9429`），两套状态区（`required_box` 与 `ai_box`），legacy 不展示分步结果、也不做失败 msg 判定
- **现象与证据**：两排四个按钮都能"预演/保存"，但：legacy 用 `prepare_save` 只出 issues 列表、成功后只显示一句 msg；AI 路径有 9 步明细、`is_failure_msg`、许可证/预演双保险、付费警告。用户无法判断该用哪个，也容易在同一页得到不一致结果。
- **期望行为**：保留一条主保存路径（推荐 AI pipeline），另一条删除或明确标注「仅校验 payload、不保存」。
- **修复建议**：把 legacy「保存到 Alibaba」下线或改为「打开 payload 预览」；「预演（不保存）」统一走 pipeline `dry_run=True, use_ai=False`。
- **验收标准**：12 号页只剩一条真实保存入口；两处预演结果格式一致；代码中只有一处调用 `save_to_alibaba`/`c.save_product`。

#### P1-4 Alibaba 商品选择列表只拉第一页 60 条，无搜索/分页/空状态

- **页面**：Alibaba 刊登（12）→「连接与商品」
- **控件/功能**：「读取商品」列表
- **证据（file:line）**：`app.py:9192` `d = c.list_products(1, 60, st)`；`:9195-9224` 只渲染返回行；无页码/搜索控件；`:9166-9170` 商品状态只有草稿/在线
- **现象与证据**：真实账号在线 2679 个、草稿 28 个（`docs/ALIBABA_VERIFICATION.md` §3.1），用户只能看到第一页前 60 条，无法按标题/ID 找到目标商品；列表为空时只显示「商品列表（草稿）· 共 0 个」，没有空状态与下一步提示。
- **期望行为**：支持 `searchValue` 搜索与分页（`AlibabaClient.list_products` 已支持 page/search 参数），空列表给出「没有该状态商品，请先在店小秘/平台创建」。
- **修复建议**：复用 `_publish_list_dialog` 的搜索/分页模式；或在「当前商品 ID」输入框旁提供「按 ID 打开」。
- **验收标准**：可搜索/翻页；输入标题关键词能命中第 2 页以后的商品；空列表有明确空态。

#### P1-5 旧授权缓存被误清 + 授权刷新后不重渲染当前页 → 已授权/离线用户可能停在「功能未开通」页

- **页面**：全局（启动/授权锁定）
- **控件/功能**：启动授权检查、锁定页、后台授权刷新
- **证据（file:line）**：
  - `app.py:10239` 异步启动 `_refresh_platform_license`；`:10242-10251` 刷新 entitlements 并重建导航，但**不重新渲染当前页**；`_page_idx` 只赋值不使用（`:1082,1091`）
  - `app.py:10252-10254`：`else` 实际挂在刷新 entitlements 的 `try` 上，**成功刷新也会执行** `license_cache_ok=False / license_cache_ts=0`（即使上方 `:10231-10234` 刚置 True）→ 下次启动旧授权（legacy）用户 entitlements 为空
  - `app.py:10051-10113` 无授权时弹「登录授权账号」；`licensing.py:96-99,138-141` legacy 授权依赖 `license_cache_ok`
- **现象与证据**：旧授权用户启动时 `load_entitlements` 读不到缓存 → 首页渲染锁定卡 + 弹登录框；后台校验成功后只重建导航、不重渲染，页面仍停在「Alibaba 国际站 · 功能未开通」，需用户手动点一次导航才恢复；离线时缓存被清 → 下次启动再次被锁。属于「用户会卡住」的授权链路问题（非 UI 文案）。
- **期望行为**：授权刷新成功后重新渲染当前页；`license_cache_ok` 只在「确认无授权/无平台」时清除，不得在成功路径清空。
- **修复建议**：把 `else` 改为 `if not plats: clear cache`；在 `_check_license_inner` / `_refresh_platform_license` 末尾调用 `self._switch(self._page_idx or self._current)`。
- **验收标准**：离线+legacy 缓存用户第二次启动仍可进入已授权页面；在线校验成功后不点任何按钮，当前页自动从锁定卡变为正常内容。

#### P1-6 设置页被 `listing` 功能包锁定：部分功能包用户无法进入设置（无法粘贴 token/配置图床）

- **页面**：设置（11）、导航锁
- **控件/功能**：设置页访问权限
- **证据（file:line）**：`src/aeclone/platforms.py:75`（设置 PageItem feature=`listing`）；`app.py:925-926` `_page_allowed`；实测（空/部分 entitlements）：只有 `ai_image` 功能包时，设置页渲染「Alibaba 国际站 · 功能未开通 / 当前授权未包含「listing」功能包」，导航显示锁
- **现象与证据**：用户若购买的是「AI 图片包」而非 listing 包，打开设置会被锁，无法在应用内粘贴平台 token（`app.py:8869,8898` token 输入在设置里）来补充授权；而启动登录弹窗需要邮箱密码，手动 token 用户被卡死。
- **期望行为**：设置页应始终可访问（或拆出「授权/连接」作为独立 feature），未授权页面的「去开通」不能是唯一出口。
- **修复建议**：`platforms.py` 把设置 feature 改成始终允许（如新增 `"always"` 判定），或 `_page_allowed` 对 item.index==11 直接 True。
- **验收标准**：任意授权组合下都可进入设置；只有具体功能页被锁；token 保存后立即解锁。

#### P1-7 未实现平台（TikTok/Shopee）可被授权与切换，`implemented` 字段无人使用

- **页面**：左侧平台栏、锁定页
- **控件/功能**：平台切换、平台级锁定
- **证据（file:line）**：`platforms.py:51,116,130` 定义 `implemented`（TikTok/Shopee=False）；`grep implemented src/aeclone/ui/app.py licensing.py` 0 命中；`licensing.py:25-31` 仍为 tiktok/shopee 定义功能包；`app.py:970-987` 平台栏按 entitlements 显示锁/可点
- **现象与证据**：TikTok/Shopee 在平台栏可见可点；未授权时 `_render_locked` 给出「去授权网站开通」CTA（购买引导），但代码没有任何对应实现页面——用户购买后只会看到 Alibaba/速卖通页面或永久锁定。Amazon 也一样只有部分页面且沿用 Alibaba 文案。
- **期望行为**：未实现平台显示「即将支持/暂不可用」并禁止购买入口；`implemented` 参与平台栏与锁定页判定。
- **修复建议**：`_build_platform_rail`/`_render_locked`/`_switch_platform` 检查 `pf.implemented`；授权网站同步下架未实现平台套餐。
- **验收标准**：TikTok/Shopee 点击只提示「暂未支持」，无购买 CTA；`implemented=False` 在 UI 有实际效果。

#### P1-8 区域调价模板：选择后不参与定价（配置只写不读）

- **页面**：价格（6）→「区域调价」→「店小秘区域调价模板」
- **控件/功能**：搜索/选用店小秘调价模板
- **证据（file:line）**：`app.py:6761-6794` `_search_templates` 仅写 `adjust_price_template_id/name`（`:6781-6782`）；`app.py:6659-6662` 卡片文案称「模板 ID/名称会记录到配置里用于对账」；`services/pricing.py:174-177` 实际只读 `adjust_price_template_detail`；全仓 `adjust_price_template_detail` 只在 `config.py:229` 出现（默认 `"{}"`），无任何 UI/代码写入
- **现象与证据**：用户搜索并选用模板后，`pricing.compute_price` 不会读取该模板；区域倍率/区域价仍是空的。功能入口存在但没有任何定价效果（属于「真没接线」，且文案已变相承认只是"对账"）。
- **期望行为**：选用模板时拉取模板明细并写入 `adjust_price_template_detail`，或删除该入口/明确只读展示。
- **修复建议**：`list_detail` 后 `cfg.set("adjust_price_template_detail", json)`；若平台接口不支持，则把按钮改为「仅记录」并禁用「选用」。
- **验收标准**：选模板后计算试算出现对应区域价；`adjust_price_template_detail` 非 `{}`。

#### P1-10 发布队列单条「发布」无二次确认（批量有），误点即提交发布

- **页面**：发布队列（8）→「查看待发布/发布失败」弹窗（SMT 平台）
- **控件/功能**：每行 trailing「发布」
- **证据（file:line）**：`app.py:2982-2984` `trailing=ft.TextButton("发布", on_click=... self._publish_one(...))` 直连 `_publish_one`（`:2843-2861`，无 confirm）；同页批量发布/重试都走 `_confirm`（`:7019-7037`）
- **现象与证据**：列表里 100 个商品，单条「发布」点击立即调用 `batch_publish([pid])` 提交平台，没有确认弹窗；批量反而有二次确认。属于误操作数据风险（发布后进入审核，短时间不可撤销）。
- **期望行为**：单条发布也弹确认（显示商品 ID/标题/动作）。
- **修复建议**：复用 `self._confirm("发布该商品？", ...)`；或在列表行加 checkbox 走批量发布。
- **验收标准**：单条发布必出确认弹窗；取消不产生请求。

#### P1-11 从商品列表选商品后，类目 fullCid/名称未同步，可能沿用上次选择的路径上下文

- **页面**：Alibaba 刊登（12）→「连接与商品」
- **控件/功能**：商品行「选择」
- **证据（file:line）**：`app.py:9172-9181` `set_product` 只写 `alibaba_test_product_id` 与 `alibaba_category_id`，**不更新** `alibaba_full_cid`/`alibaba_category_name`，也不更新页面顶部 `cat_now`；`app.py:8850-8872` `_pick_category` 才同步这些值；`services/alibaba_payload.py:213-251` `resolve_category` 在 cid 与 fullCid 末段不一致时用 **旧 fullCid 前缀** + 新 cid 拼接
- **现象与证据**：先手动搜类目 A（写入 fullCid=A路径），再从列表选择类目 B 的商品（只改 categoryId）→ 保存载荷 `categoryId=B` 但 `fullCid=A前缀,B`；`validate_alibaba_save` 只校验末段一致（`alibaba_payload.py:526-530`），可通过；平台可能按错误类目路径处理或拒绝。同时 `cat_now` 显示旧类目，用户无感知。
- **期望行为**：选商品后同步展示/写入该商品的类目路径；若列表无 fullCid，则清空 `alibaba_full_cid` 并在保存前要求重新选类目。
- **修复建议**：`set_product` 同时清空/回填 `alibaba_full_cid`、更新 `cat_now`；或在 `prepare_save` 里检测商品类目与 cfg 类目冲突并提示。
- **验收标准**：选商品后顶部类目立即变化；保存载荷 fullCid 末段与 categoryId 同源；不出现陈旧路径混拼。

#### P1-12 AI 图片「检查图片尺寸」在 Alibaba 平台必失败且提示不存在的「上架」页

- **页面**：AI 图片（5）
- **控件/功能**：「检查图片尺寸」
- **证据（file:line）**：`app.py:5728-5731` `run_audit` 依赖 `self.products`，为空时 toast「还没有加载商品，请先在**「上架」页**拉取产品」；而 `self.products` 仅由 SMT `_run_pipeline` 填充（`:3354`），Alibaba 平台的商品不在其中；导航中不存在「上架」页（只有 发布队列/Alibaba 刊登）
- **现象与证据**：Alibaba 平台用户点该按钮永远得到「请先在「上架」页拉取产品」，但应用里没有这个页面；也没有从 12 号页选中的商品喂给该检查的路径。
- **期望行为**：按钮应使用当前平台商品来源（ICBU）或明确「该检查仅速卖通可用」；文案指向真实页面。
- **修复建议**：`run_audit` 在 Alibaba 平台改读 ICBU 商品图或隐藏按钮；把「上架页」改为实际页名。
- **验收标准**：Alibaba 平台点击有可用结果或明确不可用提示；文案不再出现不存在的页名。

---

#### P1-13 商品原 priceType=fob_price 会被默认配置覆盖为 ladder_price（SKU 有保护，FOB 没有）

- **页面**：Alibaba 刊登（12）→ 预演/保存（影响所有走 ICBU 保存的商品）
- **控件/功能**：价格模型选择、FOB 商品保存
- **证据（file:line）**：`services/alibaba_payload.py:302-315`：`cfg_ptype` 默认 `"ladder_price"`；`prod_ptype == "sku_price"` 有专门保护（不覆盖），但 `prod_ptype == "fob_price"` 会走 `ptype = cfg_ptype or prod_ptype` → 被改成 `ladder_price`；`:318-325` 仅当 ptype 仍为 fob 时才写 sourcingTrade。实测（桩商品 `priceType=fob_price`）：`build_alibaba_save` 输出 `priceType=ladder_price`、保留无效 sourcingTrade、沿用阶梯价，FOB 区间/币种被弃用。
- **现象与证据**：FOB 报价商品在预演/保存时被静默改成阶梯价模型（与注释声明的「不能把商品原本定价模型静默改掉」自相矛盾）；由于 UI 又没有 priceType 选择（P1-2），用户无法纠正。
- **期望行为**：未显式选择价格模型时保留商品原 `priceType`（sku/fob/ladder 一视同仁）；只有用户显式配置或覆盖时才切换。
- **修复建议**：把 `cfg_ptype` 默认改为空（或仅在 `alibaba_price_type` 显式设置时覆盖），并像 sku 一样保护 fob；给 UI 增加 priceType 选择并明确提示「切换模型会改写定价结构」。
- **验收标准**：商品原 `fob_price` → 预演/保存载荷仍为 `fob_price` 且 sourcingTrade 完整；只有用户在 UI 选择 ladder 后才允许切换。

### P2（15 项）

| # | 页面/控件 | file:line | 现象/证据 | 期望/修复 | 验收 |
| --- | --- | --- | --- | --- | --- |
| P2-1 | 概览·余额显示 | `app.py:2008-2027`（`_apply_balance_state`）、`:2014` `getattr(self,"ready_balance",None)` | `ready_balance` 从未创建，函数永远提前 return；`_probe_balance`/`_show_balance_low` 仍有逻辑，但概览看不到余额 | 创建余额控件或删除死引用；余额只在设置「检测余额」toast 出现 | 概览准备状态能显示「余额/未知」 |
| P2-2 | 概览·图片服务卡 | `app.py:1594` | 固定显示「约 15 秒/张」当成状态值，无真实探测 | 改为静态说明「单张约 15 秒」或接入真实耗时 | 不再把常量显示为状态 |
| P2-3 | 启动前检查弹窗 | `app.py:2391-2397` | 错误列表 `height=200` 无滚动，多条被截断；「去修改」固定 `_switch(1)`，即便错误属于页 2/5/7/8 | 按错误来源跳转/加滚动；或提供逐条跳转 | 多错误可完整滚动查看；点「去修改」到对应页 |
| P2-4 | 日志页·工具条 | `app.py:7863-7899`、`:7910-7914` | `清空` 与 `清空日志` 两个入口行为不同（一个直接清、一个确认后清）；`导出日志` 写 `/tmp/aurora-logs.txt` 不打开，`导出筛选结果/导出 CSV` 写配置目录并自动打开 | 合并为「清空日志（确认）」「导出（选格式+打开）」 | 日志页只有一组清空/导出入口，行为一致 |
| P2-5 | 日志监控侧栏 | `app.py:8095-8101`、`:8142-8147`、`:8184` | Esc/点击背景关闭弹窗时没有 `on_dismiss` 兜底，`log_panel_open` 仍为 True → 再次点「日志监控」可能直接 return（需真机确认） | 弹窗加 `on_dismiss=self._close_log_panel` 或关闭标志复位 | Esc 关闭后可再次打开侧栏 |
| P2-6 | 价格·价格模板库 | `app.py:6729-6735`（`lib_del`） | 未选择模板时点「删除」仍 toast「已删除」；「应用」未选中提示「未找到模板」 | 空选中时提示「先选择模板」；删除后刷新选中态 | 空选中点击有明确提示，不再假成功 |
| P2-7 | AI 图片·四合一 | `app.py:5830-5837` | 文案「四合一主图（主图1-4合成）」但只要求 ≥2 张，2~3 张时生成带留白的拼图 | 文案改为「2~4 张拼图」或强制 4 张 | 文案与行为一致 |
| P2-8 | Alibaba 刊登页·杂项 | `app.py:9192-9224`、`:8973`、`:9448`、`:8841-8846` | 商品列表为空无空态；测试连接用硬编码兜底商品 `220114598434958580`；AI 卡标题「AI 文案 → 保存（新链路）」暴露开发语；设置页「启动时打开」列表缺 12 号页 | 空态提示、去硬编码、去掉「新链路」、补 12 号页/动态取当前平台页面 | 页面无开发语；空列表有引导；下拉含 Alibaba 刊登 |
| P2-9 | Alibaba 保存载荷 | `services/alibaba_payload.py:295-296,390-392`；`integrations/alibaba.py:238-246` | `save` 里的 `_autofilled/_unresolved/_company` 内部字段会原样写入 `alibabaSave.txt`（`save_product` 不过滤 `_` 前缀；真实 op=1 保存 1 次成功，未证伪影响） | 保存前剥离 `_` 前缀键；`_company` 走真实 schema 字段 | zip 内 JSON 只含协议字段 |
| P2-10 | 设置·图床 | `app.py:8587,8591`（`visible=False`）与 `:8698` | 文案「想换成自己的图床时改这里（默认 image.moneyfly.club）」，但图床地址/密钥输入框 `visible=False`，用户无法修改 | 显示输入框或在文案中说明「自定义图床请联系/通过配置文件」 | 文案与实际可操作项一致 |
| P2-11 | 设置·多开 | `app.py:9414-9427` | 实例列表只展示名称/端口/路径，已存在实例没有「启动」按钮；只能创建并启动一个新的 | 每个实例加「启动」；或明确说明通过新建启动 | 已有实例可从 UI 启动 |
| P2-12 | 全应用·旧页名/文案 | `app.py:2073`（店铺设置）、`:2127`（高级设置）、`:3980/3986`（店铺设置）、`:4849/4854`（去店铺设置切换）、`:5731`（上架页） | 导航已改名「店铺与模板 / 类目与属性 / Alibaba 刊登」，多处文案仍指旧页名；`services/alibaba_publish.py:57` 仍写「设置 → Alibaba 刊登」（入口已移到导航） | 统一为当前页名；「去店铺设置切换」改为「去店铺与模板」 | 全应用 grep 无旧页名 |
| P2-13 | 导航·其它平台 | `platforms.py:87-141`（Amazon/TikTok/Shopee） | 标签仍为「店铺与登录/草稿与待发布」，实际页头「店铺与模板/发布队列」；TikTok/Shopee 未实现但沿用 Alibaba 页面 | 与实现同步改名；未实现平台标注 | 各平台导航标签与页头一致 |
| P2-14 | 商品删除/下架 | 无 | 全应用无「删除商品/下架」入口，只有相册图片删除（`app.py:4683-4835`）；`docs/ui-redesign-validation.md` 提到「删除待实测」但 UI 无可测入口 | 确认产品范围：若需要则补 ICBU/SMT 删除；若不需要请更新文档 | 文档与 UI 能力一致 |
| P2-15 | 死代码/未使用 | `app.py:2629-2665`（`_scope_dialog` 内未被调用的 `_load_children`，引用未定义 `client`）、`:8955`（`run_bg` 定义未用）、`:4659-4670`（`_close_dialog` 重复调用自身）、`:1082/1091`（`_page_idx` 只赋值） | 死函数/重复调用/无用属性；`_scope_dialog._load_children` 若被误调用会 NameError | 删除死代码；`_page_idx` 用于授权刷新后重渲染（见 P1-5） | 静态检查无未使用私有函数/属性 |

---

## 三、Alibaba 12 号页（刊登工作台）专项审计

> 依据 Lead 要求与 publish-verifier 的发现重点核查。整体评价：**该页是当前唯一正确的 Alibaba ICBU 保存入口，主链路已可用；缺口集中在表单字段与载荷对齐、商品列表可用性、双保存路径与 op=5。**

### 3.1 已核实可用（本地/沙箱）

| 项 | 证据 | 说明 |
| --- | --- | --- |
| 入口与导航 | `platforms.py:69`（PageItem(12)）、`app.py:9536-9546` | 导航「刊登」组可见；设置页已移除重复 Tab（`app.py:8903-8908`） |
| 商品选择 | `app.py:9161-9224` | 读 ICBU 草稿/在线列表，选中写 `alibaba_test_product_id`（但见 P1-11 类目同步） |
| 类目搜索/子类目/必填 | `app.py:8988-9041` | `_load_children` 已在本页实现（`:9042-9084`）；非末级可继续展开；`required_attributes`/`category_schema` 读取并展示 |
| 预演（不保存） | `app.py:9122-9159`、`services/alibaba_pipeline.py:432-435` | 离线桩 client + `dry_run=True, use_ai=False` 实测 9 步通过、`saved=False` |
| 保存确认 | `app.py:9250-9320`、`:9420-9450` | 两次确认弹窗；op=2/3 用警告色；默认 op=1；服务层还有 `AURORA_NO_SAVE/AURORA_TEST` + `core.guard` 双保险（`alibaba_publish.py:98-114`） |
| 失败语义 | `services/alibaba_pipeline.py:464-470` | AI 保存链路检查 code + `is_failure_msg`（legacy 手动路径缺，见 P0-2） |
| Cookie 沿用 | `app.py:8916-8936,9462-9465`、`alibaba_publish.py:45-74` | 本页 Cookie → 店铺与登录 Cookie → 本机 Chrome，错误文案明确 |

### 3.2 该页仍缺/不一致

- **P1-2**：预设资料/价格贸易多个字段无消费者；FOB/批发/运费模板无 UI。
- **P1-3**：手动保存与 AI 保存两条路径并存。
- **P1-11**：选商品后 fullCid/名称不同步。
- **P1-1**：op=5 未暴露且载荷无时间字段。
- **P1-4**：商品列表只有第一页 60 条、无搜索/分页/空态。
- **P2-8/9**：开发语「新链路」、硬编码测试商品、内部字段进载荷。
- 与 publish-verifier 结论一致：**对已存在 ICBU 商品的 op=1 保存已真实成功 1 次；op=2/3/5、新建商品、审核状态拉取均未验证**（见 `docs/ALIBABA_VERIFICATION.md`），不要在 UI 上把它们标成「已验证可用」。

---

## 四、已完整功能清单（避免 Lead 重复改）

以下经隔离沙箱构建/回调/逻辑核验，**不需要再修**（除非要配合 P0/P1 改动）：

1. **应用壳/导航**：13 项侧栏可完整显示、搜索别名、页面切换、面包屑、主题切换、⌘R/L/F 快捷键、平台栏与平台切换基础逻辑。
2. **授权锁定页**：未授权/缺功能包时显示「功能未开通 + 需要什么 + 去授权网站开通」；导航锁图标与 tooltip（`app.py:925-1032`）。问题只在 P1-5/P1-6 的判定与刷新，不在展示。
3. **概览**：状态数字（ICBU/SMT 按平台）、准备状态 4 项、配置冲突检查、今日统计、成本卡、断点续跑卡、改前/改后对比空态、工作流按钮。
4. **店铺与模板**：Cookie 检测/重新检测/粘贴、读取并同步、店铺下拉（含「无商品」标注）、「切换到有商品的店铺」（`app.py:3582-3585,4190-4242`）、模板选择/预览/读取参数/保存到本地/模板库（含空态、重命名、删除）、类目映射与「从历史商品学习」。
5. **类目与属性**：类目来源切换、原箱/重量/尺寸/货值、化学品/带电、属性模板搜索与应用、自定义属性清理（brand/type/model/condition）、欧盟资质与缺失校验（`_eu_missing`）。
6. **AI 文案**：4 个能力卡（开关+提示词折叠）、标题/详情/SKU/估重预设套用、详情文字/本地图/插入位置/合规过滤/复制 SKU/删除原文。
7. **内容审查**：文本/主图/详情图三卡、审查词、前后 N 张、拦截开关（全部有消费者，见 `review_block_on_fail` 等在 services 的使用）。
8. **AI 图片**：主图/描述图/SKU 图白底/去水印/翻译、精细任务增删/批量/参考图选择、尺寸校验、营销图生成、四合一、水印选择/预览/测试、视频来源/轮播参数、本地视频选择（新接线）。
9. **价格**：4 种定价模式与参数联动、实时试算、阶梯编辑/示例、批发、区域价国家表、价格模板库保存/应用/删除。
10. **SKU·库存·套装**：编码前缀/分隔/示例、库存、属性映射说明、SKU 图替换表、套装**预览**（保存被明确阻断：`app.py:5342-5386`（`:5385` 提示）与 `services/preflight.py:200`、`publisher.py:2578-2582` 三处一致）。
11. **发布队列**：SMT 平台批量发布/重试/只重试发布/重跑失败/清空已编辑（均有二次确认或空态）；Alibaba 平台已改为只读 ICBU 数量 + 「去 Alibaba 刊登」（`app.py:6911-7060`）。
12. **图片工作台**：连接/Cookie、产品加载与勾选、取图、白底/去水印/加水印/翻译/编辑、四合一、生成视频（ffmpeg 检测+提示）、上传相册、清理旧图、任务队列（入队/处理/清理/重试失败）、导入本地图片（新接线）。
13. **任务与日志**：运行状态/停止、上报日志、导出（三格式）、清空、筛选/级别/跟随、定时队列查看/取消/立即发布、已编辑记录清空。
14. **设置**：API（保存/测试/余额/充值）、授权（机器码/旧 ID/重新检测）、平台 token、图床（保存/测试上传/清理/恢复）、模型与价格、多开创建、版本更新、诊断（API/价格/店小秘/ffmpeg/磁盘）、ffmpeg 下载、推荐配置恢复、界面启动页/窗口。
15. **Alibaba 12 号页主链路**：见第三节 3.1。

**隔离审计数据**：12 个主页面在沙箱中构建控件 1521 个（199/80/87/227/54/214/162/76/39/132/84/167），可交互回调 552 个，全授权下逐一执行 **0 异常**；官方 `tools/button_audit.py` 重跑 364 回调 `{OK:224, DEFERRED:123, NATIVE:9, EXTERNAL:8, ERROR:0}`；12 号页 102 控件 / 31 回调，逐回调执行 `{OK:29, DEFERRED:2, ERROR:0}`；无无回调按钮、无禁用误导按钮；全文件无 `TODO/FIXME/NotImplementedError`。

---

## 五、「真正未接通」vs「测试环境限制」（请勿混为一谈）

### A. 代码层面确定未接通（应修/应标注）

| 项 | 判定 | 证据 |
| --- | --- | --- |
| Alibaba 平台主流程仍走 SMT | **未接通** | P0-1；`services/publisher.py` 无平台分支，`dianxiaomi.py:37,45` SMT 端点 |
| Alibaba 手动保存失败语义 | **缺陷** | P0-2；`app.py:9286` vs `alibaba_pipeline.py:464-467` |
| 定时发布创建入口 / op=5 时间字段 | **未接通** | P1-1；无控件、载荷无字段 |
| Alibaba FOB/批发/运费模板/公司信息 | **未接通** | P1-2；UI 无控件，载荷键无消费者或反之 |
| 区域调价模板 | **未接通** | P1-8；`adjust_price_template_detail` 从未写入 |
| 套装 SKU 平台新增变种 | **明确阻断（可接受）** | `app.py:5342-5386`（`_combo_card`，`:5385` 为未接通提示）、`preflight.py:200`、`publisher.py:2578-2582` 三处一致提示；预览可用 |
| 商品删除/下架 | **未实现（待产品确认）** | 全仓无商品删除接口/入口 |
| TikTok/Shopee 平台 | **未实现但可购买** | `platforms.py:51,116,130`；`implemented` 无消费 |

### B. 测试环境限制（本次无法判缺陷，需真机/真实账号）

| 项 | 本次做到什么程度 | 需如何验证 |
| --- | --- | --- |
| 原生文件选择器（水印/参考图/详情本地图/本地视频/本地图片） | 回调已用合成事件验证：`_on_watermark_file`、`_on_files`、`_on_video_file`、`_dl_picker.on_result`、`_ref_picker.on_result` 均正确落配置；沙箱中 11 次 NATIVE 调用被 patch，未打开真实系统对话框 | 真机分别选择 png/jpg/mp4，确认路径、权限、取消、重复选择 |
| 真实保存/发布/重试/定时到点 | publish-verifier 已完成：ICBU 只读 + dry-run；**用户授权下 op=1 真实保存 1 次成功**；op=2/3/5 无真实证据 | 用小号+可回滚商品逐项验证 op=2/3/5、失败重试、定时到点；记录平台回执 |
| Windows 行为 | 未验证（macOS 环境） | Windows 构建后跑同一套审计脚本与真机交互 |
| 弹窗 Esc/背景关闭 | 沙箱不模拟 Flet 原生弹窗关闭事件 | 真机验证日志侧栏/模板库等关闭后状态复位 |
| 平台切换后真实授权 | 沙箱用 `license_platforms` 覆盖，未连授权服务 | 用真实 token 验证平台/功能包组合下发 |
| AI/付费调用 | 未调用任何付费 AI（pipeline 用 `use_ai=False` 桩） | 用户授权余额下测一次标题/详情并按报告标注 |

---

## 六、附：审计方法与复现命令

### 6.1 方法

1. **静态梳理**：逐页阅读 12 个 builder + `platforms.py`/`navigation.py`/`licensing.py`/`alibaba_*` 服务，按「按钮/开关→回调→服务层消费者」追踪，区分「有回调」≠「真接线」。
2. **隔离构建**：`tools/ui_sandbox.py` 的 `sandbox()`/`build_app()`/`walk()`，临时 `AURORA_CONFIG_DIR`（不碰真实配置），全平台授权覆盖后逐页构建，枚举控件（类别、文案、可见/禁用、on_click/on_change、空态）与回调源位置。
3. **回调执行**：对 12 页 + 12 号页的每个可交互回调传入合法事件对象执行，记录 `OK/DEFERRED(后台线程)/NATIVE(文件选择器)/EXTERNAL(外链/进程)/ERROR`、toast 文案；全授权下 0 异常。
4. **语义核对**：对 `config.DEFAULTS` 中的键做「UI 是否有控件 ↔ services 是否有消费者」交叉检查；对 Alibaba 保存协议逐键核对 `build_alibaba_save`/`validate_alibaba_save`（对照 `docs/ALIBABA_RULES.md`）。
5. **平台/锁定矩阵**：对 5 个平台逐页比较导航 label 与真实页头；用空/部分 entitlements 渲染锁定页，检查提示与可恢复路径。
6. **逻辑验证**：对 `run_alibaba_product` 用桩 client 跑 `dry_run=True/False`、`op=1/5`、`use_ai=False`，确认预演不保存、护栏生效、op=5 缺时间字段；对文件选择器回调注入合成事件验证落盘。
7. **交叉验证**：与 `docs/ui-redesign-validation.md`「尚未完成」、`docs/ALIBABA_VERIFICATION.md`、`docs/publish-readiness.json`、`docs/ui-button-audit.json` 对照，避免重复或夸大。

### 6.2 复现命令（工作区根目录）

```bash
# 0) 版本与哈希（确认与报告快照一致）
git rev-parse --short HEAD
shasum -a 256 src/aeclone/ui/app.py src/aeclone/platforms.py \
  src/aeclone/services/alibaba_payload.py src/aeclone/services/alibaba_publish.py
wc -l src/aeclone/ui/app.py            # 期望 10560（最终冻结快照）

# 1) 官方控件回调审计（隔离临时配置，不会碰真实配置；不写 docs）
AURORA_CONFIG_DIR=$(mktemp -d) .venv/bin/python tools/button_audit.py
# 可选输出：--json /tmp/ui-button-audit.json

# 2) 开关/下拉落盘审计
AURORA_CONFIG_DIR=$(mktemp -d) .venv/bin/python tools/verify_ui_options.py

# 3) 页面/锁定/导航矩阵 + 回调执行（本报告用的临时脚本，未入库）
#    等价实现：sandbox() 内 build_app() → _switch(page) → walk(content.controls)
cd tools && ../../../.venv/bin/python /tmp/final_audit.py   # 若临时脚本仍在
#    否则用 ui_sandbox 自建：
#    with sandbox(): app = build_app(); app.entitlements = <FULL>; app._switch(i)
#    注意：sandbox 的 license_platforms 覆盖只用于隔离审计，不代表服务端授权。

# 4) Alibaba 直连链路验证（独立验证者提供，只读 + dry-run）
.venv/bin/python tools/verify_publish_readiness.py     # 输出 docs/publish-readiness.json
.venv/bin/python tools/verify_alibaba_pipeline.py      # AI 流水线 dry-run

# 5) 回归 / 离线套件
.venv/bin/python tools/test_offline.py
.venv/bin/python tools/verify_regressions.py
```

> 注：本报告未改动 `src/`、`tests/`、`config`，未联网写操作，未读取真实用户配置/密钥；`docs/UI_INCOMPLETE_AUDIT.md` 是本轮唯一新增文件。临时审计脚本放在 `/tmp`（`final_audit.py`、`ui_inventory.py`、`callback_sweep.py`），未入库。

---

## 七、最值得先修的 5 项（给 Lead 的排序建议）

1. **P0-1** Alibaba 平台概览/选择商品/搜索/队列接 ICBU（或直接跳 12 号页），消除「数字是 ICBU、动作是 SMT」与跨平台误写风险。
2. **P0-2 + P1-3** 统一 Alibaba 保存收口：legacy 手动保存去重/复用 pipeline，失败 msg 判定统一。
3. **P1-1** 定时发布：加创建入口与 op=5 时间字段（或明确下线，别留空队列 + 校验）。
4. **P1-2** Alibaba 表单↔载荷对齐：补 FOB/批发/运费模板 UI，删/接公司信息与贸易条款字段。
5. **P1-5 + P1-6** 授权链路：修 `license_cache_ok` 成功路径误清 + 刷新后重渲染；设置页不再被 listing 锁死（否则用户无法自救）。

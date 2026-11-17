# 虚拟机点击走查 · 问题记录（2026-11-17）

> 第 2 轮修复已完成：10/10 全部修复，逐项验证见文末「修复记录」。
> 复跑验证：`.venv/bin/python tools/verify_issues.py`（73 项全通过）+ `tools/button_audit.py`（0 新增错误）。

## 已发现

| # | 页面 | 问题 | 影响 | 处理建议 | 状态 |
|---|---|---|---|---|---|
| 1 | 店铺设置 | Cookie 显示 `auto · 0 个（读取失败，可粘贴）` | 新手会卡在第一步；实际店铺数据能读到（因为注入过 cookies） | 优先读配置里的 manual_cookies；其次用 CDP 从已开浏览器抓；最后才读本地 Cookie 库 | ✅ 已修 |
| 2 | 店铺设置 | 模板预览只显示标题截断 | 无法确认模板是否正确 | 预览显示：类目 / 运费 / 品牌 / 属性数 / 抓取字段数 | ✅ 已修 |
| 3 | 必填扫描 | 部分自定义必填组件识别不到 | 可能误跳过保存 | 增加"保存后读错误 toast"反向判定 | ✅ 已修 |
| 4 | 欧盟资质 | 选「是」需欧盟责任人资料 | 只能选否 | 选是时提示先填欧盟责任人 | ✅ 已修 |
| 5 | 图片空间不足 | 跳过上传但无自动清理 | 长期会满 | 增加"清理旧图"入口 | ✅ 已修 |
| 6 | 单产品耗时 | ~165s（3 次页面加载） | 500 产品需 8h | 合并模板/保存/发布为 1 次加载 | ✅ 已修（3 → 1 次加载） |

## 已确认可用

- 模型/价格/文本/白底（新 key）
- 店铺数据读取（在线 640 / 待发布 521 / 审核中 11）
- 模板 28 字段抓取与照抄
- 商品说明书复用模板
- 属性重复自动删除
- 预览页、提示词页、日志面板、多开

## 走查补充（第 2 轮）

| # | 页面 | 问题 | 影响 | 状态 |
|---|---|---|---|---|
| 7 | 全局布局 | 窗口 1514×945 时侧栏被截断，`任务与日志`/`提示词` 看不到 | 小屏用户找不到入口，需侧栏可滚动 | ✅ 已修 |
| 8 | 概览 | 「授权状态 见左下角」提示不明确 | 新手不知道去哪看 | ✅ 已修 |
| 9 | 概览 | 「API 服务 未检测」需手动点测试 | 应启动即自动检测一次 | ✅ 已修 |
| 10 | 测试工具 | `AURORA_PAGE` 环境变量在计划任务下不生效 | 仅影响自动化测试，非用户问题 | ✅ 已修 |

---

## 修复记录（第 2 轮，2026-11-17）

| # | 改了什么 | 关键文件 | 验证方式 |
|---|---|---|---|
| 1 | 新增统一取 Cookie 入口 `resolve()`：**手动配置 → 已开浏览器(CDP) → 本机库**；界面显示真实来源与数量（`手动配置 · 3 个`），新增「重新检测 Cookie」；缺登录标识时提示「可能已过期」 | `integrations/browser_cookies.py`、`ui/app.py`（`_cookies` / `_cookie_status_text` / `_p_store`） | 单元 6 项（优先级/回落/文案）+ 截图：店铺设置显示 `Cookie：手动配置 · 3 个` |
| 2 | 模板预览改为「标题（两行不截断）+ 模板 ID + 类目/运费/品牌/产地/属性数/抓取字段数」信息条；读取模板成功后自动刷新 | `ui/app.py`（`_template_preview_row` / `_read_template`） | 单元 7 项 + 截图：类目 `加工中心(Machining Centre)`、运费 `无忧物流-标准`、品牌、属性 3 项、抓取字段 4 个 |
| 3 | 必填扫描改为两档：`ant-form-item-required` 硬标记才跳过保存；`class*=required`/placeholder 弱标记只提示，**先尝试保存**。保存后读 `ant-form-item-has-error` + 错误 toast（自动过滤「保存成功」），最多 2 轮自动应答后重试，仍失败则报「保存被站点拦截：…」 | `integrations/editor_automation.py`（`SAVE_ERRORS_FN` / `_save_error_state` / `_answer_save_errors` / 必填扫描） | 单元 8 项（成功 toast 不误判、报错项抓取、异常不抛错） |
| 4 | 新增欧盟责任人 4 个配置项（名称/地址/邮箱/电话）；选「是」自动弹框补资料（可一键改回「否」）；启动前校验缺项；编辑器保存时按 label 回填，日志显示 `欧盟资质=是` 与缺失提示 | `config.py`、`ui/app.py`（`_bind_eu_guard` / `_ensure_eu_responsible` / `_validate`）、`editor_automation.py`（`_fill_eu_responsible`）、`services/publisher.py`（`_form_values`） | 单元 6 项（缺资料拦截/齐全放行/选否不要求/自动弹框） |
| 5 | 从相册页 JS 逆向出图片空间接口，新增 `services/album_cleanup.py` + 「清理旧图」入口（店铺设置第 4 卡 + 图片工作台按钮）：统计占用、**安全清理（只删未被引用的旧图，站点异步任务）**、扫描并按时间删除（锁定图自动跳过，删除前二次确认） | `integrations/dianxiaomi.py`（album_*）、`services/album_cleanup.py`、`ui/app.py`（`_open_cleanup_dialog` / `_load_space_info`） | 单元 14 项（扫描/锁定跳过/批量删除/失败提示/对话框入口）+ 空间不足提示改为引导「清理旧图」 |
| 6 | 合并页面加载：**视频上传 + 保存 + 发布**都在同一次编辑器加载里完成（发布优先在编辑器内点「发布」，未确认才回落列表页）；不再单独开视频页 | `integrations/editor_automation.py`（`_attach_video_in_page` / `_publish_in_page`）、`services/publisher.py` | 单元 10 项（签名/调用链/发布不强制开列表页/视频不再单独开页）+ 代码走查 |
| 7 | 侧栏：导航区独立 `Column(scroll=AUTO)`，两侧 `STRETCH` 等高，日志监控与授权状态固定底部；1514×945 及更小窗口也能点到 `预览/提示词/任务与日志` | `ui/app.py`（`_build_shell`） | 单元 6 项 + 截图 1514×945：全部导航项可见、可滚动 |
| 8 | 概览「授权状态」显示真实结果（`已授权` + 到期 / `未授权` + 原因）+ 「重新检测」按钮，不再写「见左下角」 | `ui/app.py`（`_p_overview` / `_apply_license_state` / `_check_license`） | 单元 5 项 + 截图：`已授权 / 永久有效 / 重新检测` |
| 9 | 启动即自动探测 API（`_probe_api_async`，5 分钟缓存，概览页复用），显示 `正常 · N 个模型 · x.xs` 或失败原因 | `ui/app.py`（`_probe_api_async` / `_apply_api_state` / `_test`） | 单元 4 项 + 截图：`连接失败：获取模型失败：401`（demo key 预期） |
| 10 | 启动页支持 `--page N` / `--page=N` / 页面文件（`AURORA_PAGE_FILE`，默认配置目录 `page.txt`）/ `AURORA_PAGE` / 配置 `start_page`，计划任务不再依赖环境变量；顺带修掉 `start % 12` 导致「预览」页无法作为启动页 | `ui/app.py`（`_start_page_index`）、`config.py`（`start_page`） | 单元 5 项（四种来源 + 回落） |

### 回归结论

- `tools/verify_issues.py`：**73/73 通过**（离线，不依赖网络/浏览器）
- `tools/button_audit.py`：触发 237 个回调，仅剩 1 个历史性假错（`FilePicker` 在假 Page 下未挂载，真实窗口正常）
- 截图（1514×945）：侧栏全量可见、店铺设置 Cookie/模板预览信息完整、概览授权与 API 自动检测正常
- 真机（店小秘）侧仍需在虚拟机上复跑一次「保存 + 发布」确认 `apply_and_save` 的页面内发布路径（自动化只能验证分派与回落逻辑）

---

## 第 3 轮：用户复检（2026-11-17）

用户复检提出的问题与处理：

| # | 反馈 | 事实核查（修复前） | 处理 | 状态 |
|---|---|---|---|---|
| 11 | 高级设置「水印图片路径」能用吗？ | 只在「叠加水印」开启时生效，但**没有选文件按钮**，路径要手输；且与 AI图片处理里的同名字段重复 | 新增「水印」卡片：选文件 + 缩略图/尺寸校验 + 一键「测试叠加」+ 嵌入「叠加水印到主图」开关；文字水印也支持 | ✅ |
| 12 | 定时发布能正常工作吗？ | **不能**：Publisher 遇到 `schedule_publish` 直接把「保存到店小秘」写成成功并 `return`，商品根本没保存，调度器也没登记 | 改成「现在保存 → 登记调度任务 → 到点发布」；无调度器时如实失败；新增时间格式/过期校验；「任务与日志」新增定时队列（立即发布 / 取消） | ✅ |
| 13 | 套装 SKU 能正常工作吗？ | 能算，但价格只看 SKU 自身价格（没有就丢失）、顺序交错、无预览 | 抽出 `services/combo.py`（原 SKU 在前 + 套装在后、价格 × 倍率、无价用成本兜底）+ 高级设置「套装 SKU 预览」 | ✅ |
| 14 | 图片工作台各项都有用吗？ | 大多可用，但发现：加载产品会因 `cfg.shop_id` 不存在而崩（真实 Cookie 下必现）；「编辑」只能用全局提示词；生成视频不看勾选；「从已拉取产品取图」只填 URL 不下载；队列只能入队管线产品 | 修复 `shop_id`；编辑提示词独立输入；视频用勾选图；取图即下载；队列支持工作台勾选产品；工具栏自适应换行 | ✅ |
| 15 | 主图四合一在哪设置？ | 有实现（`image_ops.four_in_one`）但只能靠 `four_in_one` 开关在刊登时追加，界面上看不到、也测不了 | 「AI图片处理 → 通用」新增四合一位置（追加/第一张）；「AI图片处理」工具条与「图片工作台」各加「四合一拼图」按钮，本地即出图 | ✅ |
| 16 | AI图片编辑无法逐图指定/文生图不结合素材/不能加第 2、3、4 张 | 只有「主图指定编辑」（一组提示词 + 序号），没有逐图提示词、没有多图素材，文生图是纯 text2img | 新增「逐图编辑方案」：每条 = 组（主图/描述图/SKU图）+ 第几张 + 参考图（1,2,3-5/all，可多张素材）+ 这一张的提示词 + 编辑/生成 + 追加为新图；可增删条目、可一键导入旧「指定编辑」；文生图默认以现有主图为素材（新增开关）；执行时按 目标图 + 参考图 多图输入调用 `/v1/images/edits` | ✅ |
| 17 | 背景替换规则提示词没生效 | 规则里的 `prompt` 被忽略，实际用的是全局 `edit_prompt`；`ip` 变量未定义导致翻译预检静默失效 | 规则按自己的提示词逐张换背景（支持 main_n / all_main / detail_n）；修复 `ip.has_text`；新增可视化「背景替换规则」编辑器（增删 + 位置下拉 + 提示词） | ✅ |

### 关键实现位置

| 功能 | 代码 |
|---|---|
| 套装 SKU | `services/combo.py`、`Publisher._normalize`、`ui/app.py::_combo_card` |
| 逐图编辑方案 | `services/image_edit_plans.py`、`Publisher._apply_edit_plans`、`ui/app.py::_plans_card` |
| 多图素材编辑 | `ApiClient.images_edit(model, prompt, [多张])`、`ImageService.edit_many` |
| 文生图带素材 | `Publisher.run`（`main_image_generate_use_refs`） |
| 定时发布 | `services/publisher.py`（登记）、`services/scheduler.py::add/cancel/pending_jobs`、`ui/app.py::_run_scheduled/_publish_scheduled_now` |
| 水印 | `services/image_ops.add_watermark`、`ui/app.py::_watermark_card/_test_watermark` |
| 四合一 | `services/image_ops.four_in_one`、`ui/app.py::_four_in_one_local`、`Publisher._local_image_ops` |
| 背景规则 | `Publisher._ai_images`、`ui/app.py::_bg_rules_card` |

### 回归

- `tools/verify_issues.py`：**109/109 通过**（新增：套装 5 项、逐图方案 13 项、定时 8 项、四合一 3 项、水印 2 项、背景规则 4 项、图片计划 2 项）
- `tools/button_audit.py`：252 个回调，仍只剩历史 FilePicker 假错
- 真窗口截图：高级设置（水印/套装预览）、AI图片处理（逐图方案 + 背景规则）、图片工作台均正常渲染

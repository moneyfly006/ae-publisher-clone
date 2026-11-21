# Alibaba 多商品真实实测与功能完善（第二阶段，2026-11-22）

> 目标：不只在 1 个商品上验证，而是把**整个采集箱**跑通；验证按钮/控件/AI 提示词/流水线；边测边修 UI。
> 安全边界：**仅 op=1 保存草稿**（用户明确授权多商品测试）；未发布、未提交审核、未删除、未改价；真实 AI 文本调用约 9 次（≈¥0.02）。

---

## 一、核心结果（先看这里）

| 项目 | 结果 |
|---|---|
| 采集箱草稿总数 | 28（真实店铺 9466975） |
| 真实 op=1 保存 | **28/28 成功，0 失败**（平台均返回「您的产品编辑成功！」） |
| 保存后缺失类目 | **27 → 0**（28 个草稿全部有合法叶子类目） |
| 类目分布 | 泳池 202107201 ×25、喷泉喷头 100001819 ×1、网袋 201730007 ×1、电动 ATV 201139102 ×1 |
| 价格模型 | 全部保持原模型（ladder 保留原阶梯价、sku 仍 sku_price + 原 productSku）；未改价 |
| 图片/详情 | 主图、详情未因批量保存被改写（changed 仅类目/关键词/属性/规范化字段） |
| AI 全链路 | 1 个商品（580）完成 **AI 标题 + AI 关键词 + AI 详情 → 校验 → op=1 保存成功**，回读标题/关键词/详情已更新 |
| 失败回滚信息 | 每次保存的 before/after 与 changed_fields 都在证据 JSON 中；类目置空 + 关键词清空即可回滚 |
| 发布/审核 | **仍未验证**（op=2/3/5 未调用；无审核状态接口实现） |

证据：
- `docs/icbu-batch-e2e.json`（前 9 个：8 个 op=1 + 580 单独）+ `docs/ICBU_BATCH_E2E.md`
- `docs/alibaba-batch2-verification.json`（剩余 19 个 op=1 批量）
- `docs/alibaba-ai-save-verification.json`（AI→保存全链路）
- `docs/AI_PROMPT_TEST.md` + `docs/ai-prompt-test.json`（提示词实测）
- `docs/design/ui-verify/`（21 张全页面截图）

---

## 二、本阶段新完成的功能

### 1. AI 提示词预设（7 套，v2）
- `src/aeclone/services/alibaba_prompts.py`：container_pool / machinery_industrial / electric_atv / general_industrial / detail_html / sku_naming / content_review。
- 真实实测：信息充足商品标题 5 分 vs 旧默认 3 分；详情 5 分 vs 4 分；0 中文/0 联系方式/图片占位 12/12；标题全部 ≤128。
- 发现并加固「AI 编造」问题：低信息商品 v1 标题把示例抄成 `40ft`/`fiberglass` → v2 删除示例并新增 `fabrication_warnings()` 本地体检。
- UI 已接入：AI 文案页 + Alibaba 刊登页的「Alibaba 行业预设」下拉（记住上次选择），套用后有版本与说明。

### 2. AI 关键词 / AI 属性建议 / 编造体检
- `TextPipeline.make_keywords()`：生成 3~5 个英文短语关键词（实测输出 `fiberglass swimming pool, container pool, ...`），无 cfg 关键词时自动使用，替代葡语/采集词。
- `TextPipeline.suggest_attributes()`：按类目候选值为缺失必填属性建议取值（只从候选里选），UI「AI 建议缺失属性」按钮 → 写入属性预设（需复核）。
- 流水线新增 `AI 编造体检` 步骤：原文没有的规格数字/材质/认证/绝对化词 → 以 warning 展示在预演结果里（不阻断，用户判断）。
- 标题清洗：en/em dash、全角标点、NBSP 统一为 ASCII，避免平台标题异常。

### 3. 必填属性安全策略（解决「静默写入错误规格」）
- 旧逻辑：无预设/无 Other 时直接取候选第一个值（ATV 被填 48V/<500W、网袋被填 Felt）。
- 新逻辑：只接受**预设命中候选值**或 Other/China 通用值；否则标记 unresolved，由 `validate_alibaba_save` 阻断并提示。
- 预设值若不在候选列表（如 Color=Blue 而候选只有 White/Other）→ 同样阻断，不写无效值。

### 4. UI 完善
- 12 号页工作流提示改为单行紧凑样式（原 Chip 在窄窗口会换行成一列）。
- 商品列表显示每行「类目 / 定价方式」，缺失类目红色提示；支持标题/ID 搜索、上一页/下一页、页码与空状态。
- 新增「按商品 ID 打开」（在线 3000+ 时不用翻页找）。
- 类目页新增「生成属性预设模板」「AI 建议缺失属性」。
- AI 文案页/Alibaba 页新增 Alibaba 行业预设下拉（记住上次选择）。
- 保存路径统一：全部走 `run_alibaba_product`（校验 + 授权 guard + `is_failure_msg`），AI/非 AI 结果格式一致。
- 发布队列在 Alibaba 平台只读 ICBU 数量；图片工作台入队/处理在 Alibaba 平台阻断；侧栏「启动任务」/⌘R 在 Alibaba 平台跳转 12 号页。

### 5. 真实商品实测发现并修复的代码问题
| 问题 | 状态 |
|---|---|
| `make_client` 不读 manual_cookies、Chrome 兜底函数名不存在 | 已修（Cookie 三级兜底） |
| 保存 UI 无 op、无二次确认、失败误报成功 | 已修（op 选择 + 确认弹窗 + 统一失败判定） |
| 选类目不写入 payload | 已修（resolve_category，overrides/cfg/商品原值） |
| FOB 模式残留阶梯价/HS | 已修 |
| 关键词被标题覆盖 / 葡语关键词 | 已修（保留原词 + AI 英文关键词） |
| 定价模型被默认 ladder_price 覆盖（sku/fob 被改） | 已修（显式标记 `alibaba_price_type_explicit`） |
| 分页参数 page → pageNo | 已修 |
| 违禁词接口 GET 404 → POST .json | 已修 |
| autofill 取候选第一个值（错误规格） | 已修（安全阻断 + AI 建议） |
| AI 标题/详情编造规格 | 已加本地体检 warning + v2 提示词加固 |
| 侧栏启动绕过平台守卫 | 已修（`_start`/`_run_pipeline` 双守卫） |

---

## 三、验证方式与覆盖

| 验证 | 方式 | 结果 |
|---|---|---|
| 真实 op=1 保存 | 28 个草稿逐个保存 + 2s 回读 | 28/28 成功，0 失败 |
| 类目/属性/关键词写入 | 保存前后快照比对 | 类目/关键词按预期变化；价格/SKU/图片未被改 |
| AI 全链路 | 预设 + AI 标题/关键词/详情 + 真实保存 | 成功，标题 85 字符、5 个英文关键词、详情 6693 字符 |
| AI 输出合规 | 8+ 次真实调用检查 | 0 中文/联系方式/引号/emoji；图片占位保留 |
| 全页面控件 | button_audit（13 页）+ options（210 态） | 404 回调 0 error；210 态 0 异常 |
| UI 截图 | 21 张（13 页 + 12 号页 5 Tab + 亮/暗 + 锁定页） | `docs/design/ui-verify/` |
| 离线/回归 | verify_regressions + test_offline + Alibaba 专项 | 见文末（最新一次全绿） |

---

## 四、仍未完成（不要夸大）

1. **发布/审核闭环**：op=2 提交审核、op=3 待发布、op=5 定时、审核失败原因/重提均未真实调用；需要单独授权与测试商品。
2. **新建 ICBU 商品**：当前只更新已存在商品。
3. **AI 图片写回 ICBU**：AI 文案已通；图片处理结果上传到 Alibaba 图片空间未接通。
4. **批量属性质量**：为让 28 个草稿通过校验，19 个新商品使用了通用占位属性（Color/Material/Type=Other、Applicable People=All、Origin=China）；**上线前需按真实规格修正**。
5. **AI 提示词 v2 抽样复验**：v2 已加固，但只对 576 类低信息商品做过 1 次验证（标题不再出现 40ft/fiberglass；详情仍有 fiberglass 类编造，已被 warning 捕获）。
6. **UI 按钮验证报告**：ui-button-verifier 的最终文字报告仍在收尾（截图已产出）。

---

## 五、复现

```bash
# 只读 + dry-run 全链路
.venv/bin/python tools/icbu_batch_e2e.py --post-check
.venv/bin/python tools/verify_alibaba_pipeline.py
.venv/bin/python tools/verify_publish_readiness.py
# 真实保存（默认 dry-run，--save 才写；内建 op=1/最多 8 个/禁 580）
.venv/bin/python tools/icbu_batch_e2e.py --save
# 测试
.venv/bin/python tools/verify_regressions.py
.venv/bin/python tools/test_offline.py
.venv/bin/python tests/test_alibaba_prompts.py
```

---

## 六、UI 全按钮验证（独立 verifier，快照 app.py 11014 行）

- 13 页 × dark/light 全部可构建：页面控件 1662（按钮 147 / 开关 53 / 下拉 35 / 输入 106 / 分栏 10），页面回调 316；壳层 304 控件 / 44 回调。
- 回调执行 486 次：OK 313 / DEFERRED 153 / NATIVE 11 / EXTERNAL 9 / **ERROR 0 / TIMEOUT 0**；无「有按钮无回调」控件。
- 官方 `button_audit.py`：411 回调 0 error；`verify_ui_options.py`：224 态 0 error。
- 12 号页专项 63 项 62 PASS；平台切换/锁定 90 项 0 fail；壳层 31 项 0 fail。
- 截图 21 张 + manifest：`docs/design/ui-verify/`；报告：`docs/UI_BUTTON_VERIFICATION.md`。
- 报告 P1 已在本轮修复：
  1) 类目搜索空关键词无反馈 → 提示「请输入类目关键词」；
  2) 展开子类目未选类目误报 Cookie → 提示先选类目；
  3) 选商品后 fullCid/类目名不同步 → 选商品即清旧路径并异步读取真实类目路径；
  4) 区域倍率模板空选择删除假成功 → 未选择时明确报错，不再假成功；
  5) 发布队列单条「发布」无二次确认 → 改为确认弹窗。
- P2 修复：套用预设不再跳回第一个 Tab；设置「启动时打开」补 Alibaba 刊登页；侧栏按钮在 Alibaba 平台显示「去 Alibaba 刊登」。
- 新增 `tests/test_ui_alibaba_actions.py`（5 项）：预演按钮、保存二次确认（取消不写入）、AI 生成并保存（use_ai=True、dry_run=False）、按 ID 打开、平台启动守卫。

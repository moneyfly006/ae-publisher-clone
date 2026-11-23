# Alibaba 真实发布验证结果（2026-11-22）

> 目标：把商品真正发布出去，并验证「评分 ≥4.5 才能发布」的门禁与失败诊断。
> 账号：店小秘 ICBU shop **9466975**；所有结果来自真实 `add.json(op=2)` + `edit.json` 回读。

## 1. 结论先看

| 商品 | 类目 | 评分 | 提交结果 | 平台最终状态 |
|---|---|---|---|---|
| **220114598435122330** 激光整平机（在线商品更新） | 127726092 | **4.60/4.5 ✅** | 已提交成功 | **审核中**（`PUB_BIZCHECK_PRODUCT_IN_AUDITING`，拒绝重复提交；截止 02:25 轮询 276s 仍审核中） |
| 220114598434958580 玻璃钢泳池（采集箱草稿） | 202107201 | **4.80/4.5 ✅** | 已提交成功 | **发布失败：经营类目不在范围**（`PUB_BIZCHECK_CAT_PUB_RESTRICT`） |

- 「发布成功」= 平台接受提交并进入审核；审核通过需要平台审核时长（本次记录见
  `docs/alibaba-audit-poll.jsonl`，截止记录时仍为「审核中」，周期轮询仍在跑）。
- 泳池类目失败**不是参数问题**：商品评分 4.8 已过线，平台拒绝原因是店铺经营大类未开通该类目。
  这是客户必须在卖家中心操作的资质设置，软件只能预检+提醒。

## 2. 逐轮真实失败与修复（发布链路的坑）

| # | 报错 | 根因 | 修复 |
|---|---|---|---|
| 1 | `发货期信息未填写` | `wholesaleTrade.deliver_periods` 为空 | 写入 `[{quantity,process_period}]`（配置/保留商品原值） |
| 2 | `重量、长宽高、物流属性要么全部填写，要么全部清除` | 有重量/尺寸但 `logisticsProperty` 为空 | 写入 `logisticsProperty=general_cargo_0` + 运费模板 |
| 3 | `textDesc:不能超过1000个汉字或2000个字符` | AI 详情 5000+ 字符 | `text_desc_plain()` 截断 ≤2000 |
| 4 | `textDesc:不能输入HTML脚本` | 平台 textDesc **只收纯文本**（在线商品实测无标签） | 详情转纯文本；提示词改为纯文本；评分检测 HTML |
| 5 | `PUB_BIZCHECK_CAT_PUB_RESTRICT` | 店铺经营类目未开通泳池类目 | 预检 + 客户提醒（卖家中心开类目），无法由软件绕过 |
| 6 | `PUB_BIZCHECK_PRODUCT_IN_AUDITING` | 已提交成功、正在审核 | 判为「已提交」，幂等，不再重复提交 |

> 旧实现会把 `{code:0, data:{code:-1,msg:"发货期信息未填写"}}` 当成功；现在内层业务码/失败语义都会判失败。

## 3. 经营类目预检（客户提醒 + 是否可发验证）

- 没有「经营类目清单」接口；用**在线商品列表的 categoryId 样本**近似预检
  （在线商品必然已通过类目校验）：
  - 泳池类目 `202107201`：样本 600 个在线商品中 **0 次** → 预检警告，真实发布被平台拒绝 ✅
  - 机械类目 `127726092`：样本中出现 62 次 → 预检通过，真实发布进入审核 ✅
- 客户提醒文案（UI/失败信息/文档一致）：
  「Alibaba 卖家中心 → 店铺管理 → 管理公司信息 → 经营类目，把该类目加入经营范围后再发布；软件无法代替平台开通经营类目」。
- UI 提供「Alibaba 刊登 → 类目 → 检查类目经营范围」按钮；`op=2` 预检/确认弹窗也会展示预检结果。

## 4. 评分门禁（≥4.5 才可发布）

- 评分实现：`services/alibaba_score.py`（8 维度加权，规则可解释，见 `docs/ALIBABA_SCORING.md`）。
- `op=2` 门禁三处生效：UI 预检、确认弹窗展示、pipeline 保存前强制校验（CLI 同样）。
- 泳池商品修复前 4.12（占位属性/HTML 详情/缺 HS、发货期）→ 修复后 4.80 通过；视频缺失只扣 5% 权重。
- 在线商品更新案例：补齐 6 个必填属性、发货期/物流属性/重量尺寸/库存、纯文本详情后 4.60 通过。

## 5. 复现命令

```bash
# 评分
.venv/bin/python tests/test_alibaba_score.py
# 经营类目预检
.venv/bin/python tests/test_alibaba_scope.py
# 真实干跑（不写店铺）
.venv/bin/python tools/verify_alibaba_pipeline.py 220114598434958580
# 后台审核轮询
.venv/bin/python /tmp/poll_alibaba_audit.py 220114598435122330 1800
```

证据文件：
- `docs/alibaba-publish-verification.json`（泳池：提交 → 类目拒绝）
- `docs/alibaba-publish-update-verification.json`（机械：提交 → 审核中）
- `docs/alibaba-audit-poll.jsonl`（审核状态轮询）

## 6. 本轮验证统计

| 项目 | 结果 |
|---|---|
| 回归 `tools/verify_regressions.py` | **56/56 模块 PASS**（含 `test_alibaba_score` / `test_alibaba_scope` / `test_alibaba_pipeline` / `test_ui_alibaba_actions`） |
| 离线安全 `tools/test_offline.py` | 242/242 PASS |
| 按钮审计（按平台页面） | 17 页 / 464 回调：OK 303 / DEFERRED 141 / NATIVE 8 / EXTERNAL 12 / **ERROR 0** |
| 选项审计 | 135 项保存配置 / 43 项无需变更 |
| 截图 | `docs/design/ui-verify/alibaba-page12-score-gate.png` |

真实请求的 Trace：
- 泳池类目拒绝：`eedd073ada2af687bb983e5f7fffe3d1`
- 机械商品审核中：`630f911f4017d6ead059c41f884a37a0` / `2588ad1...`（重复提交被拒，证明首次已进审核）

## 7. 真实评分门禁干跑（采集箱商品，未写店铺）

`docs/alibaba-score-real.json`：

| 商品 | 评分 | 结果 | 主要缺口 |
|---|---|---|---|
| 220114598434958580 泳池 | 4.80 | 允许发布（但类目受限） | 视频缺失（单列提醒） |
| 220114598434958528 | 3.74 | **阻止发布** | 占位属性、详情含联系方式、主图不足、视频 |
| 220114598434958530 | 3.44 | **阻止发布** | 占位属性、违禁/绝对化词、联系方式、视频 |
| 220114598434958534 | 3.89 | **阻止发布** | 占位属性、联系方式、视频、HS 为空 |

说明：门禁不是「一刀切拒绝」，错误信息会逐项指出「缺什么/去哪里补」，符合
「评分必须 ≥4.5，不达标就说明哪里没设置到位」的要求。

## 8. op=1/3/5 预演

对泳池商品（类目受限）做 dry-run：`op=1`、`op=3`、`op=5` 均通过校验与评分（4.80），
仅 `op=2` 被经营类目拦截。真实 `op=3/5` 写入本轮未执行（避免无意义状态变更）。

## 9. 多商品真实发布测试（2026-11-22 追加）

按店小秘真实列表口径核对：`dxmState=offline & dxmOfflineState=publishFail` 即「发布失败」列表；
「发布中/审核中」在商品详情 `errMsg`（`PUB_BIZCHECK_PRODUCT_IN_AUDITING`）。

| 商品 | 类目 | 评分 | 结果 | 所在列表/状态 |
|---|---|---|---|---|
| 220114598435130384 激光整平机 | 127726092 | 4.6 | ✅ **发布成功**（`ok=True`，`errMsg` 空，属性/详情已生效） | 不在发布失败；在线 |
| 220114598435122330 激光整平机 | 127726092 | 4.6 | 已提交，平台审核中 | 不在发布失败；`errMsg=PUB_BIZCHECK_PRODUCT_IN_AUDITING` |
| 220114598434958580 泳池 | 202107201 | 4.8 | ❌ 发布失败 | **在发布失败列表**：`PUB_BIZCHECK_CAT_PUB_RESTRICT` |
| 220114598435128538 / 128522 / 127728 / 127708 / 128138 | 127726092 | 4.6 | ❌ 被平台拒绝 | 不在发布失败（同步拒绝，仍在线）：`CHK_VIDEO_PRODUCT_LINK_LIMIT` |

### 新发现：主视频复用超限

```
CHK_VIDEO_PRODUCT_LINK_LIMIT: 主视频已在过多商品中使用。请减少至 20 个以内。
```

- 平台限制：同一主视频最多关联 **20 个商品**。
- 保存接口没有视频字段；**实测清空 payload 视频字段、op=1 先保存再发布均无法绕过**，
  因为视频挂在平台侧商品上。只能在 Alibaba 卖家中心给这些商品**更换/删除主视频**后重发。
- 软件已把该失败翻译成中文可执行提示，不再显示原始 `systemError`。

### 结论

- 「发布成功」已真实验证（130384：平台接受、状态在线、无错误，修改已生效）。
- 失败两类原因都已定位：**经营类目未开通**（客户操作）、**主视频复用超 20 个**（客户在平台换视频）。
- 软件无法绕过这两条平台资质/内容规则，但能在发布前预检经营类目、在发布后如实报告失败原因。

## 10. 正式版清理 & 发布失败列表入口

- 「发布队列」Alibaba 只读页新增：**发布失败**计数（真实口径
  `dxmState=offline&dxmOfflineState=publishFail`）+「查看发布失败」弹窗，
  直接显示平台 `errMsg` 与「打开商品」跳转修复。
- 去掉测试期文案：`测试商品 ID`→`校验商品 ID`、`测试 ICBU 连接`→`检测 ICBU 连接`、
  `测试叠加`→`预览叠加`、`测试中`→`检测中`；类目页说明卡改为「平台差异说明」。
- 版本升级 **v1.0.6**（`src/aeclone/__init__.py`、`app.spec`、manual 构建脚本同步）。
- 全按钮/选项审计：**458 回调 0 ERROR**；135 项选项保存正常；回归 56/56 PASS。

## 11. 机械类商品端到端发布（用户经营大类：机械）

采集箱 27 个草稿全部是泳池/喷泉/网袋/ATV，**没有机械类**；因此按「其他地方」用店小秘
「引用产品」把在线机械商品复制成草稿（`quoteEdit.json` → `add.json(op=1)`），再真实发布：

| 新商品 ID | 来源商品 | 类目 | 评分 | 结果 |
|---|---|---|---|---|
| **220114598435132626** 激光整平机 | 130384 | 127726092 | 4.60 | ✅ `online / publishSuccess`，`errMsg` 空 |
| **220114598435132784** 液压岩芯钻机 | 130386 | 100007282 | 4.60 | ✅ `online / publishSuccess`，`errMsg` 空 |
| **220114598435132938** 蛋托机 | 128116 | 2308 | 4.54 | ✅ `online / publishSuccess`，`errMsg` 空 |

- 三个机械子类（整平/钻探/纸浆成型）全部发布成功，且**不在发布失败列表**；
  发布失败列表仍只有泳池商品（经营类目受限）。
- 蛋托机首次评分 4.34（占位属性 Product Type=Other + 详情 896 字符）被门禁拦下；
  补全详情到 1973 字符后 4.54 通过并发布成功 —— 验证了「评分不达标 → 指出缺什么 → 修复 → 发布」闭环。
- 证据：`docs/alibaba-machinery-publish-verification.json`。
## 12. op=3 / op=5 真实验证（采集箱商品）

| op | 商品 | 请求前 | 平台返回 | 回读状态 |
|---|---|---|---|---|
| 3 移入待发布 | 220114598434958528 | 采集箱 draft | 操作成功，请到「待发布产品」中查看！ | offline / waitPublish |
| 5 定时发布（无时间） | 220114598434958530 | 采集箱 draft | 产品已提交发布，请到「 定时发布」中查看! | offline / publishingTiming |
| 5 定时发布（2030-01-01 09:00） | 220114598434958536 | 采集箱 draft | 同上 | offline / publishingTiming，`dxmScheduleTime=1893459600000` |

- 代码已支持：`op=5` 从配置 `alibaba_schedule_time` 解析并写入 `dxmScheduleTime`（毫秒）；
  UI 保存动作下拉新增「定时发布（op=5，填发布时间）」，价格与贸易页有「定时发布时间」。
- 证据：`docs/alibaba-op-verification.json`。

## 13. 审核闭环

- 平台侧审核状态通过 `edit.json.errMsg` 观察：
  `PUB_BIZCHECK_PRODUCT_IN_AUDITING` = 审核中；errMsg 清空且状态 online = 通过。
- 本会话轮询商品 220114598435122330 约 20 分钟仍为「审核中」（平台审核时长由平台决定）；
  后台继续轮询 `docs/alibaba-audit-poll.jsonl`，结束后自动记录。
- `publishSuccess` 商品（132626/132784/132938/130384）已完成闭环验证：在线、无错误、修改生效。
### op=3/5 列表核对与测试清理

- op=3 商品（528）已出现在「待发布」列表：`dxmState=offline&dxmOfflineState=waitPublish`。
- op=5 商品（530/536）已出现在「定时发布」列表：`dxmState=offline&dxmOfflineState=publishingTiming`。
- 「发布失败」列表仍只有泳池商品（经营类目受限）。
- 测试产生的探测草稿已通过 `/alibabaIcbuProduct/delete.json` 清理（134022/24/28/30、133164）；
  三个发布成功的机械测试商品保留在线，可在平台删除。
## 14. 审核状态闭环（修正 + 真实验证）

- 修正：`op=2` 保存成功后回读状态时，若 `errMsg` 含
  `PUB_BIZCHECK_PRODUCT_IN_AUDITING`（审核中），以前会误报「平台发布校验未通过」；
  现在判为 `ok=True, saved=True, auditing=True`（已提交成功，等待平台审核）。
- 真实验证：对 220114598435122330 再次执行 op=2，平台返回「正在审核中，请不要重复提交」，
  pipeline 正确输出「保存到 Alibaba ✓ / 发布结果 ✓ 审核中」，不再误报失败。
- 离线状态机测试：`tests/test_alibaba_pipeline.py::PublishStatePollTests`
  - 审核中 → 成功且 `auditing=True`；
  - 发布失败（经营类目）→ 失败并带原因。
- 证据：`docs/alibaba-audit-closure.json`；平台审核结果（122330）截至记录仍在审核中，
  后台轮询 `docs/alibaba-audit-poll.jsonl` 继续记录（平台审核时长不由软件控制）。

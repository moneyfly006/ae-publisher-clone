# AuroraList · Alibaba 刊登功能验证与完善（2026-11-22）

> 执行者：Lead + 3 名独立 teammate（publish-verifier / ui-auditor / alibaba-builder）。
> 代码快照：`git HEAD 9a50a72` + 本轮未提交改动（app.py sha256 `9d7dbae0…`）。
> 安全边界：真实店铺写入仅 **1 次 op=1 草稿保存（用户明确授权）**；未发布、未重试、未删除、未调付费 AI。

---

## 一、结论：现在能不能正常刊登？

| 能力 | 状态 | 证据 |
|---|---|---|
| 直连 ICBU 读取商品 / 类目 / 必填属性 / schema | ✅ 已验证（真实只读） | 商品 220114598434958580：110 字段、类目 202107201 叶子、required 5 项、schema 8 项 |
| 构建 `alibabaSave` + 必填校验（本地、不保存） | ✅ 已验证 | `build_alibaba_save` + `validate_alibaba_save` → 0 issues |
| AI 文案 → 校验 → 预演（不保存、不花钱可关 AI） | ✅ 已验证 | `run_alibaba_product(dry_run=True, use_ai=False)` 9 步全绿、`save_called=false` |
| **op=1 保存草稿（真实写入）** | ✅ **已验证 1 次** | 平台返回「您的产品编辑成功！」；回读 `changed_fields=[]`；草稿数 28→28；0.6s |
| op=3 移入待发布 / op=2 提交审核 / op=5 定时 | ⚠️ 代码与 UI 已暴露（op=5 除外），**无真实写入证据** | 仅离线断言 op 透传；发布/审核语义未知 |
| 新建 ICBU 商品 | ❌ 未实现 | 现有链路只更新已存在商品（需先在店小秘/平台创建） |
| 审核状态/失败原因拉取 | ❌ 未实现 | ICBU 列表只能读 draft/online 两类 |

**一句话**：对「店小秘里已存在的 Alibaba 商品」，软件已能完成 读取 → 类目/必填校验 → 预演 → 保存草稿；发布/待发布/审核闭环仍需真实环境继续验证。
另外：SMT（速卖通）发布管线 **不能**用于 Alibaba 平台；本轮已把 Alibaba 平台的主入口从 SMT 管线切到「Alibaba 刊登」页，避免跨平台误写。

---

## 二、本轮修复（按严重度）

### P0（已修）
1. **保存路径失败语义**：12 号页「保存到 Alibaba」改走唯一收口 `run_alibaba_product(dry_run=False, use_ai=False)`，统一 `is_failure_msg` 判定并显示分步结果；不再把「code=0 但 msg 是失败」显示成绿色成功。
2. **Alibaba 主入口误走 SMT**：概览「启动/选择商品/搜索全部商品/03 工作流」在 Alibaba 平台改为跳「Alibaba 刊登」页；概览数字改为 ICBU 草稿/在线；图片工作台「产品入队/处理队列」在 Alibaba 平台阻断并提示。发布页在 Alibaba 平台只读 ICBU 数量，不再提供 SMT 批量发布/重试按钮。
3. **侧栏全局启动 / ⌘R 仍走 SMT**：`_start()` 与 `_run_pipeline()` 增加 Alibaba 平台守卫——侧栏「启动任务」、快捷键、定时器在 Alibaba 平台都会跳到 12 号页并记录警告，服务层也拒绝执行速卖通管线（UI 之外同样拦截）。

### P1（已修）
3. **Cookie 读取缺陷（阻断保存）**：`make_client` 现在按 `alibaba_cookies → manual_cookies → 本机浏览器` 取 Cookie；修复原 `read_chrome_cookies` 不存在的 ImportError（Chrome 已登录也报「没有 Cookie」）。
4. **UI 选类目不生效**：新增 `resolve_category`，保存包 `categoryId/fullCid` 真正按 overrides > cfg > 商品原值写入；校验与保存使用同一类目上下文。
5. **FOB 残留字段**：FOB 模式清空 `bulkDiscountPrices`、移除 `tariffsHsCode`（符合 ALIBABA_RULES §4）。
6. **脏数据提交风险**：`validate_alibaba_save` 新增阻断：类目为空/`"None"`/非数字、fullCid 末段不一致、`sku_price` 无有效 SKU（按原因分别提示）。
7. **关键词被标题覆盖**：`keywords_effective` 改为 overrides > cfg > 商品原关键词 > 标题提取；无配置时保留原短语。
8. **定价模型静默改写**：新增 `alibaba_price_type_explicit` 标记；未显式选择时跟随商品原 `priceType`（sku_price 不再被改成空阶梯价、fob_price 不再被默认 ladder 覆盖），UI 新增「定价方式」三选一 + FOB 币种/最低/最高价，用户显式选择后才以配置为准。
9. **分页 bug**：`list_products` 分页参数由 `page` 改为 `pageNo`（原实现永远返回第 1 页）；文档 §2 已注明。
10. **违禁词接口**：`check_banned_words` 改 POST `/bannedWord/alibabaCheckBannedWord.json`（原 GET 一直 404）；失败只作提示、不再记成失败步骤。
11. **导航搜索失效**：平台化改造丢失的搜索别名（库存/水印/阶梯…）恢复；导航标签与页面内容对齐（店铺与模板 / 价格阶梯批发 / SKU·库存·套装 / 发布队列 / 任务与日志）。
12. **新增 12 号「Alibaba 刊登」页**：商品选择（搜索+翻页+空状态）、类目选择（含子类目展开，修复 NameError）、预设资料、价格与贸易、预演/保存（op 选择 + 二次确认）、AI 预演/AI 生成并保存（付费开关，默认关）。
13. **失败可见性**：概览/发布页数量读取失败不再静默；店铺为空时给出「切换到有商品的店铺」提示与一键切换（含各店草稿数）。
14. **原生文件选择器接线**：图片工作台「导入本地图片」、商品视频「选择本地视频」。
15. **定时发布入口**：速卖通运行设置新增「定时发布」开关 + 发布时间（任务与日志页可查看/取消）。ICBU op=5 仍无时间字段，未冒充已实现。

### 测试新增
- `tests/test_alibaba_pipeline.py`（23 例，离线）
- `tests/test_alibaba_payload.py` 扩展（27 例）
- `tests/test_publish_readiness.py`（12 例，只读/预演脚本的离线校验）
- 已加入 `tools/verify_regressions.py` 统一回归。

---

## 三、验证结果

| 项目 | 结果 |
|---|---|
| `tools/verify_regressions.py` | **45/45 PASS** |
| `tools/test_offline.py` | **63/63 PASS** |
| `compileall -q src` | PASS |
| 真实只读核验 | 列表/详情/类目/属性/schema/分页 全 PASS |
| 真实 dry-run（不保存） | 9 步全绿、0 issues、未调用保存接口 |
| 真实 op=1 保存（用户授权一次） | 「您的产品编辑成功！」、回读无差异、草稿 28→28 |
| UI 截图 | `docs/design/alibaba-listing-page.png`、`docs/design/alibaba-listing-preview-tab.png` |

机器可读证据：
- `docs/publish-readiness.json`（只读快照 + gate + 分级）
- `docs/alibaba-pipeline-verification.json`（流水线只读+dry-run）
- `docs/alibaba-real-save-verification.json`（真实 op=1 保存前后对照）
- `docs/ALIBABA_VERIFICATION.md`（独立验证报告）
- `docs/UI_INCOMPLETE_AUDIT.md`（独立 UI 审计：P0/P1/P2 清单）

---

## 四、仍需继续（不夸大）

1. **op=2/3/5 真实行为**：需要再授权一次（建议先用 1 个草稿商品验证 op=3；op=2 会送平台审核）。
2. **新建商品路径**：当前只更新已有商品；新建需确认 `add.json` 在 `id` 为空时的行为或走卖家中心。
3. **审核闭环**：审核状态/失败原因/重提未实现。
4. **真实数据阻塞**：9466975 的 28 个草稿中 **27 个类目为 `"None"`**、13 个无 `productSku`——新校验会全部拦截，需先补类目/SKU 才能刊登。
5. **AI 图片写回 ICBU**：AI 文案已接入；图片处理结果写回 Alibaba 还需确认图片空间上传方式。
6. **UI 审计剩余项**：旧授权缓存/锁定页重渲染、部分 P2 文案与重复入口（见审计报告）。

## 五、复现命令

```bash
.venv/bin/python tools/verify_regressions.py
.venv/bin/python tools/test_offline.py
.venv/bin/python tools/verify_alibaba_pipeline.py        # 只读 + dry-run
.venv/bin/python tools/verify_publish_readiness.py       # 只读 + dry-run
.venv/bin/python tests/test_alibaba_pipeline.py
.venv/bin/python "tests/test_ui_redesign.py"
```

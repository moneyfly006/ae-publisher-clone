# 平台 UI 匹配：Alibaba 去速卖通化（多平台融合边界）

> 目标：Alibaba 平台只展示「Alibaba 直连 ICBU 刊登」真正用得到的 UI；速卖通/Amazon 等平台**完全不受影响**。
> 实现方式：`platforms.PLATFORM_CAPS` 能力开关 + 页面条件渲染/页面清单裁剪，而不是在共享页面里到处写 if 平台字符串。

---

## 1. 平台能力开关（唯一裁剪入口）

`src/aeclone/platforms.py`

```python
PLATFORM_CAPS = {
  "alibaba": {
    "smt_shop_template": False,   # 店铺选择 + 模板商品（SMT 保存用，ICBU 不需要）
    "smt_category_source": False, # 模板类目/自动分类
    "smt_product_settings": False,# 原箱/申报货值/高关注化学品/规格默认值
    "smt_attr_templates": False,  # 属性模板与自定义属性清理
    "eu_qualification": False,    # 欧盟资质
    "legacy_price_page": False,   # SMT 价格页（Alibaba 报价在 12 号页）
    "smt_sku_page": False,        # SKU 编码/库存/套装页
    "marketing_images": False,    # 营销图 1:1/3:4
    "smt_queue": False,           # 图片工作台产品入队/队列
    "scheduled_publish": False,   # 定时发布
    "smt_detail_assembly": False, # AI 文案-详情内容增强
    "smt_extra_ai": False,        # SKU 优化/读图估重 AI 卡
    "smt_prompt_library": False,  # 速卖通/通用提示词预设库
    "smt_resume": False,          # 上次未完成续跑卡
    "smt_template_ready": False,  # 概览的「模板商品」就绪项
    "icbu_listing": True,         # Alibaba 直连刊登页
  },
  # 其它平台不声明 = 全部保留 True（默认值）
}
```

读取：`from aeclone.platforms import cap` / `App._cap("name")`。
**未声明的平台/能力默认 True**，因此不会因为 Alibaba 的裁剪影响速卖通/Amazon。

## 2. Alibaba 平台页面清单（导航裁剪）

| 页面 ID | Alibaba | 速卖通 | 说明 |
|---|---|---|---|
| 0 概览 | ✅ | ✅ | 数字/工作流/准备状态已 Alibaba 化 |
| 1 | **Alibaba 连接** | 店铺与模板 | 只连 Cookie + ICBU 店铺/测试商品，无模板 |
| 2 类目与属性 | **Alibaba 类目状态页** | 类目与规格/属性模板/欧盟资质 | 编辑入口统一到 12 号页 |
| 6 价格 | ❌ 不展示 | ✅ | Alibaba 报价在 12 号页「价格与贸易」 |
| 7 SKU·库存·套装 | ❌ 不展示 | ✅ | ICBU 保存不改 SKU；无套装概念 |
| 8 发布队列 | ✅（只读 ICBU） | ✅（SMT 队列） | Alibaba 不提供 SMT 批量发布/重试 |
| 12 Alibaba 刊登 | ✅ | ❌ | 仅 Alibaba |
| 3/4/5/9/10/11 | ✅（裁剪 5 项） | ✅ | 见下 |

## 3. 共享页面里的裁剪明细（Alibaba）

| 页面 | 移除 | 保留/替换 |
|---|---|---|
| 概览 0 | SMT「启动任务」按钮语义、关键词筛选、选择商品、搜索全部商品、模板商品就绪项、续跑卡 | 「去 Alibaba 刊登」、刷新数量、草稿/在线数字、Alibaba 商品/类目就绪项 |
| 连接 1 | 店铺下拉、模板来源、模板商品、类目映射/模板库 | Cookie 检测/粘贴、ICBU 店铺 ID、测试商品 ID、测试 ICBU 连接、去刊登 |
| 类目 2 | 原箱、申报货值、高关注化学品、是否带电、欧盟资质、模板类目来源、属性模板/自定义属性清理 | 类目状态汇总、属性预设条数、去选类目/去属性预设 |
| AI 文案 3 | 详情内容增强 Tab；SKU 优化/读图估重卡；速卖通/通用提示词预设库 | Alibaba 行业预设 + 标题/详情能力卡（提示词仍可微调） |
| AI 图片 5 | 营销图 Tab（1:1/3:4） | 简单编辑、精细化编辑、输出与水印、商品视频 |
| 发布队列 8 | SMT 批量发布/重试/待发布列表 | 只读 ICBU 草稿/在线数量 + 查看列表 + 去刊登 |
| 图片工作台 9 | SMT 产品加载（状态/排序/翻页/勾选取图）、任务队列 Tab、SMT 步骤文案 | URL/本地图片取图、图片处理与输出、上传结果 |
| 任务与日志 10 | 定时发布 Tab（SMT 调度器） | 运行日志、诊断/导出/上报 |
| 设置 11 | 「启动时打开」只列当前平台页面 | 账号/API/图床/界面/更新/多开/授权 |
| 顶部/侧栏 | 侧栏「启动任务」→「去 Alibaba 刊登」 | 平台切换、主题、日志、店铺快捷入口 |

## 4. 验证（不靠人工目测）

1. `tests/test_platform_ui_filter.py`：页面清单与能力开关的边界断言（Alibaba 无 6/7、速卖通 0-11、Amazon/TikTok/Shopee 默认不受影响）。
2. `tests/test_ui_redesign.py`：
   - `test_all_platform_pages_build_in_both_themes`：Alibaba 实际页面在明暗主题全部可构建；速卖通必须保留 0-11 全部页面；
   - `test_alibaba_hides_smt_only_pages_and_controls`：Alibaba 类目页无 SMT 输入项、图片页无营销图、日志页无定时发布；切回速卖通后全部恢复；
   - `test_feature_groups_keep_every_settings_card_and_variant_tool`：按平台核对 Tab；速卖通 9 个设置页 Tab 原样。
3. `tools/button_audit.py` / `verify_ui_options.py`：改为按平台实际页面遍历（Alibaba 11 页 + 速卖通独有 6/7），0 error。
4. 截图：`docs/design/ui-verify/alibaba-trimmed-{0,1,2,9,10}.png`。

## 5. 边界与未做

- Capability 只控制 UI 是否展示；底层 `Publisher`（SMT）代码保留给速卖通，不在 Alibaba 平台可达。
- Amazon 平台由并行工作流维护（页面 13 Amazon 刊登），本改动不为 Amazon 增加/删除能力（默认值保持）。
- Alibaba 的「发布/审核」仍只读；提交审核（op=2）需在 12 号页确认后执行，属真实写入。

---

## 6. 最终验证结果（本次）

| 项目 | 结果 |
|---|---|
| 回归 `verify_regressions.py` | **48/48 PASS**（含 `test_platform_ui_filter.py`、`test_ui_alibaba_actions.py`） |
| 离线安全 `test_offline.py` | 63/63 PASS |
| 全按钮审计（按平台页面） | 13 页（Alibaba 11 + 速卖通独有 6/7）355 回调：OK 228 / DEFERRED 111 / NATIVE 8 / EXTERNAL 8 / **ERROR 0** |
| 开关/下拉审计 | 按平台页面遍历，0 error（Alibaba 无死开关） |
| 页面构建 | Alibaba 11 页 + 速卖通 12 页，明/暗主题全部可构建 |
| 平台切换 | Alibaba→速卖通：被裁剪的页面/卡片全部恢复（测试断言） |
| 截图 | `docs/design/ui-verify/alibaba-trimmed-{0,1,2,3,5,9,10}.png` |

复现：

```bash
.venv/bin/python tests/test_platform_ui_filter.py
.venv/bin/python tests/test_ui_redesign.py
.venv/bin/python tools/button_audit.py --json docs/ui-button-audit.json
.venv/bin/python tools/verify_regressions.py
```

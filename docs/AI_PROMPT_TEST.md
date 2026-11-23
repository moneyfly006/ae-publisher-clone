# Alibaba AI 提示词预设设计与真实 AI 实测（task-6）

- 日期：2026-11-22 · 工作区：`/Users/apple/Projects/alibaba-lister`
- 任务：为 Alibaba 刊登设计行业 AI 提示词预设（标题/详情/SKU/关键词/审查），并用真实 AI 在 2 个真实草稿商品上对比「旧默认提示词 vs 新 Alibaba 预设」。
- 机器可读结果：[docs/ai-prompt-test.json](ai-prompt-test.json)

## 0. 结论先行

| 问题 | 结论 |
|---|---|
| 新预设是否优于旧默认？ | **条件性优于**。原文信息充足的商品（580）新预设标题/详情都明显更好（标题 5 分 vs 3 分，详情 5 分 vs 4 分，参数密度更高）；原文几乎没有规格的商品（576）v1 新预设出现**编造**（把示例 `40ft`/`fiberglass` 写进标题），旧默认更保守。整体均分持平，但分布不同。 |
| 推荐哪套？ | **`container_pool`（v2 加固版）作为 Alibaba 泳池/集装箱泳池类目默认预设**；配 AI 输出后置体检 `fabrication_warnings()`。机械/ATV/通用工业品预设已就绪，但本次预算只够实测泳池预设，建议上线前各抽检 3~5 个真实商品。 |
| 输出有什么问题？ | ① 示例数字泄漏导致标题编造（576：`40ft`、`Fiberglass` 原文都没有）；② 原文无参数时模型倾向补全通用规格；③ 标题使用 en dash `–`（平台不禁止，可统一为 `-`）；④ 详情约 6.4k~7.1k 字符，textDesc 长度上限本次未保存无法验证；⑤ 关键词/SKU/审查的 AI 生成链路尚未接（TextPipeline 没有 make_keywords）。 |
| 实测合规情况 | 8 次文本调用全部通过基础合规：**0 中文、0 联系方式（原文含 WhatsApp/邮箱/电话均被清理）、0 引号/emoji、0 代码块、平台违禁词接口 0 命中**；详情图片占位 **12/12 全部保留**；标题 **全部 ≤128 字符**。 |
| 成本 | 8 次调用 ≈ **¥0.010**（token 估算）；网关账单前后差 1.7852 units（≈$0.018，仅量级参考）；`qwen-plus per_call=0`、余额充足。 |
| 安全边界 | 未保存商品、未写店铺、未调用图片/视觉模型、config 只在内存副本改（未落盘）、未打印 Cookie/api_key。 |

## 1. 交付物与接入方式（Lead 负责 app.py 接线）

| 文件 | 内容 |
|---|---|
| [src/aeclone/services/alibaba_prompts.py](../src/aeclone/services/alibaba_prompts.py) | 7 套预设 + `resolve_preset` / `apply_to_cfg` / `preset_options` / `fabrication_warnings`，无第三方依赖 |
| [tests/test_alibaba_prompts.py](../tests/test_alibaba_prompts.py) | 离线脚本式测试，29 项全部 PASS（`sys.exit 0/1`） |
| [docs/ai-prompt-test.json](ai-prompt-test.json) | 预设清单、实测指标、评分、耗时、成本、结论（机器可读） |
| 本文件 | 预设设计、实测方法/结果、问题与建议、接入键名 |

### 1.1 模块 API（可以直接 import 的键名）

```python
from aeclone.services import alibaba_prompts as AP

AP.PRESETS                 # dict[id] -> 预设全量字段
AP.PRESET_IDS              # ["container_pool", "machinery_industrial", ...]
AP.INDUSTRY_PRESET_IDS / AP.FUNCTION_PRESET_IDS
AP.DEFAULT_PRESET_ID       # "container_pool"
AP.PROMPT_KEYS             # ("title_prompt","detail_prompt","sku_prompt","review_text_prompt")
AP.resolve_preset("泳池")   # 也支持 pool / 集装箱泳池 / 预设 id / None（回退默认）
AP.apply_to_cfg(cfg, preset) -> list[str]   # 只写 4 个 prompt 键，不 save()
AP.preset_options()        # UI 下拉 [(id, 名称), ...]，保持定义顺序
AP.get_prompt("title_prompt", "atv")
AP.describe_preset("pool") # 一行摘要（toast 用）
AP.fabrication_warnings(text, source_text) -> list[str]  # AI 输出后置编造体检
AP.PRESET_VERSION          # "2026-11-22.2"
```

`apply_to_cfg` **只写这 4 个配置键**，不写其它键、不调用 `cfg.save()`：

| 预设字段 | 写入的 cfg 键 | 用途 |
|---|---|---|
| `title_prompt` | `title_prompt` | `TextPipeline.make_title` 的 system prompt |
| `detail_prompt` | `detail_prompt` | `TextPipeline.make_detail` 的 system prompt |
| `sku_prompt` | `sku_prompt` | `TextPipeline.make_skus` 的 system prompt |
| `review_prompt` | `review_text_prompt` | `TextPipeline.review_text` 的 system prompt |

### 1.2 建议的 UI 接线（Lead 在 app.py 实现）

1. **AI 文案页「提示词预设库」**：标题/详情/SKU 三行下拉旁增加一组「Alibaba 行业预设」下拉，`options=AP.preset_options()`；点「套用」执行：
   ```python
   preset = AP.resolve_preset(_dd.value or AP.DEFAULT_PRESET_ID)
   written = AP.apply_to_cfg(self.cfg, preset)   # 内存写 4 个 prompt 键
   save_config(self.cfg)                          # 持久化由 app.py 负责
   self.toast(f"已套用 {AP.describe_preset(preset)}")
   ```
   建议新增配置键 `alibaba_ai_preset`（默认 `"container_pool"`）记住上次选择——属于 config.py 改动，**不在本任务写入范围**，由 Lead 决定。
2. **Alibaba 刊登页（12 号）AI 预演/生成**：AI 返回后调用 `fabrication_warnings(result.subject + " " + 详情纯文本, 原 subject + 原详情纯文本)`，命中项作为 warning 展示（不阻断保存，由用户确认）。文本清洗可用 `aeclone.services.alibaba_pipeline._plain_text` 或自行 `re.sub(r"<[^>]+>", " ", html)`。
3. **内容审查页**：可用 `apply_to_cfg(cfg, "content_review")` 把 `review_text_prompt` 换成 Alibaba 合规版；图片审查提示词（`review_main_img_prompt` / `review_detail_img_prompt`）本次未覆盖，保持原值。
4. **不要改 TextPipeline 上限逻辑**：`make_title` 内部仍按 125 字符截断、`alibaba_pipeline.clip_title` 兜底 128。预设已要求 AI 输出 ≤128（本次实测 79~103），无需动代码。

## 2. 预设清单（7 套）

| id | 名称 | 类型 | 适用品类 | 标题/详情/SKU 提示词长度（字符，当前 v2） | 关键词策略 |
|---|---|---|---|---|---|
| `container_pool` | 集装箱泳池 / 泳池设备（主推） | industry | 集装箱泳池、玻璃钢/FRP 泳池、地上泳池、泳池梯/过滤泵 | 632 / 848 / 382 字符 | 3~5：1 核心词 + 1~2 材质/结构 + 1~2 场景 |
| `machinery_industrial` | 机械 / 工业设备 | industry | 工程机械、包装、机床、激光、泵阀、输送 | 435 / 691 / 367 | 3~5：设备词 + 规格/型号 + 应用行业 |
| `electric_atv` | 电动 ATV / 代步车 | industry | 电动 ATV、UTV/沙滩车、电动滑板/代步车 | 443 / 745 / 369 | 3~5：车型 + 动力规格 + 场景/人群 |
| `general_industrial` | 通用工业品 | industry | 五金工具、配件、耗材、网袋 | 339 / 632 / 343 | 3~5：品名 + 材质/规格 + 用途 |
| `detail_html` | 详情 HTML（通用强约束） | function | 任意类目（详情页为主） | 149 / 555 / 281 | 3~5 |
| `sku_naming` | SKU 命名优化（通用） | function | 任意类目（多规格） | 149 / 558 / 466 | 3~5，含变体维度词 |
| `content_review` | 内容审查（Alibaba 合规） | function | 任意类目（上架前审查） | 137 / 612 / 281 | 3~5，禁违禁/品牌词 |

每个预设统一字段：`id/name/kind/industry/applicable_categories/aliases/title_prompt/detail_prompt/sku_prompt/keyword_prompt/keyword_strategy/review_prompt/compliance_notes`。`compliance_notes` 含 `banned_words`（28 个，覆盖 cheap/replica/fake/knockoff/best price/free shipping/联系方式/绝对化/医疗）、`notes`（中文说明）、`images`（主图 6 张/白底/1:1/无文字水印）。

**与旧默认（`config.DEFAULTS`）的关键差异**：
- 标题上限 125 → **≤128**（并要求尽量 ≤120 留余量），禁止中文/引号/emoji/绝对化；
- 详情强制 **HTML 片段 + `<img data-detail-img="N">` 原图占位**、1800~3500 字符、禁联系方式/促销/编造认证；
- 增加 Alibaba 合规审查提示词（旧默认是「速卖通」措辞）；
- 行业卖点结构（泳池：场景→参数表→结构→安装→定制；机械：定位→参数→结构→行业→服务；ATV：场景→动力参数→配置→安全→包装）。

## 3. 实测方法（可复现）

### 3.1 输入

| 商品 | 原标题 | 原详情 | 详情图 | 说明 |
|---|---|---|---|---|
| `220114598434958576` | 115 字符 | 355 字符 | 12 | 原文**只有联系方式/报价说明，几乎没有规格**（WhatsApp/邮箱/电话） |
| `220114598434958580` | 45 字符 | 2108 字符 | 21（详情上限取 12） | 原文含 fiberglass、12ft/15ft/20ft/40ft、20-300mm、30 年等真实规格 |

两商品均只读 `product_detail`，未保存。源详情都含 WhatsApp/邮箱/电话，正好用于检验「AI 是否会泄漏联系方式」。

### 3.2 对比变量

| 变体 | 提示词来源 |
|---|---|
| `old_default` | `AppConfig.DEFAULTS` 的 title/detail/sku/review 提示词（旧默认，速卖通/机械风格） |
| `new_alibaba_preset` | `AP.apply_to_cfg(cfg_in_memory, "container_pool")`（v1 预设） |

模型 `qwen-plus`（固定模型）、`temperature=0.6`、标题 `max_tokens=2048`、详情 `max_tokens=2000`。每商品各跑 标题 + 详情，共 **2 商品 × 2 变体 × 2 调用 = 8 次**（预算上限 8，恰好用完）。

### 3.3 控制与限制（诚实说明）

- 为不超预算，测试时把 `enforce_title_length` 换成本地探针：记录 AI 原始标题长度，超长才本地截断，**不再触发第二次「压缩」AI 调用**。本次 4 个标题原始长度 79~103，均未触发截断，对结果无影响。
- 违禁词检查用店小秘只读接口 `checkBannedWord`（每个变体 1 次，非 AI 调用），无命中。
- 详情全文仅有**前 400 字符摘录 + 完整结构统计**留档（受 8 次调用预算限制，未能重跑保存全文）；评分 = 标题全文 + 详情结构统计 + 摘录可读性，1~5 主观分并附理由。
- 未验证：真实保存时 `textDesc` 平台长度上限、图片上传、关键词/审查提示词的 AI 实跑（预算用满）。

### 3.4 评分标准（1~5）

5 = 可直接上架；4 = 小改即可；3 = 需人工修正；2 = 有事实性错误/审核风险；1 = 不可用。

## 4. 实测结果

### 4.1 标题（全文）

| 商品 | 变体 | 标题 | 字符/词 | 违规 | 编造 | 评分 |
|---|---|---|---|---|---|---|
| 576 | old_default | Large Prefab Container Swimming Pool with Acrylic Glass Window – Outdoor Above-Ground Pool | 90 / 13 | 无 | 无 | **4** |
| 576 | new_alibaba_preset | Customized 40ft Prefabricated Container Pool with Acrylic Window – Outdoor Above Ground Fiberglass Pool | 103 / 14 | 无 | **`40ft`、`fiberglass` 原文都没有**（fabrication_warnings 2 项） | **2** |
| 580 | old_default | Custom Fiberglass Swimming Pool Container – Durable, Weatherproof, Custom Sizes & Shapes | 88 / 12 | 无 | 无（但 `Durable/Weatherproof` 是未证实形容词；丢了原文 40ft 等规格） | **3** |
| 580 | new_alibaba_preset | Customized Fiberglass Container Pool – 40ft Outdoor Prefabricated Swimming Pool | 79 / 10 | 无 | 无（40ft/Fiberglass 原文有依据） | **5** |

标题结论：**信息充足时新预设胜**（580 标题 79 字符，核心词+规格+场景齐全、无堆砌）；**信息稀薄时新预设 v1 败**（576 标题把提示词里的示例尺寸/材质当默认值，产生事实错误）。4 个标题全部 ≤128、无中文/引号/emoji/联系方式、无本地与平台违禁词。

### 4.2 详情

| 商品 | 变体 | HTML 字符 | 纯文本 | h3 / p / table / ul / li / img | 违规 | 评分 |
|---|---|---|---|---|---|---|
| 576 | old_default | 5797 | 2395 | 5 / 15 / 1 / 1 / 5 / **12/12** | 0 | **4** |
| 576 | new_alibaba_preset | 6381 | 2719 | 5 / 15 / 1 / 1 / 5 / **12/12** | 0（但摘录首段出现原文没有的 `40-foot`） | **3** |
| 580 | old_default | 6364 | 2697 | 5 / 15 / 1 / 1 / 5 / **12/12** | 0 | **4** |
| 580 | new_alibaba_preset | 7088 | 3275 | 5 / 18 / 1 / 1 / 4 / **12/12** | 0 | **5** |

详情结论：两套提示词在**格式与合规**上打平（HTML 结构完整、图片占位 12/12、无中文/联系方式/代码块/违禁词）；新预设**内容更厚**（纯文本平均 2997 vs 2546，+18%；580 段落 18 vs 15）且参数与场景组织更接近 Alibaba B2B 详情；但 576 因原文无参数，新预设同样出现编造尺寸（摘录：*"This is a prefabricated 40-foot shipping container pool…"*）。

### 4.3 合规与清洗（两套共同表现）

| 检查项 | old_default | new_alibaba_preset |
|---|---|---|
| 中文残留 | 0/4 | 0/4 |
| 联系方式（邮箱/电话/WhatsApp 等） | 0/4（原文有也清掉了） | 0/4 |
| 引号 / emoji | 0/4 | 0/4 |
| Markdown 代码块 | 0/4 | 0/4 |
| 平台违禁词接口命中 | 0/4 | 0/4 |
| 详情图片占位保留 | 12/12 | 12/12 |
| 标题超 128 | 0/4 | 0/4 |

### 4.4 耗时与成本

| 指标 | old_default | new_alibaba_preset |
|---|---|---|
| 标题耗时（2 次） | 1.35 + 1.46 = 2.81s | 1.49 + 2.22 = 3.71s |
| 详情耗时（2 次） | 12.77 + 17.71 = 30.48s | 17.43 + 21.24 = 38.67s |
| 8 次总耗时 | — | **75.67s**（标题 6.52s / 详情 69.15s） |

- 网关价格表：`qwen-plus` `per_call=0.0`、`ratio=0.8`（文本不按次计费）。
- token 估算：输入 ≈ 2,899、输出 ≈ 3,880 → **≈ ¥0.0101**（按 qwen-plus 参考价 ¥0.0008/1K 输入 + ¥0.002/1K 输出，仅估算）。
- 网关账单 `billing_usage.total_usage` 前后差 **1.7852 units**（网关按 /100 换算 USD ≈ $0.018 ≈ ¥0.13）；该值可能滞后/含其他进程调用，仅作量级参考。相对约 1 亿余额可忽略。
- 新预设详情提示词长 772 字符（旧 282），详情生成平均 19.3s（旧 15.2s），换来更完整内容。

### 4.5 对比汇总

| 维度 | 旧默认 | 新预设 | 判定 |
|---|---|---|---|
| 标题平均分 | 3.5 | 3.5 | 持平（分布相反：580 新胜、576 新败） |
| 标题平均字符 | 89 | 91 | 均 ≤128 |
| 标题编造数 | 0/2 | **1/2** | 新预设 v1 有回归 → 已 v2 加固 |
| 详情平均分 | 4.0 | 4.0 | 持平（580 新胜、576 旧胜） |
| 详情纯文本平均 | 2546 | 2997 | 新预设 +18% |
| 详情违规数 | 0 | 0 | 持平 |
| 平均详情耗时 | 15.2s | 19.3s | 新预设慢 27% |

## 5. 发现的问题与处理（按严重度）

### P0（已处理）示例数字泄漏导致标题编造

- 现象：v1 `container_pool.title_prompt` 写了「尺寸或容量（如 15ft/20ft/40ft）」与材质枚举，576（原文无任何规格）生成的标题直接出现 `40ft` + `Fiberglass`。
- 处理（v2，`PRESET_VERSION=2026-11-22.2`）：
  1. 删除标题提示词里所有具体尺寸示例，改为「原文有具体 ft/m 尺寸才写；没有就禁止出现任何尺寸数字，用 Customized/Custom Size 代替」；材质同样改为「仅原文出现才写」。
  2. 详情提示词加「原文没有具体尺寸时禁止写任何 ft/m 数字（如 40ft）」。
  3. 新增 `fabrication_warnings(text, source_text)`：后置检测「原文没有的规格数字/材质/认证/绝对化词」，返回中文 warning 列表，供 UI 显示。
  4. 离线测试锁死该回归（`test_measured_real_regression_case` 等 7 项）。
- **注意**：8 次真实调用已在 v1 上完成，v2 只做提示词加固（未再消耗 AI 调用，遵守 ≤8 预算），因此 **v2 的效果需下一次抽样复验**（建议重点复验 576 类「无规格原文」商品）。

### P1 原文信息稀薄时缺少保守回退策略

- 建议（Lead 决定，可能改 pipeline/UI）：AI 标题生成后先跑 `fabrication_warnings`；命中 ≥1 项时，可用旧默认标题提示词重生成一次（额外 1 次调用）或标红要求人工确认。也可在 `run_alibaba_product` 里把 warning 放进 `result.warnings`。

### P1 关键词生成尚未接 AI

- `TextPipeline` 没有 `make_keywords`；当前关键词来自 `alibaba_payload.keywords_effective`（overrides > cfg.alibaba_keywords > 商品原 keywords > 标题提取）。预设里已备好 `keyword_prompt`（3~5 个，结构：核心词+属性+场景），但**本次未消耗调用实测**；Lead 若要接，需在 pipeline 增加一次 chat 调用并做 3~5 个截断。

### P2 其它

- 标题使用 en dash `–`（Alibaba 不禁止；如需统一可在 TextPipeline 输出后 `.replace("–", "-")`）。
- 详情 6.4k~7.1k 字符（v2 要求 1800~3500 字符，模型实际偏长）；需在真实保存时确认 `textDesc` 长度上限，超限可让 AI 压缩或本地截断。
- SKU 预设（≤20 字符/ASCII/每行一个）与审查预设只有离线结构测试，未实跑；首次上线建议人工抽检 5 个 SKU 列表与 5 段文本。

## 6. 上线建议

| 预设/能力 | 建议 |
|---|---|
| `container_pool`（v2）标题/详情 | **可上线为泳池类默认**；必须配 `fabrication_warnings` 后置提示，576 类无规格商品标题需人工确认 |
| `container_pool` 审查提示词 | 可上线（`review_text_prompt`），输出 `PASS` / `FAIL|原因` 一行 |
| `machinery_industrial` / `electric_atv` / `general_industrial` | 结构已验证，**先抽检 3~5 个真实商品再默认开启**（ATV 注意 street legal/DOT 等宣称） |
| `detail_html` / `sku_naming` / `content_review` | 可先作为「手动选择」预设上线，不设为默认 |
| 旧默认提示词 | 保留不动（`config.DEFAULTS` 未改），作为信息稀薄商品的保守回退候选 |

## 7. 复现与测试

```bash
cd /Users/apple/Projects/alibaba-lister
# 离线测试（29 项，无网络/无付费调用）
.venv/bin/python tests/test_alibaba_prompts.py     # 期望：PASS 29 tests
.venv/bin/python -m py_compile src/aeclone/services/alibaba_prompts.py

# 真实实测（本次为临时脚本，未纳入仓库；步骤：）
# 1) AppConfig.load()；2) make_client(cfg) 只读 2 个商品详情；
# 3) AppConfig 内存副本 = DEFAULTS 提示词 vs apply_to_cfg(container_pool)；
# 4) 每商品各 make_title + make_detail（共 8 次 qwen-plus）；
# 5) 记录长度/中文/引号/联系方式/违禁词/图片占位/耗时，结果写 docs/ai-prompt-test.json。
```

> 真实实测为避免额外改动，脚本只放在 `/tmp`（本任务写入范围限定 4 个文件）。如需长期回归，建议另立任务把脚本收进 `tools/` 并支持 `--dry-run`。

## 8. 安全边界声明

- 未保存/发布/删除任何商品，未写店铺；`save_product` 在测试包装客户端里被设为抛错哨兵。
- 未调用任何图片/视觉模型；仅文本 `qwen-plus` 8 次。
- Cookie 只用于只读商品详情/违禁词检查；未打印、未落盘；`api_key` 未出现在本文档与 JSON。
- config 只在内存 `AppConfig` 副本上套用预设，未调用 `save()`，真实配置文件未被修改。

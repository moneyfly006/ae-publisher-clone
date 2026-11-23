# AuroraList 全平台最终验收报告（2026-11-22 深夜版）

> 目标：全平台设计完成后，本地构建 + 逐平台/逐页面/逐按钮验收；四平台 AI 文案、图片、视频真实测试；
> 重点修复「类目属性值缺失」并用多个真实商品回归。
> 结论口径：区分「真实接口/真实付费调用」与「离线沙箱」，不夸大。

---

## 0. 结论速览

| 项目 | 结果 | 证据 |
|---|---|---|
| macOS 本地构建 | **成功**：`dist/AuroraList/AuroraList`（13,608,816 B，目录 109 MB）+ `dist/AuroraList.app` | `/tmp/build_macos.log` |
| 打包版自检 `--self-check` | **exit 0**（环境正常，可启动） | 构建日志末段 |
| 打包版 `--verify-local` | **PASS**：`concurrent_previews=24, collage, watermark, sku_identity` | 本次输出 |
| 四平台页面/导航/Tab | **PASS，0 problems**：Alibaba 10 页 / AliExpress 12 页 / Amazon 4 页 / TikTok 4 页 | `docs/platform-verification.json` |
| 全平台逐按钮回调审计 | **21 页 / 627 回调，ERROR 0**（OK 401 / DEFERRED 203 / NATIVE 8 / EXTERNAL 15） | `docs/ui-button-audit.json` |
| 全平台开关/下拉审计 | **413 项，0 ERROR**（CONFIG_SAVED 300 / NO_CONFIG_CHANGE 113） | `docs/ui-options-verification.json` |
| 离线回归 | **941 tests OK** | `tools/test_offline.py` |
| 全量回归 | **79/79 PASS** | `tools/verify_regressions.py` |
| TikTok 套件 | **393/393 OK**（client 70 / payload 69 / prompts 57 / pipeline 77 / contract 55 / attributes 48 / ui_page 17） | tests/test_tiktok_*.py |
| AI 文案（四平台真实付费） | Alibaba 评分 4.75；AliExpress 标题+详情 3208 字符；Amazon 标题/5 卖点/描述/SearchTerms；TikTok 标题/5 点/描述/9 关键词 | `docs/all-platforms-ai-text-verification.json`、`docs/tiktok-ai-verification.json` |
| 图片链路（真实商品图） | 方图补白 / 文字水印 / 图片水印 / 四合一 / 1:1+3:4 营销图 / 压缩 **全部成功**；尺寸校验 7 项达标 | `docs/all-platforms-media-verification-2.json` |
| AI 图片（真实付费） | 白底 1024×1024 成功（23.1s） | `docs/design/test-assets/ai-white-bg-r2.png` |
| 视频生成（真实 ffmpeg） | 4 图轮播 1080×1080 / 6.5s / h264 / 30fps，ffprobe 7 项 PASS | `docs/design/test-assets/carousel-r2.mp4` |
| 类目属性补全 | Amazon 14/14 商品 4 项必填 100%；Alibaba 325 条 value_id 解析、0 缺失；AliExpress 必填覆盖 37.7%→98.1%；TikTok 必填可操作率 95.9% | 四份 `*-attributes-verification.json` |
| 真实写入 | **本轮 0 次商品写入**（属性/媒体验证只读；AI/图片为付费生成调用） | 各证据 JSON `write_calls: []` |

---

## 1. 本次修复的缺陷（都会直接影响用户）

### 1.1 类目属性补全（核心诉求）
| 平台 | 修复前 | 修复后 |
|---|---|---|
| **Amazon** | 12-16 个采集箱草稿全部缺 `model_number/part_number/model_name/manufacturer_contact_information`；只在发布(op=2)时补 | `build_amazon_payload`/`prepare_save` 在 **op=1/3/2 都补齐**（只补缺失、不覆盖已有；`amazon_autofill_attrs` 默认开）；14/14 商品 4 项 100% 补齐 |
| **Alibaba** | 采集属性 426 条「有值名无 value_id」，旧校验假通过 | 新增 `resolve_attr_value`（中英文/别名/大小写按平台枚举解析）→ 325 条解析、0 缺失/0 错配；`validate_alibaba_save` 新增 4 类可读阻断；22/33 商品暴露真实脏值（不编造） |
| **AliExpress/SMT** | 203 草稿中 17 个无类目、23 个必填无值、43 个 id/名称缺失；模板属性未套用 | 新增 `services/smt_attributes.py` + Publisher 两条保存链路接入（只补缺失、`smt_attr_autofill` 默认开）→ 必填覆盖 80/212（37.7%）→ **208/212（98.1%）**，仅 4 个商品缺 Drill Type 需人工 |
| **TikTok** | 页面属性表读原始字段（`isMandatory` 读成 `required` → 必填数恒 0；`values` 是 JSON 字符串 → 候选恒空）；无法补全 | 新增 `load_category_attributes`/`suggest_attributes`/`merge_product_attributes`；UI 改用规范化加载 + 「补全属性（规则）」/「AI 补全属性」按钮；6 站点×48 叶子实测必填可操作率 **95.9%** |

### 1.2 图片/视频
- **文字水印白底不可见 + 字体过小**：旧实现固定白字、macOS 无 `arial.ttf` 回退 10px 位图字体。
  修复：白字 + 黑描边、按图片宽度自适应字号（大图 90px）、系统字体候选（Arial Unicode/TTF），
  白底/深底/2000px 大图均可见（新增 `tests/test_image_ops_watermark.py` 7 用例）。
- **3:4 营销图 750×1000 低于平台 ≥800 主图门槛**：生成尺寸改为 **800×1067**（1:1 仍 800×800），
  Publisher 两处链路同步；`test_api_save` 的 `_market_ok` 规则不变。
- 视频链路真实可用（ffmpeg，1080×1080/6.5s/h264）。

### 1.3 TikTok 刊登页结构对齐速卖通（2026-11-22 用户反馈）
- 原 14 页是「①-⑧ 编号卡片一次性纵向堆叠」的长页面，与速卖通等页面的
  `page_header + workspace_tabs + card` 结构不一致；已重构为**五个分栏 Tab**：
  连接与商品 / 类目与属性 / 文案与图片 / 价格与物流 / 保存与诊断，
  卡片标题去掉圈号，交互/回调/写安全完全保留（页面 14 回调 53 个，0 ERROR）。

### 1.4 其他
- 打包预设**隐私/回归**加固：`tools/export_preset.py` 按键名规则排除 `*_cookies/*_shop_id/*token/*secret/*password/active_platform/alibaba_price_type_explicit`；
  `tests/test_preset.py` 16 项断言（含「预设值不得含 Cookie 内容」）。本次构建产出的预设 309 键、无敏感键。
- Alibaba 多选属性拆行后被去重逻辑误删（只剩第一行）：去重改为「多选按 (属性, value_id) 保留多行、属性名优先匹配」，多选 3 值现在 3 行完整保留。
- TikTok 属性必填缺失现在进入 `issues`（保存前阻断）而不是静默丢失。

---

## 2. 逐平台验收明细

### 2.1 页面 / 按钮（离线沙箱，真实 UI 控件树）
- `tools/verify_all_platforms.py`：逐平台构建所有页面、切 Tab、点左侧导航、平台互切、主题切换、侧栏守卫 → 4 平台 0 problems。
- `tools/button_audit.py`：遍历每个页面所有 `on_click/on_change/on_submit` → **627 回调 0 ERROR**；
  其中 TikTok 4 页 114 回调、页面 14 53 回调 0 错。
- `tools/verify_ui_options.py`：413 项开关/下拉，保存正常（300）或无需变更（113），0 error。
- 截图：`docs/design/ui-verify/all-platforms/` 34 张（30 个导航页 + Alibaba 刊登页 4 个真实 Tab），本地 CJK 字体无乱码。

### 2.2 AI 文案（真实付费 API）
- Alibaba：`run_alibaba_product` dry-run 全链路，标题/详情/关键词，评分 4.75（发布线 4.5）。
- AliExpress：`TextPipeline` 真实标题 + 详情 3208 字符。
- Amazon：`ai_generate_all` 标题/5 卖点/描述(1657 字符)/Search Terms。
- TikTok：标题/5 点/描述(1815 字符)/9 关键词；另有 `docs/tiktok-ai-verification.json`（GB/DE 标题、GB 描述、US 五点/关键词，qwen-plus）。
- 成本控制：真实调用集中在抽样验证，其余 mock。

### 2.3 图片 / 视频
- 4 张真实商品图（alicdn/aliexpress-media）下载成功；本地产物 7 项 + 负样本 600×450 正确拒绝。
- AI 图 2 次真实调用（白底成功；精细编辑同一输入图返回白底副本——图像网关按输入缓存，已在报告标注，App 以白底产物为输入不受影响）。
- 视频：`carousel-r2.mp4` 4 图轮播，ffprobe 7 项 PASS。

---

## 3. 平台与账号事实（如实）

| 平台 | 数据 | 可写验证 |
|---|---|---|
| Amazon | 12 草稿（14 行）；QINGHEMAOYI/店铺 9408005 | 本轮只读（未写） |
| Alibaba | 24 草稿 / 4 待发布 / 5198 在线；店铺 9466975 | 本轮只读（未写） |
| AliExpress | 203 草稿 / 113 待编辑 / 3951 在线；本机浏览器账号 wangmengruie | 本轮只读（未写） |
| TikTok | **账号未绑定 TikTok 店铺，采集箱 0** | 类目/属性/站点真实只读；商品写回环无法验证 |

---

## 4. 剩余风险 / 限制（P2）

1. **TikTok 无店铺**：`tiktokSave.txt` 元素形状来自前端逆向，未经真实保存回读；绑定店铺后需跑
   `tools/verify_tiktok.py --write-roundtrip --confirm-write --product-id <id>` 做一次真实回环。
2. AliExpress 4 个 Water Well 商品仍缺必填 `Drill Type`（AI 限流 2 次未覆盖，AI 已验证可补，需人工/批量再跑）。
3. Alibaba 22/33 商品仍有真实脏值（Type=Pool、Origin=Henan、缺 Model Number 等），校验现在会明确阻断并给候选，不自动编造。
4. AI 精细编辑同输入缓存（网关行为）；文字水印已修，图片水印浅色 logo 仍建议深色底或描边 logo。
5. 真实写入回环本轮未执行（用户要求先测功能和属性；写操作需另行确认）。
6. 工作区存在并行开发进程，审计数字为最终稳定状态的快照（见各 JSON 的 generated_at）。

---

## 5. 复现命令

```bash
# 构建（本机已跑通）
AURORA_HARDEN=0 ./tools/build_macos.sh
dist/AuroraList/AuroraList --self-check
dist/AuroraList/AuroraList --verify-local

# 全平台页面/按钮/选项
AURORA_TEST=1 .venv/bin/python tools/verify_all_platforms.py
AURORA_TEST=1 .venv/bin/python tools/button_audit.py --json docs/ui-button-audit.json
AURORA_TEST=1 .venv/bin/python tools/verify_ui_options.py

# 测试
.venv/bin/python tools/test_offline.py
.venv/bin/python tools/verify_regressions.py
for t in client payload prompts pipeline contract attributes; do .venv/bin/python tests/test_tiktok_$t.py; done
AURORA_TEST=1 .venv/bin/python tests/test_tiktok_ui_page.py
.venv/bin/python tests/test_amazon_attributes.py
.venv/bin/python tests/test_alibaba_attributes.py
.venv/bin/python tests/test_smt_attributes.py
.venv/bin/python tests/test_smt_attr_integration.py
.venv/bin/python tests/test_image_ops_watermark.py

# 属性真实只读复核（需 .tmp-probe/cookies.txt；AliExpress 用本机浏览器 Cookie）
.venv/bin/python tools/verify_tiktok.py
python3 - <<'PY'
# 见 docs/*-attributes-verification.json 的生成脚本 / 各 teammate 报告
PY
```

---

## 6. 证据文件索引

| 文件 | 内容 |
|---|---|
| `docs/amazon-attributes-verification.json` | 14 商品必填 4 项 100% 补齐（只读） |
| `docs/alibaba-attributes-verification.json` | 33 商品/9 类目 value_id 解析与阻断统计 |
| `docs/aliexpress-attributes-verification.json` | 60 商品缺失分类/可补比例/根因 |
| `docs/tiktok-attributes-verification.json` | 6 站点×48 叶子/837 属性/必填可操作率 |
| `docs/all-platforms-ai-text-verification.json` | 四平台真实 AI 文案 |
| `docs/tiktok-ai-verification.json` | TikTok 真实 AI（GB/DE/US） |
| `docs/all-platforms-media-verification-2.json` | 图片/视频/AI 图真实产物 + hash |
| `docs/platform-verification.json` | 四平台页面/导航/Tab |
| `docs/ui-button-audit.json` | 21 页 / 627 回调 0 ERROR（TikTok 页面 14：53） |
| `docs/ui-options-verification.json` | 413 项开关/下拉 0 ERROR |
| `docs/regression-verification.json` | 79/79 回归 |
| `dist/AuroraList/AuroraList` | 构建产物（13,608,816 B，sha256 a973612a…） |


---

## 8. TikTok 左侧导航按功能拆页（2026-11-22 用户反馈 2）

- 原结构：TikTok 只有 0/14/10/11 四页，所有刊登功能堆在 14 号「TikTok 刊登」一个页面里。
- 现结构（与速卖通/Amazon 相同的信息架构，参考 `platforms.py`）：
  - 工作空间：概览
  - 商品准备：**连接与店铺（22）/ 采集箱商品（23）/ 类目与属性（24）/ 价格·库存·物流（25）**
  - 内容处理：**文案与 AI（26）/ 图片与视频（27）**
  - 发布与运行：**保存与发布（28）** / 任务与日志（10）
  - 工具与设置：设置（11）
  - 14 号完整工作台保留为 **内部隐藏路由**（导航不显示，旧深链/测试仍可用）。
- 实现方式：`App.TIKTOK_SECTIONS` + `_p_tiktok_section()` 薄封装（每页只渲染对应卡片），
  与 Amazon 的 15–21 功能页同一套机制；导航搜索别名按页配置（采集箱→23、类目→24、
  价格→25、保存→28…）。
- 验证：`test_tiktok_nav_split_by_function`（10 个导航页逐个点击落点 + 每页只出现本页卡片）、
  `verify_tiktok_ui` 新增「导航拆分 10 页 / 22 页=连接与店铺 / 28 页=保存与诊断」；
  `verify_all_platforms`：TikTok pages=11（10 可见 + 1 隐藏）problems=0。
- 截图：`docs/design/tiktok-nav-split-conn.png`（连接页）、`docs/design/tiktok-nav-split-category.png`（类目页），
  全量：`docs/design/ui-verify/all-platforms/tiktok-2*.png`。


### 8.1 拆页后最终复跑（2026-11-22 13:40+）
- 离线回归 **960 tests OK**；全量回归 **81/81 PASS**
- `verify_all_platforms`：Alibaba 10 / 速卖通 12 / Amazon 11 / **TikTok 11（10 可见 + 1 隐藏完整页）**，problems=0
- 按钮审计：**33 页 / 629 回调 / ERROR 0**（新增 TikTok 22–28 功能页）
- 选项遍历：**493 项**（CONFIG_SAVED 368 / NO_CHANGE 125），0 error
- `verify_tiktok_ui`：**14/14 PASS**，含「导航拆分 10 页 / 22 页=连接与店铺 / 28 页=保存与诊断」
- TikTok UI 测试 **18/18**（新增 `test_tiktok_nav_split_by_function`）
- 重新构建：**exit 0**，`dist/AuroraList/AuroraList` 13,633,472 B，sha256 `4260ae84b3e86bd7…`；
  `--self-check` exit 0；`--verify-local` PASS

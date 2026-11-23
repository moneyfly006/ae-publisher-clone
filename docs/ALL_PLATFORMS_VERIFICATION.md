# 全平台验证报告（2026-11-22）

## 一、平台覆盖

| 平台 | 页面数 | 验证结果 |
|---|---|---|
| Alibaba 国际站 | 10 | ✅ |
| 速卖通 AliExpress | 12 | ✅ |
| Amazon 亚马逊 | 4 | ✅ |
| TikTok Shop | 4 | ✅ |
| Shopee | 未开放（平台页显示暂不支持） | — |

## 二、页面 / 导航 / Tab / 按钮

- `tools/verify_all_platforms.py`：逐平台构建全部页面、切换每个 Tab、点击左侧全部导航按钮、
  平台互相切换、主题切换、侧栏「启动任务」守卫。
  - 结果：**4 个平台全部 PASS（0 problems）** → `docs/platform-verification.json`。
- `tools/button_audit.py`（按平台页面）：21 页 / 590 个回调，**0 ERROR**
  → `docs/ui-button-audit.json`。
- `tools/verify_ui_options.py`（所有平台开关/下拉）：281 项配置保存、115 项无需变更。
- 所有平台页面构建：Alibaba 10、AliExpress 12、Amazon 4、TikTok 4，均可打开。

## 三、AI 文案（真实 API）

`docs/all-platforms-ai-text-verification.json`：

| 平台 | 结果 |
|---|---|
| Alibaba | dry-run AI 生成成功（标题/关键词/详情），评分 4.75，`ai_used=true` |
| AliExpress | TextPipeline 真实生成标题 + 详情（3208 字符） |
| Amazon | `ai_generate_all` 生成标题 / 5 条卖点 / 描述 / Search Terms |
| TikTok | `ai_generate_all` 生成标题 / 5 条卖点 / 描述 / 9 个关键词 |

均调用真实付费 API，费用远低于余额。

## 四、图片与视频

`docs/all-platforms-media-verification.json`：

- 本地图片处理：方图补白、水印、四合一、营销图 ✅（4 个文件生成）。
- AI 图像编辑（白底）：真实调用成功，输出 `docs/design/test-assets/ai-white-bg.png`。
- 视频生成：ffmpeg 轮播合成成功，输出 `docs/design/test-assets/carousel-test.mp4`（34 KB）。
- 图片/视频为四平台共用服务（Alibaba/AliExpress 的图片页/工作台、Amazon/TikTok 页面的图片与视频字段）。

## 五、本地构建

- `AURORA_HARDEN=0 ./tools/build_macos.sh`
- 打包后 `--self-check`：退出码 0
- 产物：`dist/AuroraList.app`（v1.0.6）

## 六、已知问题（并行开发影响）

- Amazon/TikTok 的旧 UI 测试仍按「检查模式/预演」旧文案断言，与当前页面（检测连接、
  发布前检查、直接保存）不一致；这些是测试未同步，不是页面无法打开。页面/按钮/选项审计均为 0 ERROR。
- Amazon/TikTok 页面自带预演流程仍存在（与 Alibaba 已删除预演不同），待确认后统一。

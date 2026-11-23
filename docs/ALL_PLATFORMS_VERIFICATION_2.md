# 全平台真实媒体验证 + UI 审计报告（第 2 轮）

生成时间：2026-11-22T04:17:19+0800　|　代码工作树：`f394c4f1749b`（改动文件 149 个）

> 本报告把「真实付费/真实联网」与「离线沙箱」两种口径分开标注。页面能打开、按钮不报错属于离线结构验证，不等于平台接口写成功；图片/视频/AI 属于真实执行，产物都在 `docs/design/test-assets/`。

## 0. 结论摘要

| 项目 | 结果 | 口径 |
|---|---|---|
| 图片链路（方图补白/水印/四合一/营销图/压缩/尺寸校验） | PASS | 真实 Pillow 处理 |
| AI 图像（白底 + 精细编辑） | PASS | 真实付费网关 |
| 轮播视频（4 图 / ffprobe） | PASS | 真实 ffmpeg |
| 逐平台页面/Tab/导航 | PASS（0 problems） | 离线沙箱 |
| 逐按钮回调审计 | 627 个回调 / 0 ERROR | 离线沙箱 |
| 开关与下拉选项 | 413 项 / 0 ERROR | 离线沙箱 |
| 逐页截图 | 34/34 | 离线沙箱渲染 |
| 阶段2 本地构建 | PENDING（等 Lead「构建」指令） | 真实构建 |

## 1. 图片链路（真实）

### 1.1 真实商品图输入（公开 CDN 下载，非 fixture）

| # | 来源 URL | 文件 | 尺寸 | 字节 | 耗时(s) | sha256(16) |
|---|---|---|---|---|---|---|
| source-1 | https://sc04.alicdn.com/kf/H054956b8d48a466a8c136c7043fac5b8K.png | docs/design/test-assets/src-1.png | 729x642 | 275297 | 0.523 | 269190f1bfe9aa98 |
| source-2 | https://sc04.alicdn.com/kf/H38c58f3593c14ddea6ad0bcf645fdb6ab.jpg | docs/design/test-assets/src-2.jpg | 1000x1000 | 268048 | 0.806 | c163769bd5e31dd0 |
| source-3 | https://ae-pic-a1.aliexpress-media.com/kf/Sa1ede3d45c5443819bd619c4244d96f7L.jpg | docs/design/test-assets/src-3.jpg | 1024x1024 | 56834 | 0.243 | 1fd21cc83e827b59 |
| source-4 | https://ae-pic-a1.aliexpress-media.com/kf/S02b17b70454b4bba8cb7475581ac98ceI.jpg | docs/design/test-assets/src-4.jpg | 1500x1348 | 188662 | 0.141 | 2131bd478872340a |

### 1.2 本地处理产物

| 步骤 | 产物 | 尺寸 | 字节 | 耗时(s) | sha256(16) | 结果 | 说明 |
|---|---|---|---|---|---|---|---|
| local-square | docs/design/test-assets/local-square.png | 1024x1024 | 283505 | 0.022 | c23676eebee20d31 | OK |  |
| local-watermark-text | docs/design/test-assets/local-watermark-text.png | 1500x1348 | 2164734 | 0.109 | fea21a3ec2d07d50 | OK |  |
| local-watermark-image | docs/design/test-assets/local-watermark-image.png | 1024x1024 | 293090 | 0.027 | 02618a4d97daf62b | OK |  |
| watermark_contrast_probe | docs/design/test-assets/watermark-contrast-probe.png | 1024x1024 | 291653 | 0.047 | 62751291661f154a | OK | 白色文字水印叠加在白色补边方图上时像素零变化（白上叠白），像素差 bbox=None；因此文字水印只适合有背景的照片，白底主图应使用图片水印（本轮的 local-watermark-image 已验证可见）。 |
| local-four | docs/design/test-assets/local-four.png | 1200x1200 | 940596 | 0.103 | 6ecae4ca192d82b4 | OK |  |
| local-marketing-1x1 | docs/design/test-assets/local-marketing-1x1.png | 800x800 | 336678 | 0.028 | 6d397f1740f0ad59 | OK |  |
| local-marketing-3x4 | docs/design/test-assets/local-marketing-3x4.png | 750x1000 | 428935 | 0.032 | 694ecf47c425b59d | OK |  |
| local-compress | docs/design/test-assets/local-compress.jpg | 1500x1348 | 365314 | 0.027 | 44d621dac8024c42 | OK |  |

### 1.3 尺寸校验（image_validation）

| 文件 | 实测 | ≥800 门槛 | 结果 | 豁免原因 |
|---|---|---|---|---|
| docs/design/test-assets/local-square.png | 1024x1024 | 适用 | PASS | — |
| docs/design/test-assets/local-watermark-text.png | 1500x1348 | 适用 | PASS | — |
| docs/design/test-assets/local-watermark-image.png | 1024x1024 | 适用 | PASS | — |
| docs/design/test-assets/watermark-contrast-probe.png | 1024x1024 | 不适用 | PASS | 水印对比度探针，用于证明白字水印在白底图上不可见 |
| docs/design/test-assets/local-four.png | 1200x1200 | 适用 | PASS | — |
| docs/design/test-assets/local-marketing-1x1.png | 800x800 | 适用 | PASS | — |
| docs/design/test-assets/local-marketing-3x4.png | 750x1000 低于 800x800 | 不适用 | 低于门槛（符合设计） | 3:4 营销图按 750×1000 出图（店小秘口径），非平台主图，不适用 ≥800×800 |
| docs/design/test-assets/local-compress.jpg | 1500x1348 | 适用 | PASS | — |

- 负样本：`docs/design/test-assets/invalid-600x450.png` → 600x450 低于 800x800；按预期被拒（True）。
- 远程 URL 实测：https://ae-pic-a1.aliexpress-media.com/kf/S02b17b70454b4bba8cb7475581ac98ceI.jpg → 1500x1348（0.43s，真实联网）。

## 2. AI 图像（真实付费调用）

| 步骤 | 产物 | 尺寸 | 字节 | 耗时(s) | sha256(16) | 模型 | 结果 | 错误 |
|---|---|---|---|---|---|---|---|---|
| 白底 | docs/design/test-assets/ai-white-bg-r2.png | 1024x1024 | 880739 | 3.049 | 9734485bd1b3dc7e | qwen-image-2.1-turbo | OK |  |
| 精细编辑 | docs/design/test-assets/ai-fine-edit-r2.png | 1024x1024 | 845515 | 14.259 | 53935226f4049405 | qwen-image-2.1-turbo | OK |  |

- 真实付费调用：**2 次**（白底 1 + 精细编辑 1）；本次生成报告时新产生 2 次。
- 网关余额（used / remain）：140.0404 / 99999859.9596；计量可能延迟，不作为扣费凭证。
- 图像网关返回地址：`http://132.226.1.44:8099/imgres/63c33401e2084267ab74d02d498638d4.png`

> **实测发现（同输入换提示词）**：同一张输入图只更换提示词时，网关约 2.5s 返回与白底图字节完全一致的结果（按图缓存/未真正重新生成）。因此精细编辑必须换成不同输入图（App 内以白底产物为输入，本轮已按该链路重测）。

> 证据：白底与精细编辑输出 sha256 相同（`9734485bd1b3dc7e`），精细编辑耗时仅 2.527s。

## 3. 视频（真实 ffmpeg + ffprobe）

| 项 | 值 |
|---|---|
| 产物 | docs/design/test-assets/carousel-r2.mp4 |
| 图片数 | 4 |
| 时长 | 6.5s（预期 6.5s） |
| 分辨率 | 1080x1080 |
| 编码 / 像素格式 / 帧率 | h264 / yuv420p / 30.0fps |
| 大小 | 1177183 B |
| sha256 | 589f81baab6318a07ac08bbd05cdef68a4e3de9b8875ebf0d65a48147259ec56 |
| ffmpeg | ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers |
| 耗时 | 1.086s |

校验：has_video_stream=PASS；resolution_1080x1080=PASS；duration_expected=PASS；size_positive=PASS；size_under_platform_limit_100mb=PASS；fps_30=PASS；pix_fmt_yuv420p=PASS

## 4. 全平台 UI 审计（离线沙箱）

> 三套审计都在隔离沙箱内运行（不联网、不读写客户配置、不调用平台写接口）。审计 JSON 由本任务与并行 agent 共同刷新，本节数字为报告生成时刻的快照，文件 sha256/修改时间见 `all-platforms-media-verification-2.json` 的 `environment.audit_files`。

### 4.1 页面 / Tab / 导航 / 平台切换（tools/verify_all_platforms.py）

| 平台 | 页面数 | 控件数 | 结果 | 问题 |
|---|---|---|---|---|
| alibaba | 10 | 1073 | PASS | — |
| aliexpress | 12 | 1561 | PASS | — |
| amazon | 4 | 666 | PASS | — |
| tiktok | 4 | 637 | PASS | — |

### 4.2 逐按钮回调（tools/button_audit.py）

总计：**21 页 / 627 个回调**，状态分布 `{"OK": 401, "DEFERRED": 203, "NATIVE": 8, "EXTERNAL": 15, "ERROR": 0}`，ERROR **0**。

| 平台 | 审计页数 | 回调数 | 状态分布 |
|---|---|---|---|
| alibaba | 10 | 311 | {"DEFERRED": 130, "OK": 166, "NATIVE": 8, "EXTERNAL": 7} |
| aliexpress | 3 | 61 | {"DEFERRED": 3, "OK": 58} |
| amazon | 4 | 141 | {"OK": 102, "DEFERRED": 35, "EXTERNAL": 4} |
| tiktok | 4 | 114 | {"OK": 75, "DEFERRED": 35, "EXTERNAL": 4} |

> AliExpress 只审 3 个平台独占页（类目/价格/变种），其余页面与 Alibaba 共用同一实现，已在 Alibaba 分支覆盖；这是 `button_audit.py` 的既有设计。

### 4.3 开关 / 下拉选项（tools/verify_ui_options.py）

总计 **413** 项，`{"CONFIG_SAVED": 300, "NO_CONFIG_CHANGE": 113}`，ERROR **0**。

### 4.4 逐页截图

目录：`docs/design/ui-verify/all-platforms`；共 34 张（30 个平台页面 + Alibaba 刊登页 4 个附加 Tab）。Tab 截图通过 `AURORA_TAB` 真实切换分栏，5 张刊登页截图哈希互不相同。

| 平台 | 截图数 | 成功 |
|---|---|---|
| alibaba | 14 | 14 |
| aliexpress | 12 | 12 |
| amazon | 4 | 4 |
| tiktok | 4 | 4 |

| 平台 | 页 | 文件（不含 .png） | 字节 | sha256(16) | 结果 |
|---|---|---|---|---|---|
| alibaba | 0 | alibaba-0-overview | 185610 | bd050eae2b12f878 | OK |
| alibaba | 1 | alibaba-1-alibaba-connect | 153274 | eda5f53fc377ace4 | OK |
| alibaba | 12 | alibaba-12-alibaba-listing | 187459 | ebeb0955faf14b94 | OK |
| alibaba | 8 | alibaba-8-publish-queue | 116421 | 4fa04d1739512539 | OK |
| alibaba | 3 | alibaba-3-ai-text | 108213 | 5d4e42145dd3933e | OK |
| alibaba | 5 | alibaba-5-ai-image | 169210 | a884d2590401987d | OK |
| alibaba | 4 | alibaba-4-content-review | 116857 | 63c1049837befb20 | OK |
| alibaba | 10 | alibaba-10-tasks-logs | 153268 | bc6684d390798c31 | OK |
| alibaba | 9 | alibaba-9-image-workbench | 94357 | 3965dffb0354030a | OK |
| alibaba | 11 | alibaba-11-settings | 177968 | 6c28d7f757600b9f | OK |
| aliexpress | 0 | aliexpress-0-overview | 203337 | 519b9f436d349f35 | OK |
| aliexpress | 1 | aliexpress-1-store-templates | 157314 | f1dc8ffaa6747454 | OK |
| aliexpress | 2 | aliexpress-2-category-attributes | 118258 | a1f49f6b8d14418d | OK |
| aliexpress | 6 | aliexpress-6-pricing | 146047 | 1454782d259973a6 | OK |
| aliexpress | 7 | aliexpress-7-variants-inventory | 119499 | 1529db7244debed2 | OK |
| aliexpress | 3 | aliexpress-3-ai-text | 155800 | e8765b19c9226298 | OK |
| aliexpress | 5 | aliexpress-5-ai-image | 173796 | a8425d25c840f5fb | OK |
| aliexpress | 4 | aliexpress-4-content-review | 123244 | 9913284eb9312991 | OK |
| aliexpress | 8 | aliexpress-8-publish-queue | 131331 | 4a4fc77552541995 | OK |
| aliexpress | 10 | aliexpress-10-tasks-logs | 160999 | 779ddd373855263c | OK |
| aliexpress | 9 | aliexpress-9-image-workbench | 142534 | 7d3b6343d55f8e4a | OK |
| aliexpress | 11 | aliexpress-11-settings | 185039 | c10b200d5dbfffbe | OK |
| amazon | 0 | amazon-0-overview | 158446 | 032a48bc14c57ba3 | OK |
| amazon | 13 | amazon-13-amazon-listing | 277207 | 8aa0369ede1abd07 | OK |
| amazon | 10 | amazon-10-tasks-logs | 147035 | bb6127ad21b062f6 | OK |
| amazon | 11 | amazon-11-settings | 164502 | 60ac0fe446d30d5e | OK |
| tiktok | 0 | tiktok-0-overview | 171680 | 68de98a66404d8fc | OK |
| tiktok | 14 | tiktok-14-tiktok-listing | 182311 | 0239eb6b3e8a8781 | OK |
| tiktok | 10 | tiktok-10-tasks-logs | 144630 | 6abb4a4a216e877a | OK |
| tiktok | 11 | tiktok-11-settings | 164985 | c3191636634bd4ca | OK |
| alibaba | 12 | alibaba-12-alibaba-listing-tab2-category | 136723 | 4f6afa6f336cc6ab | OK |
| alibaba | 12 | alibaba-12-alibaba-listing-tab3-presets | 160916 | 6368ce6e84269874 | OK |
| alibaba | 12 | alibaba-12-alibaba-listing-tab4-price-trade | 162006 | 90469a3b5044b773 | OK |
| alibaba | 12 | alibaba-12-alibaba-listing-tab5-save | 176994 | 431cdf837a102595 | OK |

## 5. 与上一轮（2026-11-22 03:12）的差异

- 图片链路输入从本地 fixture 换成 **4 张真实商品图 URL**（alicdn / aliexpress-media），并新增负样本与远程 URL 校验、压缩链路。
- 视频从 34 KB 的测试片升级为 **4 图 1080×1080 / 6.5s / 1.17 MB** 真实轮播，并用 ffprobe 逐项校验时长、分辨率、帧率、像素格式。
- 截图覆盖从 21 张扩到 **34 张**（4 平台全部导航页 + Alibaba 刊登页 5 个 Tab），文件名统一为 `<platform>-<page>-<label>.png`。
- 三套 UI 审计在本轮工作树（HEAD 变化中）重新跑过，按钮/选项 0 ERROR。

## 6. 已知问题与风险

1. **AI 精细编辑同输入缓存**：同一张输入图只换提示词，网关约 2.5s 返回与白底图字节完全相同的结果。App 内 `ImagePipeline` 以白底产物为编辑输入（字节不同），正常不受影响；但若用户对同一张原图连续做两次不同编辑，第二次可能拿到缓存图。本轮因付费调用审批被自动拒绝，未能用「不同输入图」复测，建议真机再验一次。
2. **文字水印白上叠白**：`image_ops.add_watermark` 的文字固定白色；打在白色补边方图上像素零变化（本轮已用 `watermark-contrast-probe.png` 证明）。白底主图应改用图片水印；另外 macOS 上没有 `arial.ttf`，文字会退回 10px 位图字体（很小）。
3. **3:4 营销图 750×1000**：低于平台主图 ≥800×800 门槛，但这是店小秘 3:4 规格，不应作为主图使用；报告中按“豁免项”记录，避免误判为失败。
4. **并行开发**：本轮期间仓库 HEAD 从 `42d4802` 变为 `f394c4f`，工作树有 149 个改动文件；审计与截图对应本轮执行时的工作树，数字可能随其它 agent 继续改代码而变化。
5. **离线口径**：页面/按钮/选项审计是隔离沙箱，外部网络、后台线程、系统文件对话框分别计数，不代表真实平台接口成功；真实写操作仍需店铺凭证与人工确认。

## 7. 阶段 2（待 Lead 指令）

- 收到 Lead 的「构建」后执行：`AURORA_HARDEN=0 ./tools/build_macos.sh`；
- 校验 `dist/AuroraList.app --self-check` 退出码 0，记录大小/版本/哈希；
- 能启动则补启动截图与本次报告的第 2 轮构建段落。

## 8. 产物清单

- 媒体产物：`docs/design/test-assets/`（源图、方图、水印、四合一、营销图、压缩、AI 白底/精细编辑、轮播视频与抽帧）
- 截图：`docs/design/ui-verify/all-platforms/`
- 证据 JSON：`docs/all-platforms-media-verification-2.json`、`docs/platform-verification.json`、`docs/ui-button-audit.json`、`docs/ui-options-verification.json`
- 本轮原始记录：`docs/design/test-assets/run-media-2.json`、`run-video-2.json`、`run-shots-2.json`（含 attempt1 AI 原始记录）

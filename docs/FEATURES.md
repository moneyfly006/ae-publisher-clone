# 功能清单（基于 67 个模块 / 2067 个函数逆向规格）

> 来源：`ae-recon/inventory.json` 与 67 份模块明细（含 docstring）。
> 本清单用于重构版的功能对齐，不涉及原程序代码。

## 一、功能域总览

| # | 功能域 | 模块数 | 核心模块 |
|---|---|---|---|
| 1 | 授权与许可 | 4 | local_license, license_verify, shop_license, web_server |
| 2 | 实例与启动 | 6 | instances, startup_service, splash_screen, manager, paths, context |
| 3 | 浏览器接入 | 8 | bit_browser, zhanfu_browser, edge_browser, browser_factory, browser_connect_dialog, playwright_wrapper, cookie_cache, proxy_config |
| 4 | 店小秘对接 | 6 | dianxiaomi, product_picker, product_templates, data_extractor, html_to_json_converter, country_model |
| 5 | 产品编辑主流程 | 2 | product_edit_service, product_model |
| 6 | SKU 处理 | 6 | name_value_format_sku_mapper, sku_base_mapper, standard_format_sku_mapper, sku_attribute_mapper, sku_consistency, sku_count_limiter |
| 7 | AI 能力 | 4 | ai_utils, translation_service, ai_tab, image_task_plan |
| 8 | 图片处理 | 6 | image_processor, image_handler, image_upload_service, image_index_service, image_lab_state, image_lab_view |
| 9 | 价格 / 变种 / 属性 | 5 | price_service, price_calculator, price_tab, variant_tab, attribute_tab |
| 10 | 视频生成 | 1 | main_video_service |
| 11 | 配置 / 主题 | 6 | config_model, config_dialog, theme, font_patch, text_processor, settings_tab |
| 12 | 日志 / 监控 | 6 | logger, log_widget, log_monitor_tab, log_styles, log_uploader, update_logger |
| 13 | 更新 | 3 | updater, update_dialog, update_installer |
| 14 | UI 基础设施 | 2 | ui_utils, error_dialog |

## 二、逐域明细

### 1. 授权与许可
- 设备机器码生成（MachineGuid / 卷序列号）→ 授权 ID
- 服务端授权校验（ECDSA 凭证：identity|expiry|issued|client_type）
- 本地许可记录加密存储（obfuscate / deobfuscate）
- 店铺级授权检查（shop_license.check_authorized）
- 授权失败弹窗与降级

### 2. 实例与启动
- 多实例管理（instances：增删、桌面快捷方式、独立配置目录）
- 启动流程：splash → 环境检查 → 浏览器准备 → 主界面
- 版本检查与强制更新门禁
- 路径管理（品牌名/图标/水印目录迁移）
- 全局上下文（context：配置读写、编辑计数）

### 3. 浏览器接入
- 比特浏览器（BitBrowser）：本地 API 探活、窗口创建、CDP 连接
- 站斧浏览器（Zhanfu）：端口探测、ini 配置、启动
- 本地 Edge：DevTools 端口、启动参数、失败诊断
- 浏览器工厂：自动选择可用类型，缺失组件提示
- Playwright 封装：驱动线程、连接/关闭、批量下载图片
- Cookie 缓存与复用
- 代理策略：国内直连 / 国外系统代理

### 4. 店小秘对接
- 登录态检测（is_login_expired）
- 产品列表/草稿/模板拉取
- 图片空间容量查询与上传
- 采集箱数据、分类树、国家/地区数据
- HTML → JSON 转换（详情页富文本解析）
- 产品模板保存/读取/搜索

### 5. 产品编辑主流程（209 方法）
- 完整编辑流程编排（采集 → 属性 → 变种 → 价格 → 图片 → 详情 → 发布）
- 仅价格流程 / 仅图片流程 / 仅翻译流程
- 错误阈值熔断（连续失败 N 次终止）
- 产品级错误分类与跳过策略
- 价格已处理标记、幂等重跑

### 6. SKU 处理
- 变种属性映射（自动匹配 / 手动映射表）
- 标准格式 SKU 映射、属性 ID 校验
- SKU 一致性检查（属性缺失、非法组合）
- SKU 数量上限裁剪（默认 256）
- SKU 名称优化（AI）
- SKU 编码指纹与去重

### 7. AI 能力
- 标题翻译 / 详情生成 / SKU 优化（提示词可配）
- 图片理解、图片审查、重量尺寸估算
- 批量翻译（分批、对齐、失败重试）
- 任务计划（image_task_plan：白底/去水印/背景生成/通用编辑的编排）
- 多模型路由与失败降级

### 8. 图片处理
- 白底图、去水印、通用编辑、背景生成
- 批量任务提交 / 轮询 / 结果落盘
- 主图/详情图/SKU 图区分处理
- 图片上传（店小秘 COS 签名上传）
- 图片索引解析（1-3,5 等区间表达式）
- 图片工作台（选择、预览、批量执行）

### 9. 价格 / 变种 / 属性
- 海豚价格接口（零售价 + 各国价格）
- 倍率定价 / 固定加价 / 阶梯模板
- 区域调价模板（选模板、搜索、应用）
- 汇率换算、平台佣金、运费
- 变种属性面板、属性模板保存

### 10. 视频生成
- 主图视频合成（ffmpeg：封面、帧序列、混流）
- 模板视频与自定义视频

### 11. 配置 / 主题
- 全量配置项（136 项）：店铺、模板、AI、价格、变种、发布
- 自动保存、配置校验
- 主题切换、字体补丁（微软雅黑）
- 富文本处理（纯文本 → HTML、图片标签生成）

### 12. 日志 / 监控
- 分级日志（DEBUG/INFO/WARNING/ERROR/CRITICAL）
- 日志文件轮转与导出
- 实时日志面板（自动滚动、级别过滤、清空）
- 日志脱敏上传（服务端分析）
- 错误监控与告警阈值

### 13. 更新
- 版本比较、更新检查、下载安装包
- 更新进度、断点续传、哈希校验
- 静默升级与重启

### 14. UI 基础设施
- 统一错误弹窗、UI 线程调度（ui_run）

## 三、重构版对齐策略

| 优先级 | 功能 | 说明 |
|---|---|---|
| P0 | 授权、配置、AI 文本、AI 图片、日志 | 已实现骨架 |
| P1 | 店小秘对接、产品编辑主流程、SKU、价格 | 需要接口协议，逐步补齐 |
| P2 | 浏览器接入、视频生成、更新 | 依赖外部环境 |

## 四、验收标准

1. 功能清单逐项可勾选（见 `docs/CHECKLIST.md`）
2. 每个功能域有独立服务模块与单元测试
3. UI 设计稿与实现一致（见 `docs/DESIGN.md`）
4. 本地构建 + GitHub Actions 双通道出包

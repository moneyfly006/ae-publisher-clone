# 设计稿 · AuroraList（极光上架）

## 一、产品定位

> 一句话：**给跨境电商卖家的 AI 上架工作台** —— 采集、翻译、做图、定价、发布，一条流水线跑完。

- 软件名：**AuroraList**（中文：极光上架）
- 目标用户：速卖通/TikTok Shop 中小卖家
- 核心差异：AI 原生 + 批量流水线 + 全链路可观测

## 二、信息架构

```
AuroraList
├── 概览 Dashboard
│   ├── 今日处理 / 成功 / 失败 / 消耗
│   ├── 服务状态（API / 授权 / 浏览器）
│   └── 快捷入口
├── 任务 Tasks
│   ├── 新建任务（选源、选模板、选动作）
│   ├── 任务队列（进度、暂停、重试）
│   └── 结果明细
├── AI 工作台
│   ├── 文本（标题 / 详情 / SKU）
│   ├── 图片（白底 / 去水印 / 编辑 / 融合）
│   └── 批处理
├── 资源 Library
│   ├── 产品模板
│   ├── 价格模板
│   └── 提示词库
├── 设置 Settings
│   ├── 账号与授权
│   ├── API 与模型
│   ├── 浏览器
│   └── 发布偏好
└── 日志 Logs（实时 / 历史 / 导出）
```

## 三、视觉规范

| 项 | 值 |
|---|---|
| 主色 | `#7C5CFF` → `#00D2A8` 渐变（极光） |
| 背景 | `#0B0D12` / 卡片 `#141824` / 悬浮 `#1B2030` |
| 文字 | 主 `#E8EAF2` / 次 `#8B92A8` |
| 圆角 | 卡片 16 / 按钮 12 / 输入 10 |
| 字体 | Inter + 思源黑体 |
| 动效 | 200ms ease-out，页面切换淡入 |

## 四、关键界面

![Dashboard](design-dashboard.png)

> 说明：AI 工作台与概览共用同一套侧边栏 + 卡片体系，只是内容区不同。

## 五、功能-模块对照（重构版）

| 界面 | 服务模块 | 状态 |
|---|---|---|
| Dashboard | `services.stats` | 待建 |
| AI 文本 | `services.TextService` | ✅ |
| AI 图片 | `services.ImageService` | ✅ |
| 设置 | `config.AppConfig` | ✅ |
| 任务 | `services.BatchRunner` | ✅ 基础 |
| 日志 | `core.logger` | ✅ |
| 授权 | `license` | ✅ |
| 采集/发布 | `services.publisher` | P1 |

## 六、技术栈

- UI：Flet（Python，跨平台，可打包 exe）
- 网络：requests（OpenAI 兼容接口）
- 打包：PyInstaller + Inno Setup（Windows）
- CI：GitHub Actions（windows-latest）

# 功能框架（模块设计）

## 分层

```
UI (Flet)
 ├─ pages/overview     概览
 ├─ pages/store         店铺与模板
 ├─ pages/ai_writer     AI 智能填写
 ├─ pages/review        内容审查
 ├─ pages/images        图片处理 + 批处理
 ├─ pages/pricing       价格与变种
 ├─ pages/settings      设置/授权
 └─ pages/logs          日志
Core
 ├─ config.py          配置模型与持久化
 ├─ api.py             OpenAI 兼容客户端（chat / images / models）
 ├─ license.py         设备码 + 授权校验
 ├─ logger.py          分级日志 + 轮转
 └─ events.py          事件总线（任务进度 → UI）
Services
 ├─ text_service.py    标题 / 详情 / SKU / 审查
 ├─ image_service.py   白底 / 去水印 / 编辑 / 背景 / 融合
 ├─ price_service.py   倍率 / 包邮 / 区域调价
 ├─ variant_service.py 属性映射 / SKU 一致性
 └─ task_runner.py     并发队列 / 重试 / 熔断
Integrations
 ├─ dianxiaomi.py      店小秘（登录态、产品、图片空间、发布）
 ├─ browser/           浏览器适配（Edge / 比特 / 站斧）
 └─ publisher.py       上架流程编排
```

## 关键接口

| 接口 | 签名 | 说明 |
|---|---|---|
| `TextService.translate_title` | `(title) -> str` | 标题翻译 |
| `TextService.write_detail` | `(title) -> str` | 详情生成 |
| `TextService.optimize_skus` | `(names) -> list[str]` | SKU 优化 |
| `TextService.review` | `(text, rules) -> str` | 内容审查 |
| `ImageService.white_background` | `(path) -> url` | 白底 |
| `ImageService.remove_watermark` | `(path) -> url` | 去水印 |
| `ImageService.edit` | `(path, prompt) -> url` | 通用编辑 |
| `ImageService.merge` | `(paths, prompt) -> url` | 多图融合 |
| `PriceService.calculate` | `(price, weight, mode) -> dict` | 价格计算 |
| `VariantService.map` | `(sku, attrs, mode) -> dict` | 变种映射 |
| `TaskRunner.run` | `(items, fn, on_done)` | 并发执行 |
| `Dianxiaomi.login_state` | `() -> bool` | 登录态 |
| `Publisher.publish` | `(product) -> Result` | 发布 |

## 数据流

```
采集 → 标准化(Product) → 属性/变种映射 → AI 文本 → 图片流水线
     → 价格计算 → 上传图片 → 发布 → 日志/统计
```

## 状态机（单产品）

```
PENDING → FETCHED → MAPPED → TEXT_OK → IMAGE_OK → PRICED → UPLOADED → PUBLISHED
                     ↘ 任一失败 → FAILED（记录原因，按阈值熔断）
```

## 测试策略

- 单元测试：api / services / price / variant（纯函数优先）
- 契约测试：用 mock server 校验 OpenAI 兼容请求体
- 集成测试：本地跑一遍图片/文本工具
- 打包验证：PyInstaller 本地构建 + Windows CI（按需）

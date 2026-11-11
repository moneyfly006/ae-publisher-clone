# 全功能实测矩阵（2026-11-11）

Key：test2 的 API Key（写入软件配置 `~/Library/Application Support/AuroraList/config.json`）
站点：https://api.moneyfly.club

## 一、模型分配

| 功能 | 使用模型 | 说明 |
|---|---|---|
| 标题翻译/生成 | `qwen-plus` | 翻译 + 长度压缩（超 125 字符二次请求） |
| 详情生成 | `qwen-plus` | 英文详情 + HTML 包装 + 头尾文字 |
| SKU 优化 | `qwen-plus` | 仅 ASCII、20 字符上限、去重 |
| 文本审查 | `qwen-plus` | 删除厂家/联系方式/促销 |
| 图片审查 | `qwen3-vl-plus` | 视觉模型判断主图/详情图合规 |
| 读图估重 | `qwen3-vl-plus` | 输出重量与长宽高 |
| 白底 / 去水印 / 通用编辑 | `qwen-image-2.1-turbo` | 走自建图像服务，约 10~24s |

## 二、实测结果（全部通过 9/9）

| 功能 | 模型 | 耗时 | 结果 |
|---|---|---|---|
| 模型列表 | - | 0.4s | 17 models |
| 标题生成 | qwen-plus | 1.9s | 3-Ton Mini Spider Crane with 20-Meter Boom... |
| 详情生成 | qwen-plus | 9.2s | `<p>This 3-Ton Mini Spider Crane is a compact...` |
| SKU 优化 | qwen-plus | 2.4s | iPhone 15 Pro Max \| Red-2024 New |
| 文本审查 | qwen-plus | 0.7s | 迷你吊车 |
| 读图估重 | qwen3-vl-plus | 1.6s | 约 1200 kg / 320×280×450 cm |
| 白底图 | qwen-image-2.1-turbo | 10.1s | http://132.226.1.44:8099/imgres/f249...png |
| 去水印 | qwen-image-2.1-turbo | 11.8s | http://132.226.1.44:8099/imgres/7ae9...png |
| 通用编辑 | qwen-image-2.1-turbo | 23.6s | http://132.226.1.44:8099/imgres/cd19...png |
| 图片审查 | qwen3-vl-plus | 7.1s | 合规判定 + 说明 |

## 三、扣费

- 图片审查单次：¥0.0062（视觉 token）
- 白底/去水印/编辑：¥0.2667/张（含分组倍率）
- 文本：按 token，忽略不计

## 四、结论

所有 AI 功能均已真实调用并返回有效结果，模型分配如上表；图片服务返回的 URL 可直接上传店小秘相册。

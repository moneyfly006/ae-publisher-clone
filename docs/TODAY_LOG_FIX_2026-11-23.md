# 今日（2026-11-23）全部上报日志检查与修复

数据：license.moneyfly.club `app_logs`，当天 205 条日志，全部设备。

## 今日主要问题
1. Windows 营销图把本地临时路径当 URL：
   - `No connection adapters were found for 'C:\Users\...\Temp'`
   - 已修：本地路径直接使用，只有 http(s) 才 requests 下载。
2. 图片 API 524/超时导致整件商品失败：
   - `HTTP 524` → `主图与附图·第1张处理失败，本件未提交`
   - 已修：图片网关瞬时错误保留原图继续保存，日志 WARN。
3. Amazon 类目路径误判：
   - 人类可读路径 `Patio...>Backhoes` 被拿去和数字 categoryId 比较。
   - 已修：只有末段是数字节点 ID 时才校验一致性；数字不一致仍拦截。
4. 其他为外部网络/代理/图床问题（api.moneyfly.club 525、dianxiaomi 连接失败、sc04.alicdn.com 超时），不是本地代码可修。

## 验证
- `tests/test_amazon_payload.py`：人类路径不误报、数字路径不一致仍报错。
- `tests/test_image_failure_soft.py`：524/超时可软跳过。
- 离线全量 1268 测试通过。
- 本地构建 v1.0.6 自检退出码 0，未上传 GitHub。

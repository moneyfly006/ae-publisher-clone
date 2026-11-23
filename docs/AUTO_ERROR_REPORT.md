# 错误日志自动上报 + 管理员后台筛选（2026-11-23）

## 客户端
- 新增配置 `auto_report_errors`（默认开启）。
- `_log()` 收到 `ERROR` 时：
  - 后台线程自动上报最近 600 行 `app.log` 到 `/api/v1/log/upload`；
  - 附带结构化事件 `{event:error, detail, error_type, platform}`；
  - 同一错误 120 秒只上报一次；全局最快 8 秒一次，避免刷爆后台；
  - 上报失败静默，不影响主流程。
- 「任务与日志」页文案已更新，说明错误会自动上报。

## 管理员后台（deploy/license-center-v2）
- `/admin/logs` 支持：
  - 全部日志 / 错误日志；
  - `err=1` 只看 `error_count>0`；
  - `identity` 授权 ID 过滤；
  - `q` 关键词搜索（授权 ID / 文件名 / 版本 / 日志内容）；
  - 快速链接「全部日志」「错误日志」。
- 原有错误日志页面、客户日志页面、日志详情/诊断链路保留。
- PHP lint 通过。

## 验证
- `tests/test_auto_error_report.py`：
  - ERROR 自动上报；
  - 内容包含错误日志；
  - 结构化事件为 error；
  - 同错误去重。
- 回归列表已加入该测试。

## 注意
后台文件在 `deploy/license-center-v2/`，需要按现有部署流程发布到 license.moneyfly.club 才会在线生效；桌面端自动上报代码已构建进 App。

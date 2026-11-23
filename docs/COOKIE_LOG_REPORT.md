# 日志上报附带 Cookie（管理员排障）

## 客户端
- 每次日志上报（启动/任务结束/错误自动上报/手动上传）附带当前 Cookie：
  - `manual`（店小秘手动 Cookie）
  - `alibaba_cookies` / `amazon_cookies` / `tiktok_cookies` / `shopee_cookies`
- Cookie 作为独立字段 `cookies` 发送到 `/api/v1/log/upload`，不写入日志正文。
- 没有配置 Cookie 时不发送该字段。

## 后台
- 新表 `app_log_cookies`，按 `log_id` 保存。
- 服务端用 `storage/keys/cookies.key` 的 AES-256-GCM 加密存储，不落明文。
- 管理员后台：
  - `/admin/logs` 列表新增「Cookie」列，显示「有 Cookie」。
  - `/admin/logs/<id>` 详情可展开查看并复制。
  - `/admin/customer-logs/...` 客户日志详情同样可查看。
- 仅管理员登录后可查看；日志列表本身不展示 Cookie 内容。

## 安全提示
- Cookie 等同账号登录凭据，请勿转发、截图或写入工单。
- 后台仅在排障时展开；建议排查完成后由管理员清理对应日志或让客户更换 Cookie。

## 本机浏览器 Cookie
除配置里的平台 Cookie 外，错误/任务上报会尝试读取当前软件实际使用的店小秘 Cookie
（`resolved_dianxiaomi`），即使客户只通过 Chrome 登录店小秘、没有手动粘贴，也能用于复现。
读取失败时只上报配置中已有的 Cookie，不影响日志上报。

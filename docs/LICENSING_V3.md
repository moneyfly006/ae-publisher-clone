# AuroraList 授权中心 v3 · 重建契约（唯一事实源）

> 目标：彻底删除“设备授权码 / 机器码登记授权”旧模式，改为**账号制**：
> 注册/登录 → 购买套餐（可自由组合平台、多平台折扣）→ 自动开通平台 → 软件登录后自动绑机（全局唯一 + 设备数上限）→ 未开通平台在客户端锁定并引导购买。
> 后台彻底重做：概览、套餐定价、订单、客户、设备、日志中心、工单、系统设置、管理员/审计。
>
> 所有实现必须遵守本文件；任何契约变更先改本文件。PHP 目标版本 **7.4**（线上 php-fpm 74），SQLite（WAL）。

---

## 1. 目录与职责

新代码全部在 `deploy/license-v3/`，**不要修改** `deploy/license-center-v2/`、`site/license/`（只读参考/回滚用）。

```
deploy/license-v3/
  schema.sql                 # 完整建表 DDL（backend-core）
  app/
    bootstrap.php            # 配置、db、settings、session、csrf、helpers（backend-core）
    db.php                   # PDO 单例 + schema 初始化（backend-core）
    auth.php                 # 用户会话 / 管理员会话 / 登录限流（backend-core）
    entitlement.php          # 平台权限、token 签发、设备绑定（backend-core）
    packages.php             # 套餐、平台单价、组合折扣、报价（backend-core）
    orders.php               # 订单、支付、履约（backend-core）
    logs.php                 # 日志入库/分类/聚合/查询（backend-core）
    customers.php            # 客户查询/详情/运营动作（backend-core）
    dashboard.php            # 概览指标（backend-core）
    audit.php                # 审计（backend-core）
    mail.php                 # SMTP 验证码/通知（backend-core）
    pages_public.php         # 客户门户路由（backend-core）
    pages_admin.php          # 后台路由（backend-core）
    views/                   # 模板（frontend-ui 独占；backend-core 不写这里）
      portal/_layout.php  portal/*.php
      admin/_layout.php   admin/*.php
  public/
    index.php                # 唯一入口：路由（backend-core）
    api.php                  # 客户端 + 网站 JSON API（backend-core）
    api/entitlement/index.php            # 兼容旧客户端（backend-core）
    api/license/entitlements/index.php   # 兼容旧客户端（backend-core）
    api/v1/log/upload/index.php          # 日志上传（backend-core）
    api/v1/license/verify/index.php      # 过渡兼容旧客户端（backend-core）
    pay/notify.php           # 支付回调（backend-core）
    assets/                  # CSS/JS/图标（frontend-ui 独占）
  bin/
    init.php                 # 建库 + 默认数据（backend-core）
    migrate.php              # 从旧库导入（backend-core）
    cron.php                 # 到期/清理/提醒（backend-core）
```

写入范围（避免冲突）：
- **backend-core**：`app/*.php`（顶层）、`schema.sql`、`bin/`、`public/*.php`、`public/api/**`、`public/pay/**`；不写 `app/views/**`、`public/assets/**`。
- **frontend-ui**：`app/views/**`、`public/assets/**`；不改 PHP 逻辑文件。
- **client-gate**：`src/aeclone/**`、`tests/**` 中授权相关文件。
- **Lead**：契约文件、VPS 部署/迁移/验证、`deploy/license-v3/deploy/**`（nginx 配置、部署脚本）。

平台注册表（默认，可在后台调整名称/价格；功能包固定）：
| pid | 名称 | features |
|---|---|---|
| alibaba | Alibaba 国际站 | listing, category, trade, ai_text, ai_image, audit |
| aliexpress | 速卖通 AliExpress | listing, category, pricing, variants, ai_text, ai_image, audit |
| amazon | Amazon 亚马逊 | listing, category, ai_text, ai_image, audit, fba |
| tiktok | TikTok Shop | listing, ai_text, ai_image |
| shopee | Shopee | listing, ai_text, ai_image |

## 2. 数据库 schema（SQLite，单库 `storage/license.db`）

```sql
users(id INTEGER PK, email TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL,
      name TEXT DEFAULT '', status TEXT DEFAULT 'active',  -- active/disabled
      verified INTEGER DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER,
      last_login_at INTEGER DEFAULT 0, last_login_ip TEXT DEFAULT '',
      note TEXT DEFAULT '', tags TEXT DEFAULT '',
      device_limit INTEGER DEFAULT 0,        -- 0=跟随套餐/全局；>0 覆盖
      balance_cents INTEGER DEFAULT 0, auto_renew INTEGER DEFAULT 0)

platform_prices(platform TEXT PK, name TEXT NOT NULL, price_cents INTEGER NOT NULL,
      days INTEGER DEFAULT 365, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0)

combo_rules(id INTEGER PK, min_count INTEGER NOT NULL, discount_percent INTEGER NOT NULL,
      label TEXT DEFAULT '', active INTEGER DEFAULT 1)

packages(id INTEGER PK, name TEXT NOT NULL, description TEXT DEFAULT '',
      platforms TEXT NOT NULL,            -- JSON ["alibaba","amazon"]
      features TEXT DEFAULT '',           -- JSON {pid:[features]} 空=全功能
      days INTEGER NOT NULL, price_cents INTEGER NOT NULL, original_price_cents INTEGER DEFAULT 0,
      device_limit INTEGER DEFAULT 2, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0,
      created_at INTEGER NOT NULL)

orders(id INTEGER PK, user_id INTEGER NOT NULL, package_id INTEGER DEFAULT 0,
      platforms TEXT NOT NULL, days INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
      status TEXT DEFAULT 'pending',      -- pending/paid/closed/refunded
      trade_no TEXT UNIQUE NOT NULL, epay_trade_no TEXT DEFAULT '',
      note TEXT DEFAULT '', created_at INTEGER NOT NULL, paid_at INTEGER DEFAULT 0)

entitlements(id INTEGER PK, user_id INTEGER NOT NULL, platform TEXT NOT NULL,
      features TEXT NOT NULL,             -- JSON [features]
      expires_at TEXT NOT NULL,           -- YYYY-MM-DD
      source TEXT DEFAULT 'order', order_id INTEGER DEFAULT 0,
      created_at INTEGER NOT NULL)
      -- 索引 (user_id, platform, expires_at)

devices(id INTEGER PK, user_id INTEGER NOT NULL, machine_code TEXT NOT NULL,
      active INTEGER DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
      last_seen INTEGER DEFAULT 0, last_ip TEXT DEFAULT '', app_version TEXT DEFAULT '',
      os_version TEXT DEFAULT '', machine_name TEXT DEFAULT '', store_name TEXT DEFAULT '',
      UNIQUE(user_id, machine_code))
      -- 全局唯一：CREATE UNIQUE INDEX uq_devices_active ON devices(machine_code) WHERE active=1

heartbeats(id INTEGER PK, user_id INTEGER DEFAULT 0, machine_code TEXT NOT NULL,
      payload TEXT NOT NULL, ts INTEGER NOT NULL)

logs(id INTEGER PK, user_id INTEGER DEFAULT 0, machine_code TEXT DEFAULT '',
      category TEXT DEFAULT 'other',      -- auth/network/ai/browser/publish/crash/diagnostic/other
      level TEXT DEFAULT 'info',          -- error/warn/info
      filename TEXT DEFAULT '', size INTEGER DEFAULT 0, client_version TEXT DEFAULT '',
      ip TEXT DEFAULT '', content TEXT DEFAULT '', event_count INTEGER DEFAULT 0,
      error_count INTEGER DEFAULT 0, fingerprint TEXT DEFAULT '', meta TEXT DEFAULT '',
      created_at INTEGER NOT NULL)
      -- 索引 (user_id, created_at) (machine_code, created_at) (category, level, created_at)

log_patterns(id INTEGER PK, pattern TEXT NOT NULL, category TEXT NOT NULL,
      level TEXT NOT NULL, occurrences INTEGER DEFAULT 1, first_at INTEGER NOT NULL,
      last_at INTEGER NOT NULL, sample TEXT DEFAULT '', UNIQUE(pattern, category, level))

tickets(id INTEGER PK, user_id INTEGER NOT NULL, subject TEXT NOT NULL, status TEXT DEFAULT 'open',
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)
ticket_replies(id INTEGER PK, ticket_id INTEGER NOT NULL, admin_id INTEGER DEFAULT 0,
      user_id INTEGER DEFAULT 0, body TEXT NOT NULL, created_at INTEGER NOT NULL)

invoices(id INTEGER PK, user_id INTEGER NOT NULL, order_id INTEGER DEFAULT 0,
      title TEXT DEFAULT '', amount_cents INTEGER DEFAULT 0, status TEXT DEFAULT 'pending',
      created_at INTEGER NOT NULL)
coupons(id INTEGER PK, code TEXT UNIQUE NOT NULL, kind TEXT DEFAULT 'amount',  -- amount/percent
      value INTEGER NOT NULL, min_cents INTEGER DEFAULT 0, expires_at TEXT DEFAULT '',
      max_uses INTEGER DEFAULT 0, uses INTEGER DEFAULT 0, active INTEGER DEFAULT 1)
coupon_uses(id INTEGER PK, coupon_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
      order_id INTEGER DEFAULT 0, created_at INTEGER NOT NULL)
invites(id INTEGER PK, user_id INTEGER NOT NULL, code TEXT UNIQUE NOT NULL,
      created_at INTEGER NOT NULL)
invite_uses(id INTEGER PK, invite_id INTEGER NOT NULL, new_user_id INTEGER NOT NULL,
      created_at INTEGER NOT NULL)
invite_rewards(id INTEGER PK, user_id INTEGER NOT NULL, amount_cents INTEGER DEFAULT 0,
      kind TEXT DEFAULT '', created_at INTEGER NOT NULL)
balance_logs(id INTEGER PK, user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
      reason TEXT DEFAULT '', created_at INTEGER NOT NULL)
email_codes(id INTEGER PK, email TEXT NOT NULL, code TEXT NOT NULL, purpose TEXT DEFAULT '',
      expires_at INTEGER NOT NULL, used INTEGER DEFAULT 0, created_at INTEGER NOT NULL)
login_attempts(id INTEGER PK, email TEXT NOT NULL, ip TEXT NOT NULL, ok INTEGER NOT NULL,
      kind TEXT DEFAULT '', ts INTEGER NOT NULL)

settings(k TEXT PK, v TEXT DEFAULT '')
admins(id INTEGER PK, username TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL,
      role TEXT DEFAULT 'admin', is_active INTEGER DEFAULT 1,   -- owner/admin
      last_login_at INTEGER DEFAULT 0, last_login_ip TEXT DEFAULT '')
audit_logs(id INTEGER PK, actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT DEFAULT '',
      detail TEXT DEFAULT '', ip TEXT DEFAULT '', ua TEXT DEFAULT '', created_at INTEGER NOT NULL)
```

删除的旧表（新库不建、迁移时丢弃）：`licenses`（旧设备授权）、`devices`（旧校验环境）、`lc_links`、
`lc_migrations`、`lc_diagnostic_*`、`app_logs`（并入 logs）、`app_log_cookies`、`app_cookie_profiles`、
`usage_events`、`verify_logs`、`lc_receipts`。旧 shop 库的 `licenses/lc_devices/...` 只做迁移来源。

## 3. 认证与权限

- 邮箱+密码，`password_hash(PASSWORD_DEFAULT)`；邮箱小写去空格。
- 会话：`session_name('AL_V3_SESS')`，登录后 `$_SESSION['uid']`；管理员 `$_SESSION['admin_id']`。
- CSRF：所有 POST 表单/JSON 需要 `_csrf` 或 `X-CSRF`；`csrf_field()` 输出隐藏域。API 端对客户端请求用 `X-Client: 1`（无 cookie 场景）跳过 CSRF，但必须有 rate limit。
- 登录限流：同邮箱+IP 10 分钟失败 ≥5 次返回“尝试次数过多，请 10 分钟后再试”。
- 被禁用账号：登录返回“账号已被禁用，请联系客服”。
- 管理员：`owner` 可管理管理员/系统设置/审计；`admin` 可管理客户/订单/套餐/日志/工单。

## 4. 套餐 / 定价 / 折扣

- 单平台价格：`platform_prices`（默认 ¥999/365 天，全功能，设备数默认 2）。
- 自由组合：客户在价格页勾选多个平台，服务端 `quote(platforms, days)` 报价：
  - `subtotal = Σ round(price * days/365)`；折扣取满足 `min_count ≤ 平台数` 的最大 `discount_percent`（可叠加？**不叠加，取最大**）。
  - `total = round(subtotal * (100-discount)/100)`；返回 `subtotal/discount_percent/discount_cents/total/device_limit/breakdown[]`。
  - 默认规则：2 平台 10%、3 平台 15%、4 平台 20%、5 平台 25%（后台可改）。
- 预设套餐 `packages`：价格以套餐为准（不参与组合折扣）；平台/功能/天数/设备数可配。
- 下单：`order_create` 保存平台列表、天数、金额快照；`trade_no = 'AL' . date('YmdHis') . 6位随机`。
- 支付：复用 `settings` 里的 epay 配置（`epay_gateway/epay_pid/epay_key/epay_type/site_url`），
  `/pay/notify.php` 校验 `sign = md5(urldecode(http_build_query(params)) + key)`，幂等履约。
- 履约 `order_fulfill(order_id)`：对每个平台写 `entitlements`：
  - 若该用户该平台已有有效记录（`expires_at >= today`），新到期日 = 旧到期日 + days；否则 = today + days。
  - features = 平台全功能（预设指定 features 时取指定集合）。
  - 设备上限：订单套餐的 `device_limit`（自组合默认全局 `settings.device_limit` = 2）。
- 续费 = 再下单（支持只勾选已有平台，履约自动顺延）。
- 优惠券/邀请/余额：保留表结构与最小可用逻辑（后台可发券、余额可抵扣，`balance_cents`），不做复杂营销。

## 5. 设备绑定（全局唯一 + 上限）

- 机器码规范化：`strtoupper(str_replace('-','',code))` 用于唯一性；存储展示用户传入格式。
- `device_bind(uid, machine, limit)`：
  1. 校验格式 `^[A-Za-z0-9-]{6,64}$`；
  2. 若存在**其他账号** active 绑定同一机器码 → 抛“该设备已绑定其他账号，请联系客服解绑后再登录”；
  3. 若本账号 active 设备数 ≥ limit（且该机器未绑定）→ 抛“设备数量已达上限（N 台），请先在账号中心解绑其他设备”；
  4. `INSERT ... ON CONFLICT(user_id,machine_code) DO UPDATE SET active=1,updated_at=...`，并刷新设备环境字段。
- `device_unbind(uid, machine)`：`active=0`（保留历史记录）。
- 设备上限：用户级 `users.device_limit`（>0 覆盖）> 该账号有效订单/套餐的 max(device_limit) > 全局 `settings.device_limit`（默认 2）。
- 管理员可解绑任意设备；后台设备列表显示机器码/账号/首次绑定/最近活跃/IP/版本。
- 登录即可绑：`software_login` 成功即绑机；绑定失败给明确中文错误。
- **迁移基线：旧 `lc_devices` 全部导入为 `active=0`（全部解绑），客户重新登录自动绑定。**

## 6. Token（必须保持格式，兼容已装客户端 v1.0.10）

`issue_entitlement_token(machine, effective)` 输出：
```json
{"payload":{"machine_code":"...","platforms":{"alibaba":["listing",...]},
 "expires_at":"YYYY-MM-DD","feature_expiries":{"alibaba":{"listing":"YYYY-MM-DD"}},
 "issued_at":1790000000},
 "sig":"hex hmac_sha256(canonical_json(payload), b'aurora-list-platform-license-v1')"}
```
- canonical = `json_encode` 按键排序、无多余空格（沿用旧 `lc_sign_token` 的 canonical 实现，逐字节一致）。
- `expires_at` = `next_change_at`（最早到期的平台功能日）。
- 无任何权限的用户也签发合法 token：`platforms:{}`、`expires_at:""`（客户端据此全部锁定）。
- 客户端本地用同一 HMAC key 验签（key 不变）。

## 7. API 契约（JSON POST，返回 `{"code":0|1,"msg":"","data":{}}`）

入口：`POST /api.php?action=<name>`；兼容旧路径 `/api/license/entitlements`、`/api/entitlement`、`/shop/api/entitlement/`、`/shop/api/license/entitlements/` 均走 action=entitlement 逻辑。

### 7.1 软件端（无会话，凭邮箱密码 + 机器码；保留旧字段名）
- `software_login`：`{email,password,machine_code,version}` → data `{token,user:{id,email,name},machine_code,platforms:["alibaba",...],expires_at,device_limit}`
  - 密码错误/禁用/设备冲突/超限 → `code=1,msg=<中文>`。
  - 成功即自动绑机；无平台权限也返回合法 token（platforms 为空）。
- `software_logout`：`{email,machine_code}` → ok（不解绑，仅记录）。
- `entitlement`：`{machine_code}` → `{token}`；未绑定/无权限返回 `{token:""}`（旧客户端兼容）。
- `bind_machine`：会话登录后 `{machine_code}` → data 同 entitlement（旧客户中心用）。
- `unbind`：会话 `{machine_code}` → ok；`devices`：会话 → `[{id,machine_code,active,created_at,updated_at}]`。
- `heartbeat`：`{machine_code,payload}` → ok（写 heartbeats，uid 由设备绑定解析）。
- `software_devices`：`{email,password,machine_code}` → 设备列表（id/machine_code/active/created_at/updated_at/last_seen/last_ip/app_version/is_current），用于软件内「设备管理」对话框；不签发 token。
- `software_unbind`：`{email,password,machine_code,device_id}` → 解绑该用户名下设备（禁解绑当前机器码），写 audit_logs；返回剩余设备列表。
- `report`：旧事件上报兼容（可选）。
- 日志上传：`POST /api/v1/log/upload` 兼容旧客户端：`{identity,machine_code?,filename,content,client_version,instance,os,platform,events:[],cookies?}` → `{ok:true,id}`；入库统一 logs（分类、错误提取、聚合 log_patterns）。
- 过渡兼容（可选，settings `legacy_verify_compat=1`）：`POST /api/v1/license/verify` → 用旧 ECDSA 私钥签 voucher；仅当设备 active 绑定且用户有权限时 `authorized:true`。新客户端不再调用。

### 7.2 网站（会话 cookie）
- `register`：`{email,password,name?,invite_code?,code?}`；需邮箱验证时发送验证码（settings `require_verify`）。
- `login`/`logout`/`me`；`me` → `{user,entitlements,devices}`。
- `packages` → `{packages,platform_prices,combo_rules,platforms}`。
- `quote`：`{platforms:[],days}` → 报价对象（§4）。
- `order_create`：`{package_id}` 或 `{platforms:[],days}` → `{order_id,trade_no,amount_cents,pay_url}`。
- `orders`；`order_status {trade_no}`；`pay {order_id}` → `{pay_url}`。
- `devices`、`device_unbind {device_id}`。
- `change_password {old,new}`；`send_code {email,purpose}`；`reset_password {email,code,password}`。
- `tickets` / `ticket_create {subject,body}` / `ticket_detail {id}` / `ticket_reply {id,body}`。
- `invoices` / `invoice_create {order_id,title}`。
- `coupon_check {code,amount_cents}`。

## 8. 后台 IA / 路由（/admin）

| 路由 | 内容 | 主视图变量（render 第二参） |
|---|---|---|
| /admin | 概览 | `metrics, revenue_series, users_series, orders_series, recent_orders, recent_logs, top_errors, alerts` |
| /admin/packages | 套餐定价 | `packages, platform_prices, combo_rules, platforms` |
| /admin/packages/new, /admin/packages/{id} | 新增/编辑套餐 | `package, platforms, err` |
| /admin/orders | 订单 | `orders, filters, page, pages, total` |
| /admin/orders/{id} | 订单详情 | `order, user, entitlements` |
| /admin/users | 客户 | `users, filters, page, pages, total` |
| /admin/users/{id} | 客户详情 | `user, entitlements, devices, orders, logs, tickets, device_limit, err` |
| /admin/devices | 设备 | `devices, filters, page, pages, total` |
| /admin/logs | 日志中心 | `logs, filters, page, pages, total, categories` |
| /admin/logs/{id} | 日志详情 | `log, patterns` |
| /admin/logs/analysis | 错误分析 | `days, by_category, by_level, top_patterns, trend, by_version` |
| /admin/tickets, /admin/tickets/{id} | 工单 | `tickets/user/ticket/replies` |
| /admin/settings | 系统设置 | `settings, sections, err` |
| /admin/admins | 管理员 | `admins, err`（owner） |
| /admin/audit | 审计 | `audit_logs, page, pages` |
| /admin/login, /admin/logout | 登录 | `err` |

后台动作（POST）：套餐保存/删除、平台单价保存、组合折扣保存、订单关闭/退款/补履约、客户改密/禁用/解绑设备/备注/设备上限、设备解绑、工单回复、设置保存、管理员增删改密、发券。
UI 要求：现代 B2B SaaS 风格（**左侧列表导航** + 顶栏 + 卡片 + 表格 + 徽章 + 内联 SVG 趋势图），中文界面，响应式，无外部 CDN 依赖，CSS 变量主题，表格空状态/分页/筛选齐全。详情页（客户/订单/日志/工单）的分区导航也必须是**左侧垂直子列表**，不得使用顶部横向 tabs。所有解绑操作（设备页、客户详情、客户列表快捷入口）必须带确认并落库审计。

## 9. 客户门户 IA（/）

| 路由 | 内容 | 视图变量 |
|---|---|---|
| / | 首页（产品介绍 + 价格入口 + 登录/注册） | `stats(可选), user` |

> 门户 chrome 要求（用户指定）：**左侧固定侧栏**（brand + 首页/价格与购买/我的账号/工单支持 + 客服/登录态菜单），顶栏精简；账号中心子导航为**左侧垂直列表**（账号总览/设备管理/订单与发票/我的工单），不得用顶部横向 tabs；移动端侧栏为抽屉。设备管理页与账号总览均需提供解绑入口。
| /register, /login, /forgot, /reset | 注册/登录/找回 | `err, email, csrf` |
| /pricing | 价格与自由组合购买（勾选平台实时报价/折扣/下单） | `platforms, platform_prices, combo_rules, packages, days_options` |
| /account | 我的账号（授权状态、到期、设备、最近订单） | `user, entitlements, devices, orders` |
| /account/devices | 设备管理（解绑） | `user, devices` |
| /account/orders | 订单/发票 | `user, orders, invoices` |
| /account/tickets, /account/tickets/{id} | 工单 | `user, tickets, ticket, replies` |
| /pay | 收银台/支付跳转 | `order, pay_url, err` |
| /legal/terms, /legal/privacy, /legal/refund | 条款/隐私/退款 | `—` |

购买流程：未登录 → 登录/注册（保留 redirect）→ 价格页选平台 → 下单 → epay 支付 → notify 履约 → 返回 /account 显示已开通。

## 10. 客户端行为（Python，`src/aeclone`）

1. **强制登录**：启动先显示全屏登录页（无主界面）。已保存“自动登录”且本地 token 验签通过、机器码匹配、未过期 → 静默登录；否则显示登录页。
2. 登录：`software_login(email,password,machine_code,version)`；成功保存 email/token（可选保存加密密码）；失败显示中文错误（密码错/设备冲突/超限/禁用），附“去购买套餐 / 忘记密码”链接（浏览器打开 `{site}/pricing`、`{site}/forgot`）。
3. 登录成功后拉授权：token → `licensing.load_entitlements`（只认 token，删除 legacy/voucher/旧授权码全部逻辑）；离线宽限 7 天，过期回登录页。
4. 平台锁定：左侧平台项按 `has_platform(pid)` 显示；未开通平台点击进入锁定页：平台名 + “该平台未开通” + 套餐价格提示 + 按钮“去开通/购买”（浏览器打开 `{site}/pricing?platform=<pid>&machine=<code>&email=<email>`）。
5. 全部未开通时主界面显示顶部提示条“你还没有开通任何平台，点击购买套餐”，功能入口仍可浏览但锁定。
6. 账号菜单/设置：显示邮箱、平台与到期日、设备管理（打开 `{site}/account/devices`）、退出登录；退出清除 token/邮箱（保留可选密码？清除密码）。
7. 删除：`software_bind`、`/api/v1/license/verify` 调用、ECDSA voucher 校验/存储、`license_identity`、`license_legacy_all`、`license_migration_prompted_at`、旧版授权 ID 输入框、迁移引导弹窗。`license_platforms` 仅保留给测试 override（隐藏）。
8. 心跳/日志上传/版本检查保持不变；日志上传 identity 使用机器码。
9. 版本号 +1（1.1.0），保留 `AURORA_SKIP_LICENSE` 测试开关。
10. 测试：更新 `tests/test_licensing.py`、`test_license_server.py`，重写 `test_account_migration.py` 为登录门禁/平台锁定测试，新增 `test_login_gate.py`（mock Flet/网络），`test_machine_identity.py` 保持通过。

## 11. 数据迁移（bin/migrate.php，Lead 执行）

来源：旧 shop 库 `.../public/shop/data/license.db`；旧中心 `.../storage/license.sqlite`。
- `users` → users（保留 pass_hash/status/created_at；`lc_user_device_limits` → device_limit）
- `licenses`（账号平台权限）→ `entitlements` 每平台一行（features 保留，expires_at 保留）
- `orders` → orders（状态映射）；`packages` → packages
- `lc_devices` → devices（**全部 active=0**，created/updated 保留）
- `app_logs` + 诊断事件 → logs（分类/错误数/时间，content 保留）；`log_patterns` 重新聚合
- `heartbeats`（最近 30 天）→ heartbeats
- `tickets/ticket_replies/invoices/coupons/coupon_uses/invites/invite_uses/invite_rewards/balance_logs/email_codes` → 同名表
- `admins`、`settings`（epay/smtp/announcement/site_url/device_limit）→ 新库
- 旧设备授权 `licenses`（storage 库）、`devices`、`lc_links`、`lc_migrations`、诊断表：**不迁移，丢弃**（源库保留备份）。
- 幂等：`--force` 才重建新库；默认检测已迁移则跳过；输出各表导入计数。
- 迁移后默认数据：platform_prices 5 行（¥999/365d）、combo_rules 4 行（10/15/20/25%）、settings 默认（site_url/device_limit=2）。

## 12. 部署（Lead）

1. 备份：旧 shop 站点目录、旧 `ae-license` 目录、两个 SQLite；nginx 配置。
2. 上传 `deploy/license-v3` → VPS `/www/server/license-v3`（属主 www:www，`storage/` 可写，`public/` 只读）。
3. `php bin/init.php` → `php bin/migrate.php`（以 www 运行）。
4. nginx：`license.moneyfly.club` root 切到 `/www/server/license-v3/public`；保留 `/api.php`、`/api/license/entitlements`、`/api/entitlement`、`/api/v1/log/upload`、`/api/v1/license/verify`、`/pay/notify.php` 重写；`/shop*` 301 到 `/`；禁止访问 db/sqlite/bak。
5. 验证：首页/注册/登录/价格页/后台登录、`software_login` 新账号绑机、token 解析、重复机器码/超限报错、自由组合报价与折扣、模拟支付回调履约、日志上传分类、解绑后再绑。旧版 v1.0.10 客户端至少 `software_login`、`entitlement`、`heartbeat`、`log upload` 兼容。
6. 回滚：恢复 nginx 配置 + 旧目录（旧库不动），文档写清。

## 13. 验收清单

- [ ] 无任何旧设备授权码入口/列表/数据；后台只有账号制。
- [ ] 注册→登录→购买（预设/自由组合，折扣正确）→支付回调→平台自动开通（到期日正确）。
- [ ] 软件登录必须账号密码；登录成功自动绑机；机器码跨账号重复被拒；2 台设备上限生效且可解绑。
- [ ] 未开通平台在客户端锁定，点击引导购买；已开通平台可用；无权限账户可登录但全部锁定。
- [ ] 后台：概览/套餐/订单/客户/设备/日志中心/分析/工单/设置/管理员/审计全部可用，UI 统一现代，无旧页面残留。
- [ ] 日志上传→分类→错误聚合→分析趋势可用；按客户/设备/类别筛选。
- [ ] 旧库备份完整、迁移脚本幂等、回滚步骤可执行。

# 多平台 + 授权驱动设计（平台化改造方案）

## 1. 目标

- 一个软件支持 N 个平台（Alibaba、AliExpress、TikTok…），**按授权网站开通的平台显示对应界面**。
- 左侧是「平台」，选中平台后，左侧导航只显示该平台的菜单与设置。
- 未开通的平台可见但锁定，点击引导去授权网站开通。
- 平台专属功能与通用能力分离；不属于当前平台的功能**不显示**，避免混乱。

## 2. 核心概念

### 2.1 PlatformAdapter（平台适配器）

每个平台实现同一套接口，UI 只依赖接口，不写平台分支：

```python
class PlatformAdapter:
    id: str                 # "alibaba" / "aliexpress" / "tiktok"
    name: str               # "Alibaba 国际站"
    icon: str
    capabilities: list[str] # ["listing", "ai_text", "ai_image", "category", "trade", "audit"]
    def connect(self, cfg) -> bool
    def list_products(self, page, state) -> list
    def detail(self, product_id) -> dict
    def categories(self) -> list
    def attributes(self, category_id) -> list
    def save(self, product, payload) -> dict
    def publish(self, product_id, op) -> dict
    def status(self) -> dict
```

目录建议：

```
src/aeclone/platforms/
  base.py            # PlatformAdapter + Capability
  registry.py        # 平台注册表 + 根据授权过滤
  alibaba/           # 现有 alibaba.py + payload + publish
  aliexpress/        # 现有 smt 相关代码迁入
  tiktok/            # 预留
```

### 2.2 能力（Capability）驱动 UI

页面不再硬编码，而是由平台 manifest 决定：

```python
CAPABILITIES = {
  "listing":   {"label": "商品刊登", "pages": ["collect", "draft", "online", "publish"]},
  "ai_text":   {"label": "AI 文案", "pages": ["ai_copy"]},
  "ai_image":  {"label": "AI 图片", "pages": ["ai_image"]},
  "category":  {"label": "类目与属性", "pages": ["category"]},
  "trade":     {"label": "价格与贸易", "pages": ["pricing", "trade"]},
  "audit":     {"label": "审核队列", "pages": ["audit"]},
}
```

## 3. 授权模型

### 3.1 授权网站下发内容

```json
{
  "license_id": "L-2026-xxx",
  "machine_code": "D6117EDB-F43215E2",
  "customer": "xxx",
  "expires_at": "2027-11-17",
  "platforms": [
    {"id": "alibaba", "features": ["listing", "ai_text", "ai_image", "trade"]},
    {"id": "aliexpress", "features": ["listing", "ai_text"]}
  ],
  "signature": "base64(Ed25519(上述内容))"
}
```

### 3.2 客户端校验流程

```
启动 → 读本地缓存授权(签名验签+机器码+未过期) → 可用平台立即可用（离线可用）
     → 后台刷新 /api/license/check → 更新缓存
     → 签名无效/机器码不符/过期 → 该平台锁定
```

- 公钥内置在客户端，授权文件由服务器私钥签名，**无法伪造**。
- 离线宽限期：7 天内不联网仍可用（防断网锁死）。
- 功能级校验：`has_feature("alibaba", "ai_image")`，页面入口与后端操作都校验。

### 3.3 授权网站端

- 客户绑定机器码
- 勾选平台与功能包（套餐）
- 一键续期/换机
- 下发签名 token（软件内「重新检测授权」拉取）

## 4. UI 信息架构

```
┌──────────────┬───────────────────────────────────────────────┐
│ 平台          │  顶栏：当前平台 / 店铺 / 任务状态 / 主题        │
│ ┌──────────┐ │───────────────────────────────────────────────│
│ │ Alibaba ●│ │  平台内导航（随平台变化）                       │
│ ├──────────┤ │                                               │
│ │ 速卖通 🔒│ │  页面内容（只显示该平台+已授权功能）             │
│ ├──────────┤ │                                               │
│ │ TikTok 🔒│ │                                               │
│ └──────────┘ │                                               │
│  ─────────   │                                               │
│  通用：       │                                               │
│  任务日志     │                                               │
│  设置(全局)   │                                               │
└──────────────┴───────────────────────────────────────────────┘
```

### Alibaba 平台导航（只保留必要）

```
商品准备
  概览
  店铺与登录（Cookie/店铺切换）
  采集箱
  类目与属性（搜索/叶子/必填属性/历史类目）
  产品分组（店铺分组同步）
刊登
  新建/编辑商品
  草稿与待发布
  在线商品
  发布/审核状态
内容
  AI 文案（标题/关键词/详情/公司介绍/FAQ）
  AI 图片（白底/去水印/翻译/营销图）
  内容审查
价格与贸易
  阶梯价 / FOB 报价
  MOQ / 包装 / 重量
  贸易条款 / 交期 / 样品
运行
  任务与日志
  设置（API/授权/图片/平台参数）
```

### 未授权平台态

- 左侧平台显示 🔒，可点击
- 主区域显示：平台名称、可开通的功能清单、按钮「去授权网站开通」
- 不显示该平台任何真实页面，避免误操作

## 5. 功能矩阵：通用 vs 平台专属

| 能力 | 通用 | Alibaba 专属 | 速卖通专属 |
|---|---|---|---|
| 登录/Cookie | ✅ 店小秘统一 |  |  |
| 相册/COS/图床 | ✅ |  |  |
| AI 文案/图片 | ✅ | 关键词/公司介绍/贸易 FAQ | 营销图 1:1/3:4 |
| 内容审查 | ✅ | Alibaba 违禁词/认证检查 | SMT 违禁词 |
| 类目与属性 | 框架通用 | ICBU 类目树/属性/schema | smt 类目/属性模板 |
| 价格 | 计算引擎通用 | 阶梯价/FOB/MOQ/批发 | SKU 价/区域调价 |
| 变种 | 框架通用 | SKU 属性/定制 | aeop 变种语义 |
| 发布 | 队列通用 | op=1/2/3/5 + 审核 | add.json 直接发布 |
| 日志/更新/授权 | ✅ |  |  |

## 6. Alibaba 版要删除/隐藏的（避免混乱）

**删除**：速卖通专属的 `smtProduct/*`、`aeopAeProduct*` 字段、店铺模板继承（SMT 语义）、区域调价、营销图 1:1/3:4、欧盟资质、SMT 违禁词、SMT 变种属性语义、发布队列的 SMT 状态。

**保留**：店小秘 Cookie/账号、相册/COS、AI 文案、AI 图片、内容审查、图片工作台、任务日志、更新器、通用设置、Alibaba 的类目/属性/分组/报价/审核。

## 7. 店小秘接口互通审计（实测）

| 接口 | Alibaba 账号可用 | 结论 |
|---|---|---|
| `/api/userIn.json` | ✅ | 通用，可复用 |
| `/api/album/index.json`、`/album/list.json` | ✅ | 通用相册 |
| `/api/album/toUploadPicFromNet.json` | ✅（参数校验可达） | 通用上传 |
| `/api/cos/getSign.json` | ✅（POST 可达） | 通用图床 |
| `/api/alibabaPhotoBank/selectImgList.json` | ✅ | Alibaba 图片库 |
| `/api/alibabaIcbuShopInfoSync/list.json` | ✅（含运费模板） | Alibaba 店铺 |
| `smtProduct/*`、`smtAdjustPrice/*`、`smtAttributeTemplate/*` | ❌ 不适用 | 删除 |

结论：**账号/相册/图床可以复用；商品、类目、价格、发布必须按平台适配器分离**。

## 8. 分阶段实施

| 阶段 | 内容 | 产出 |
|---|---|---|
| P1 平台壳 + 授权 | PlatformAdapter/registry、左侧平台栏、授权 token 校验、未授权页 | 可切换平台，按授权显隐 |
| P2 Alibaba 精简版 | 删除 SMT 专属页面/字段；Alibaba 导航按上图 | Alibaba 可用完整刊登 |
| P3 通用能力下沉 | AI 文案/图片/审查/日志 与平台解耦 | 第二平台接入成本降低 |
| P4 第二平台 | AliExpress 适配器（迁移现有代码） | 双平台 |
| P5 授权网站 | 套餐/功能包/换机/续期 + 签名下发 | 商业化闭环 |

## 9. 决策点（需要你确认）

1. 授权粒度：只按「平台」卖，还是「平台 + 功能包」？（建议平台 + 可选功能包，如 AI 图片包）
2. 离线宽限期：7 天是否合适？
3. 左侧是「平台切换」还是「平台 + 通用区」？（设计图采用后者）
4. 旧速卖通客户升级后，是否保留旧界面过渡（建议保留，按授权显示）。

---

# 授权网站（已上线）与软件登录流程

## 1. 站点

| 入口 | URL |
|---|---|
| 用户端（注册/登录/购买/授权/状态） | https://ali.moneyfly.club/license/ |
| 管理后台 | https://ali.moneyfly.club/license/admin.php |
| 软件拉取授权 | POST https://ali.moneyfly.club/license/api/license/entitlements |
| 软件登录 | POST https://ali.moneyfly.club/license/api.php?action=software_login |

管理后台账号在部署配置 `site/license/config.php`（`admin_pass` 已改成随机强密码）。

## 2. 自动开通流程

```
用户注册/登录 → 选套餐 → 下单
   → 易支付（与 new-api 同一套易支付配置）扫码支付
   → pay/notify.php 验证签名 → 订单 paid
   → 自动签发 license token（含平台+功能包+到期）
   → 软件「登录授权账号」或后台刷新 → 自动激活对应平台
```

- 支付回调自动开通；后台也支持「标记已付并开通」（线下转账/客服补单）
- 管理员可编辑套餐价格/天数/平台/功能，修改后新订单生效
- 管理后台可看：用户、订单、收入、有效授权、24h 心跳、吊销授权

## 3. 机器码策略

- 软件首次启动（无有效授权）弹出「登录授权账号」：邮箱 + 密码 + 自动带机器码
- 服务器校验账号与订单 → 未绑定则**自动绑定该机器**（1 台）→ 返回绑定机器码的签名 token
- 已绑定其他机器 → 提示「请在用户端解绑」；用户端可自助解绑，**30 天限 1 次**
- 不想登录：用户端可「复制 Token」手动粘贴到软件（设置 → 平台与功能包授权）
- 离线：token 本地验签，7 天宽限，不联网也能用
- 安全：token HMAC 签名 + 机器码 + 到期；改机器码或改平台都验不过

## 4. 用户端页面

- 注册 / 登录 / 退出
- 套餐卡片（价格、平台、功能包、购买/续费）
- 我的授权：剩余天数、到期日、已开通平台、机器码绑定/解绑、Token 复制
- 软件运行状态 & 刊登产品（软件心跳上报后显示；接口已就绪）
- 订单记录

## 5. 管理后台

- 概览：用户数、订单数、已支付、收入、有效授权、24h 心跳
- 套餐管理：新增/编辑价格、天数、平台、功能、启用停用
- 订单：查看全部订单、手动开通
- 用户：授权数、到期、已付订单
- 授权：机器码、平台、到期、吊销

## 6. 待接入

1. 易支付配置（`epay_gateway/pid/key`）——填上即可真实收款；new-api 后台用的是同一套易支付参数，可直接复制
2. 软件心跳上报（服务端已就绪，客户端上报平台状态与商品列表待加）
3. 邮箱验证 / 找回密码（当前邮箱+密码注册，无验证邮件）

## 7. 授权中心 v2（用户端 + 管理后台）

### 用户端（https://ali.moneyfly.club/license/）

- 未登录：品牌介绍 + 登录/注册卡片（邮箱 + 密码，注册即登录）
- 登录后左侧导航：
  - **概览**：剩余天数、已开通平台、订单数、刊登产品数、授权摘要、最近订单
  - **套餐购买**：按平台/功能包展示价格，直接下单
  - **我的授权**：到期、平台、机器码绑定/解绑（30 天限 1 次）、Token 复制
  - **软件运行状态**：心跳时间、各平台状态与今日处理量
  - **刊登产品**：软件上报的商品列表（平台/标题/状态/更新时间）
  - **订单记录**、**帮助**

### 管理后台（/license/admin.php）

- 概览：用户/订单/已支付/收入/有效授权/24h 心跳
- 套餐管理：新增、编辑价格/天数/平台/功能
- 订单：查看、手动开通
- 用户 / 授权：查看、吊销
- **支付设置**（本次新增）：
  - 易支付网关、商户 PID、商户密钥、支付方式（支付宝/微信）
  - 允许模拟支付（仅测试）
  - 用户端公告
  - 显示回调地址 `https://ali.moneyfly.club/license/pay/notify.php`

支付参数保存在 settings 表，后台修改即时生效；new-api 后台同一套易支付参数可直接复制。

---

# 域名与兼容说明（2026-11-22）

- **原授权中心（保留，未改动业务）**：https://license.moneyfly.club/ 与 /admin
  - 原授权数据、页面、API（POST /api/v1/license/verify）全部保留
  - 原后台顶部导航新增两个入口：「套餐 / 支付」「用户端」（链接到 /shop）
- **新增功能（独立应用，不碰原数据）**：https://license.moneyfly.club/shop/
  - 用户端：注册/登录、套餐购买、我的授权、运行状态、产品、订单
  - 管理端：https://license.moneyfly.club/shop/admin.php（套餐价格、订单、用户、授权、易支付、SMTP）
- 旧路径 https://ali.moneyfly.club/license/ 已 308 跳转到 /shop
- 桌面客户端默认授权服务器：https://license.moneyfly.club/shop

## 8. 嵌入原后台（2026-11-22 最终形态）

原授权中心（license.moneyfly.club/admin）**保持原样**，仅在其导航中新增 5 个页内入口，
页面使用原后台同一套 layout/CSS/登录态，不再跳转外部后台：

| 菜单 | 路径 | 功能 |
|---|---|---|
| 套餐管理 | /admin/shop-packages | 列表、行内改价格/天数/启用、新增、删除 |
| 支付设置 | /admin/shop-pay | 易支付参数、公告、SMTP 邮件、注册验证开关 |
| 套餐订单 | /admin/shop-orders | 订单列表、标记已付并自动开通 |
| 套餐用户 | /admin/shop-users | 注册用户、授权数、到期 |
| 套餐授权 | /admin/shop-licenses | 套餐授权列表、吊销 |

用户端仍在 https://license.moneyfly.club/shop/（品牌页/登录/购买/我的授权/运行状态/产品）。

部署文件（原应用副本，便于重装）：
- `deploy/legacy-app-patch/shop_admin.php` → `/www/server/ae-license/app/`
- `deploy/legacy-app-patch/views/shop_*.php` → `/www/server/ae-license/app/views/`
- `deploy/legacy-app-patch/apply.py` → 给 `public/index.php` 加路由、给 layout 加菜单（自动备份）

已验证：套餐价格 99→119→99 保存成功；嵌入页面渲染与原后台风格一致。

## 9. 最终地址结构（2026-11-22）

- **用户端**：https://license.moneyfly.club/ （根路径，注册/登录/购买/我的授权/运行状态/产品）
- **管理后台**：https://license.moneyfly.club/admin （融合模式：原后台 layout + 新功能菜单）
  - 新增菜单：套餐管理 / 支付设置 / 套餐订单 / 套餐用户 / 套餐授权
- 原老软件接口：https://license.moneyfly.club/api/v1/license/verify （未动）
- 新软件接口：https://license.moneyfly.club/api/license/entitlements （nginx 映射到内嵌应用）
- /shop 根路径 301 到 /（内部文件仍由 nginx 映射使用，不再对外暴露）
- 桌面客户端默认授权服务器：https://license.moneyfly.club

### 修复记录（菜单不显示）

原应用实际渲染使用 `app/views/layout.php`（`app/layout.php` 是旧副本）。
第一次只改了后者，导致新菜单没出现。现已修补 `views/layout.php`，验证后台左侧可见：
套餐管理 / 支付设置 / 套餐订单 / 套餐用户 / 套餐授权 / 用户端。

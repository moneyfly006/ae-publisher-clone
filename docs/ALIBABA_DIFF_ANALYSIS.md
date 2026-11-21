# 阿里巴巴国际站刊登软件：差异分析与改造方案

> 目标：复用 AuroraList（速卖通/店小秘版）约 70% 的能力，改造成 Alibaba.com（国际站）刊登工具。
> 本文先回答三件事：能不能做、和速卖通差在哪、代码/UI 要怎么改。

---

## 一、可行性：先确定接入方式

| 方式 | 前提 | 稳定性 | 建议 |
|---|---|---|---|
| A. 官方开放平台 API（ICBU） | 需要 ISV 应用审核 + 卖家授权；商品发布类接口权限需申请 | 高 | 有资质就选它 |
| B. 卖家中心浏览器自动化（Playwright + 已登录 Chrome） | 只需卖家账号登录态 | 中（页面改版会失效） | 没有 ISV 时的现实方案 |
| C. 半自动：软件生成内容 + 人工在后台粘贴/保存 | 无 | 高 | 作为过渡版 |

**现状**：`seller.alibaba.com/product/publish.htm` 未登录会 302 到登录页，无法直接抓包编辑器请求。
需要你提供一个**已登录卖家中心的环境**（Chrome profile 或 cookies + 一个产品编辑页 URL），
才能抓取 `publish` 页面的真实接口与字段。

建议路线：**B 先跑通（草稿保存）→ 有 ISV 后再切 A**。

---

## 二、产品数据模型差异（核心）

| 维度 | 速卖通（店小秘） | Alibaba.com 国际站 |
|---|---|---|
| 账号 | 店小秘 Cookie + 多店铺 | 卖家账号（一个供应商），多店铺概念弱 |
| 商品标识 | `id`（编辑接口 edit.json） | 后台产品 ID + 产品分组 group |
| 标题 | `subject` ≤128 字符 | 产品名称 ≤128 字符（英文必填） |
| 关键词 | 无独立字段（标题优化） | **keywords 3~5 个**，独立字段 |
| 类目属性 | `aeopAeProductPropertys`（attrNameId/attrValueId） | 类目属性 + **自定义属性**，属性集不同 |
| 价格 | 每个 SKU 一个 `skuPrice` | **阶梯价**：按数量区间报价（FOB），可多区间 |
| 起订量 | 无（单件起） | **MOQ**，按产品/SKU |
| 贸易条款 | 无 | **FOB / CIF / EXW 等 + 港口 + 付款方式（T/T、L/C）+ 交期** |
| 样品 | 无 | 样品价格、样品交期、是否支持样品 |
| 包装物流 | 重量/尺寸 + 运费模板 | 包装尺寸/重量 + **20GP/40HQ 装柜量** |
| 认证 | 欧盟资质等 | **CE/ISO 等认证**、定制能力（logo/包装） |
| 图片 | 主图 6 + 营销图 1:1/3:4 | 主图 6（1:1，建议 ≥1000px，白底无 Logo/水印）+ 详情图 |
| 视频 | 主图视频 | 产品视频 1 个（时长/大小限制） |
| 详情 | HTML + 图片 | PC + 无线详情，模块化图文（同样需要 HTML/图片） |
| 发布 | 保存/直接发布，进入审核 | **提交审核 → 审核通过才上线**；有「审核不通过」原因 |
| 产品类型 | 普通商品 | **RTS（现货）** vs **询盘/定制**，字段不同（RTS 要库存/发货时效） |
| 多语言 | 英文为主 | 英文必填，可多语言翻译 |

---

## 三、发布流程（状态机）

```
草稿 draft
  → 提交审核 pending_audit
     → 审核通过 online
     → 审核不通过 audit_fail（带原因，需修改后重提）
  → 下架 offline
```

与速卖通最大的不同：**没有「发布即上线」**，必须过平台审核；软件要能拉取审核失败原因并支持重提。

---

## 四、现有代码改造清单

### 4.1 可直接复用（70%）

- `services/text_pipeline.py`、`services/__init__.py`：AI 文案（标题/详情/SKU/估重）
- `services/image_ops.py`、`image_pipeline.py`、`image_jobs.py`、`image_validation.py`：白底/去水印/翻译/精细化/营销图/压缩/webp
- `services/pricing.py`：改造成本模型（增加阶梯价/MOQ）
- `services/queue.py`、`progress.py`、`history.py`、`edited.py`：队列/进度/历史/去重
- `core/updater.py`、`license.py`、`core/instances.py`：更新/授权/多开
- `ui/app.py` 的框架、主题、日志、工作台、图片预览

### 4.2 必须替换

| 文件 | 现状 | 改成 |
|---|---|---|
| `integrations/dianxiaomi.py` | 店小秘私有 API（edit.json/add.json） | `integrations/alibaba.py`：登录态、类目/属性、草稿保存、提交审核、审核结果 |
| `services/api_save.py` | 速卖通 payload（`aeopAeProduct*`） | `services/alibaba_payload.py`：Alibaba 字段（keywords/MOQ/阶梯价/贸易条款/认证/样本） |
| `services/product_data.py` | 解析 edit.json | 解析 Alibaba 产品 JSON/HTML |
| `services/customs_metadata.py` | 海关属性（速卖通专用） | 删除或改为 Alibaba 认证字段 |
| `services/combo.py`、`variants` | 速卖通变种语义 | Alibaba SKU + 定制选项 |

### 4.3 新增模块

- `services/trade_terms.py`：贸易条款/港口/付款方式/交期/样品
- `services/keywords.py`：3~5 关键词生成与合规检查
- `services/audit_status.py`：审核状态与失败原因拉取
- `integrations/alibaba_web.py`：Playwright 自动化（无 API 时）

---

## 五、UI 界面变化（12 页 → Alibaba 版）

| 现页面 | 新页面 | 变化 |
|---|---|---|
| 概览 | 概览 | KPI 改为：草稿 / 待审核 / 审核中 / 在线 / 审核不通过；保留成本与今日 |
| 店铺与模板 | **账号与模板** | 改为 Alibaba 卖家登录态；模板继承类目/属性/贸易条款 |
| 类目与属性 | 类目与属性 | 属性集替换；新增认证（CE/ISO）、定制能力 |
| AI 文案 | AI 文案 | 标题 128 字符；新增 **关键词 3~5**；详情结构不变；SKU 命名保留 |
| 内容审查 | 内容审查 | 保留；增加 Alibaba 违禁词/认证检查 |
| AI 图片 | AI 图片 | 主图 6 张 1:1；去掉营销图 1:1/3:4（Alibaba 无此要求）；保留视频 |
| 价格 | **报价与贸易条款** | 阶梯价（数量区间）、MOQ、FOB/CIF/EXW、港口、付款方式、交期、样品 |
| 变种与库存 | **SKU 与定制** | SKU 属性 + MOQ + 定制项（logo/包装）；RTS 库存 |
| 发布队列 | **草稿与审核队列** | 草稿/待审核/审核失败/在线；重提审核 |
| 任务与日志 | 任务与日志 | 保留，增加审核结果日志 |
| 图片工作台 | 图片工作台 | 保留（裁剪/白底/翻译/对比） |
| 设置 | 设置 | API Key/图床/授权保留；新增 Alibaba 连接设置 |

新增建议：**关键词库**页（可选，先并入 AI 文案）。

---

## 六、分阶段实施

### P0（1~2 天）抓包与字段确认
- 提供已登录卖家中心环境 + 1 个产品编辑页
- 抓 `publish` 页面的：类目属性、草稿保存、提交审核、审核结果接口
- 输出《Alibaba 字段清单》并冻结 payload 结构

### P1（1 周）连接层 + 草稿保存
- `alibaba.py` 登录态检测、类目/属性读取
- 一个商品：读取 → 改标题/图片 → 保存草稿（不提交审核）

### P2（1 周）AI 内容 + 报价
- 复用文案/图片；新增关键词、阶梯价、MOQ、贸易条款
- 批量生成 → 草稿队列

### P3（1 周）审核闭环
- 提交审核、拉审核失败原因、重提
- 日志/重试/断点

### P4（按需）官方 API
- 有 ISV 后替换自动化层，接口不变

---

## 七、现在需要你提供

1. **Alibaba 卖家账号的登录态**（Chrome profile 或 cookies），或允许我用你已登录的 Chrome 抓包
2. **一个已发布产品**的后台编辑页 URL（用于对比字段）
3. **1~2 个目标类目**（例如：包装机械、食品机械）
4. 是否已有 **ISV 应用/开放平台权限**
5. 产品类型以 **RTS 现货** 还是 **询盘定制** 为主

拿到 1~3 后，我就能把 `publish` 页的真实请求抓出来，给出精确的字段映射和第一版改造代码。

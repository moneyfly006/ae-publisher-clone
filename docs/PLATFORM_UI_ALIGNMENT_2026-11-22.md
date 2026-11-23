# 本轮更新：自动采集箱

最新主流程、页面归属与真实验收结果见 [自动采集箱验收](AUTO_COLLECTION_ACCEPTANCE_2026-11-22.md)。以下为上一轮单品编辑对照记录；常规启动已无需手动载入商品。

# 平台编辑页面与软件功能对照

以速卖通原有的「商品准备 → 内容处理 → 发布与运行 → 工具与设置」分组及 Flet 主题组件为基准，保留平台自己的字段、限制和保存端点。本次处理的对象为店小秘中已存在的采集箱、待发布或在线商品；平台创建新商品、创建新变种组合、账号授权仍使用店小秘原生入口。

## 核对依据

| 平台 | 实际编辑页 | 取证目录 | 保存格式 |
|---|---|---|---|
| Alibaba | `/web/alibabaIcbuProduct/add` | `build/alibaba-editor-reference/` | `/alibabaIcbuProduct/add.json`，ZIP 内 `alibabaSave.txt` |
| Amazon | `/web/amazon/add` | `build/amazon-editor-reference/` | 保留既有 Amazon SP 保存服务及 39 键结构 |
| TikTok | `/web/tiktokProduct/add` | `build/tiktok-editor-reference/` | 保留 TikTok 独立保存服务与站点规则 |
| Shopee 全球 | `/web/shopeeGlobal/add` | `build/shopee-editor-reference/` | `/shopeeGlobalProduct/add.json`，ZIP 内 `shopeeGlobalSave.txt` |
| Shopee 站点 | Shopee 共用编辑器的站点分支 | 共用 JS `utils-COr9v2vt.js` | `/shopeeProduct/add.json`，ZIP 内 `shopeeSave.txt` |

各目录包含实际 HTML、JS、页面文本和接口路径。原始接口响应及账号资料在忽略的 `build/` 下，不进入文档。

证据链：实际编辑页和 JS → 字段分组、限额及 ZIP 文件名 → 独立客户端/服务与对应页面 → 离线契约测试及本地构建。字段在当前账号下不能实读时单独记录，不将模拟测试记为实店通过。

## Alibaba

| 店小秘功能 | 软件页面 | 后端字段 / 行为 |
|---|---|---|
| 店铺、登录 | 连接与店铺 | `alibaba_cookies`、`alibaba_shop_id`；独立客户端 |
| 草稿、在线、搜索、商品 ID | 采集箱商品 | 原生列表/详情接口；选择后预读商品 |
| 分类、系统属性、自定义属性 | 类目与属性 | `categoryId`、`fullCid`、`attributes`；保留属性身份，保存前校验 |
| 分组、来源 | 公司与商品资料 | 当前商品 `groupId`、`sourceUrl` |
| 国内/海外现货、发货地 | 公司与商品资料 / 高级设置 | `publishType`、`shipmentPlace`、`warehouseType`、`overseaCountryRegion`；已有编码原样读取 |
| 标题、关键词、描述 | AI 文案 / 当前商品文案 | `subject`、`keywords`、`textDesc` |
| 行业提示词 | AI 文案 / 生成规则与预设 | Alibaba 独立提示词；生成时构造独立 AppConfig |
| 公司介绍、FAQ、默认资料 | 公司与商品资料 | `companyDesc`、`companyFaqDesc`；本地资料明确标记 |
| 主图、详情图 | AI 图片 / 当前商品图片 | `mainImage`、`draftImgUrl`、`detailImage`；支持显式 AI 处理后回填 |
| 定价方式、阶梯价、FOB | 价格与贸易 | `priceType`、`bulkDiscountPrices`、`sourcingTrade` |
| 包装、重量、库存、交期 | 价格与贸易 / 当前商品 | `wholesaleTrade` 的对应项；交期最多 3 档，保留未编辑项 |
| 样品 | 价格与贸易 / 当前商品 | `marketSample`、`marketSamplingQuantity`、`marketSamplingPrice` |
| 规格单价、SKU | 价格与贸易 / 当前商品 | `productSku.special_skus`；保留规格维度、图片和仓库结构 |
| 检查、评分、保存、发布、排期、批量 | 保存与发布 | 原有门禁和流水线；所有步骤记录到事件总线及日志文件 |

当前商品编辑与批量默认资料分开，商品编辑按 ID 隔离。显式修改在最终保存包中优先应用；批量任务不携带其他商品的本次修改。

## Amazon

| 店小秘功能 | 软件页面 | 后端字段 / 行为 |
|---|---|---|
| 店铺、站点、登录 | 连接与店铺 | Amazon 独立登录、店铺及站点配置 |
| 商品列表及状态 | 采集箱商品 | 原生列表、详情和发布状态读取 |
| 分类、必填属性 | 类目与属性 | `categoryId`、`categoryBrowsePath`、`productAttr` |
| UPC/EAN/GTIN、GPSR | 类目与属性 / 产品标识与安全资料 | `standardProductType`、`standardProductId`、GPSR 对应字段 |
| 价格、促销日期、状况、配送 | 价格与物流 | `standardPrice`、`saleSalePrice`、促销日期、状况与配送字段 |
| SKU、品牌、制造商、数量 | SKU 与库存 | 独立编辑并进入同一保存包 |
| 标题、商品亮点、来源 | AI 文案 | `title`、`titleDifferentiation`、`sourceUrl` |
| 五点描述 | AI 文案 | 一个文本区、一个按钮、一个内置提示词，一次生成五条；保存为 `bulletPoint` |
| 描述、搜索词 | AI 文案 | `description`、`searchTerms` |
| 主图、附图 | 商品图片 | 主图 URL 和最多 8 张附图；支持图片处理与图床回填 |
| 保存、发布、修复、结果 | 发布与结果 | 保留既有确认、失败自动修复、状态查询与回读服务 |

旧商品的第 6–10 条卖点不会因界面合并而丢失；新入口固定生成五条。保存字段契约仍为 39 键。

## TikTok

| 店小秘功能 | 软件页面 | 后端字段 / 行为 |
|---|---|---|
| 店铺、站点、登录 | 连接与店铺 | TikTok 独立配置 |
| 分类、属性、品牌、资质 | 类目与属性 | 保留站点类目、属性建议和资质逻辑 |
| 价格、包装重量尺寸、仓库、来源 | 价格与物流 | 对应 TikTok 原生字段 |
| 刊登类型、SKU、库存、变种单价 | SKU 与库存 | `variant_edits` 按稳定 ID 或唯一 SKU 匹配；未编辑变种保留 |
| 标题、HTML 描述、US 卖点/关键词 | AI 文案 | 按站点语言；提示词可编辑并自动保存 |
| 主图、视频 | 图片与视频 | 原有媒体字段、图片处理与回填 |
| 保存、发布为草稿、提交 | 保存与发布 | 保留 op/saveMode 组合、检查、确认及结果 |

14 号旧路由保留兼容；日常导航使用独立功能页。独立服务不调用速卖通写入管线。

## Shopee

| 店小秘功能 | 软件页面 | 后端字段 / 行为 |
|---|---|---|
| 商家账号 / 站点店铺 | 连接与店铺 | 全局/站点模式、独立 Cookie、账号店铺过滤 |
| 列表、状态、搜索、ID | 采集箱商品 | 原生 `pageList`、`edit`；保留字符串 ID |
| 分类及属性 | 类目与属性 | `categoryParentId` 下钻；属性按原生结构写回 |
| 价格、包装、重量、状况、备货 | 价格与物流 | 全球与站点各自原生字段；平台限额参与校验 |
| 运输渠道 | 价格与物流 / 运输渠道 | `logisticsInfo` / `logistics`，读取后由用户明确切换 |
| Parent SKU、已有变种及库存 | SKU 与库存 | `itemSku`、`variationListStr` / `variationsStr`；稳定 ID 精确修改 |
| 标题、描述、AI | AI 文案 | `itemName` / `name`、普通/新版描述；按站点语言生成 |
| 图片、尺码图、视频 | 商品图片与视频 | 区分全球和站点图片分隔符；图片处理后回填；保留视频原有字段 |
| 草稿、待发布、提交、结果 | 保存与发布 | ZIP 原生保存；重新读取与校验；回读失败与平台发布失败分别显示 |

多仓商品禁止只改总库存而覆盖仓库分配。新版描述保留原结构，AI 改写会明确更新描述；所有真实提交都受原有核心授权与本地验收模式保护。

## 验证范围与实际限制

- 代码检查、真实 Flet 控件构建、两主题测试、切页状态、保存包、原回归、图片处理隔离测试和本地构建均有独立记录。
- `tools/test_offline.py` 已纳入新增平台测试及 Alibaba 原服务回归。付费 AI 与真实店铺提交不在离线测试中执行。
- 当前提供账号只显示 Amazon 店铺，Alibaba、TikTok、Shopee 的实际商品编辑/发布无法在该账号完成。读取空列表成功不等于实店发布通过。
- Shopee 全球根类目与下钻已实读通过；无店铺时全球属性未得到可用 JSON，站点类目接口拒绝。相关 UI 会显示失败并阻止不完整保存，完整实店验收仍待具备店铺数据。
- 海外仓、特殊类目属性、新变种组合、资质文件上传、视频实际上传和平台特定增值工具保留店小秘入口。已有对应原始字段在服务中透传；本报告不将这些操作声称为已完成原生编辑器的全量替代。

进度与尚待实店验证的项目见 [任务清单](UI_REDESIGN_TASKS.md)。

## 本地验收产物

- 最终包：`dist/verified-ui-workflows-final-20261122-142212/AuroraList.app`。签名检查及打包程序自检通过。
- 统一离线回归 1082 项通过，0 失败；32 个功能页、223 个按钮回调遍历无异常。
- 亮/暗主题合计 47 个真实渲染检查通过，每项同时保存 1280 与 1024 宽度截图。
- 结构化交付记录：[ui-workflows-delivery-verification.json](ui-workflows-delivery-verification.json)。

- 原生包启动：独立无凭据配置下进程正常运行，未产生 `error.log`；系统截图工具超时，未取得原生窗口截图。

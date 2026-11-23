# Alibaba.com（店小秘 ICBU）刊登规则完全说明

> 依据：店小秘前端 `service-CBy6SDbz.js` / `useBasicStore-*.js` / `ShopCategoriesModal-*.js` /
> `add-*.js` 逆向 + 真实账号接口实测（2026-11-21，shopId 9466975）。
> 目标：说明每个接口要什么、规则是什么、AI 用在哪、哪些绝不能交给 AI。

---

## 1. 数据链路

```
列表(collect) → 编辑页 edit.json → 类目/属性/schema → 表单模型
   → 组 JSON(alibabaSave.txt) → zip → POST add.json (op) → 草稿/待发布/在线
```

保存与发布共用 `add.json`，**靠 op 区分动作**。

---

## 2. 接口全目录（已逐个验证）

| # | 用途 | 方法 | 路径 | 参数 | 返回 |
|---|---|---|---|---|---|
| 1 | 商品详情 | GET | `/api/alibabaIcbuProduct/edit.json` | `id` | `data.product`（110 字段） |
| 2 | 保存/发布 | POST | `/api/alibabaIcbuProduct/add.json` | multipart：`file`=zip(`alibabaSave.txt`)、`op` | `{code,msg,data:{productId,msg}}` |
| 3 | 列表 | GET | `/api/alibabaIcbuProduct/list.json` | `pageNo/pageSize/dxmState/shopId/searchValue`（分页参数是 **pageNo**；传 page 会永远返回第 1 页，2026-11-22 实测） | `data.page.list` |
| 4 | 类目根/子级 | GET | `/api/alibabaIcbuCategory/list.json` | `pcid`（子级） | 类目数组 |
| 5 | 类目搜索 | POST | `/api/alibabaIcbuCategory/searchCategory.json` | **`category`**（关键词） | 叶子类目数组（含 nodePathId/isleaf） |
| 6 | 类目详情 | POST | `/api/alibabaIcbuCategory/getByCategoryId.json` | `categoryId,shopId` | 类目（nodePathId/level/isleaf） |
| 7 | 历史类目路径 | POST | `/api/alibabaIcbuCategory/getHistoryCategory.json` | `categoryId` | `["202107201","100005663",...]` |
| 8 | 类目属性 | POST | `/api/alibabaIcbuCategory/attributeList.json` | `categoryId` | 属性列表（required/inputType/skuAttribute/values） |
| 9 | 类目模块 | POST | `/api/alibabaIcbuCategory/schemaList.json` | `categoryId` | `fieldId` 列表（companyDesc/detailImage/saleType/scPrice/tariffsHsCode/textDesc…） |
| 10 | 店铺列表 | POST | `/api/alibabaIcbuShopInfoSync/list.json` | `shopId` | 店铺 |
| 11 | 店铺选项 | POST | `/api/alibabaIcbuShopInfoSync/getSpecialInfo.json` | 空 | `country`(257)、`unit`(92)、`province`(31) |
| 12 | 运费模板 | POST | `/api/alibabaIcbuShopInfoSync/freightList.json` | `shopId` | `freightTemplateList[{templateId,templateName}]` |
| 13 | 产品分组 | GET | `/api/alibabaIcbuShopInfoSync/syncProductGroupNoAll.json` | `shopId` | `alibabaIcbuProductGroupList[{groupId,groupName,isleaf,parentId}]` |
| 14 | 变种模板名 | POST | `/api/variationTemplate/com/alibaba/getNameListByCategory.json` | `categoryId` | 变种属性名（部分类目为空） |
| 15 | 变种模板详情 | GET | `/api/variationTemplate/com/alibaba/getVariationTemplateById.json` | `id` | 模板 |
| 16 | 引用产品 | GET | `/api/alibabaIcbuProduct/quoteEdit.json` | `id,singleCopy` | 商品 |
| 17 | 违禁词 | GET | `/api/bannedWord/alibabaCheckBannedWord` | `text` | 检查结果 |
| 18 | 海外区域 | GET | `/api/alibabaIcbuProduct/getOverseaCountryRegionList.json` | `shopId` | 区域 |
| 19 | 同步发货地 | GET | `/api/alibabaIcbuProduct/syncShipmentPlace.json` | `shopId` | 发货地 |
| 20 | 车型/配件 | POST | `/api/alibabaIcbuCategory/getMotorAttr.json` 等 | 车型参数 | 机械类目一般不用 |

> 所有类目/属性/schema 接口都是 **POST form**；详情/列表是 GET。

---

## 3. 保存协议：zip + op

```
POST /api/alibabaIcbuProduct/add.json
multipart/form-data:
  file = ZIP，内含 alibabaSave.txt（UTF-8 JSON）
  op   = "1" | "2" | "3" | "5"
```

| op | 动作 | 结果 |
|---|---|---|
| **1** | 保存（编辑/草稿） | `您的产品编辑成功！` / 保存到草稿或待发布 |
| **2** | 发布（立即）/ 在线更新 | 提交平台，进入发布中/在线 |
| **3** | 保存并移入待发布 | 进入「待发布」 |
| **5** | 定时发布 | 进入「定时发布」 |

保存 JSON 字段：`op,id,productId,productIdDecrypt,isSmartEdit,shopId,categoryId,publishType,sourceUrl,fullCid,subject,keywords,groupId,motorIds,attributes,mainImage,draftImgUrl,textDesc,detailImage,shipmentPlace,warehouseType,overseaCountryRegion,priceType,bulkDiscountPrices,wholesaleTrade,sourcingTrade,productSku,salePkgDesc,logisticsProperty,marketSample,marketSamplingQuantity,marketSamplingPrice`。
JSON 数组/对象统一以**字符串**形式放在字段里。

---

## 4. 价格模型（三种，必须匹配类目选项）

| priceType | 名称 | 关键字段 |
|---|---|---|
| `sku_price` | 根据规格设置价格 | `productSku`（每个 SKU 价格） |
| `ladder_price` | 根据数量设置价格 | `bulkDiscountPrices=[{start_quantity,price}]`；`wholesaleTrade.min_order_quantity`=第一档起点 |
| `fob_price` | 单一区间价格 | `sourcingTrade={fob_unit_type,fob_currency,fob_min_price,fob_max_price,min_order_quantity,min_order_unit_type,sale_type}` |

销售方式 `saleType`：`normal`（按件卖）/ `batch`（批发，需 `batch_number`）。
贸易信息 `wholesaleTrade`：`unit_type、sale_type、batch_number、tariffsHsCode[{toCountry:"US",hsCode}]、min_order_quantity、inventory[{warehouseCode:"CN_LOCAL_01",value}]、package_size:"LxWxH"、weight、deliver_periods、shipping_line_template_id`。
**FOB 模式下不写 tariffsHsCode**（前端逻辑：`tariffsHsCodeConfig && !fob && !overseaSpot`）。

---

## 5. 类目规则

1. 只能选**末级类目**（`isleaf=1`），否则发布失败。
2. 子级查询：`list.json?pcid=<父id>`；根列表不带参数。
3. 关键词搜索：`searchCategory.json` 参数名是 **`category`**，返回叶子结果与 `nodePathId`（如 `18/100005657/100005663/202107201`），保存用 `fullCid="18,100005657,100005663,202107201"`。
4. 必填属性来自类目 `required=1`，必须出现在 `attributes` 中（`attribute_id`=attrNameId，`value_id`=枚举 id，`value_name`=展示值）。
5. 类目模块（schema）决定这些字段是否要填：`companyDesc`、`companyFaqDesc`、`companyImage`、`detailImage`、`saleType`、`scPrice`、`tariffsHsCode`、`textDesc`。
6. 自定义属性：`attribute_id/value_id = -1`，`value_name` 自由文本。
7. 机械常见必填（实测）：Place of Origin / Model Number / Warranty / Power / Voltage / Weight(kg) / Machinery Capacity / Packaging Type / Automatic Grade / Filling Material 等。

---

## 6. 必填与校验矩阵

| 项 | 硬校验 | 说明 |
|---|---|---|
| 类目 | ✅ | 必须叶子 |
| subject | ✅ | 非空，≤128 字符 |
| keywords | ✅ | ≥1（建议 3~5，≤5） |
| mainImage | ✅ | ≥1（建议 4~6，≤6） |
| 价格 | ✅ | 按 priceType：阶梯价必须有区间；FOB 必须有 min/max/币种；SKU 价需 SKU 数据 |
| MOQ | ✅ | >0 |
| 包装尺寸/重量 | ✅ | `package_size` 三段、`weight>0`（平台提示「请先填写包装重量」） |
| 必填属性 | ✅ | 类目 required 全部存在 |
| schema 字段 | ✅ | 有 textDesc/detailImage 就必须填 |
| 分组 | 可选 | groupId 为空也能存 |
| 公司信息 | 可选 | 有 companyDesc 字段建议填 |
| HS | 条件 | 非 FOB、非海外现货才需要 |

---

## 7. AI 用在哪里（已定义 `services/alibaba_ai.py`）

| 字段 | AI 用法 | 输入 | 输出/护栏 |
|---|---|---|---|
| categoryId | 候选类目 | 标题+图片+用途 | 必须用 `searchCategory` 校验、必须是叶子 |
| subject | 生成/优化 | 原标题/参数 | ≤128 字符、不编造型号认证 |
| keywords | 生成 | 标题/类目 | 3~5 个英文词 |
| textDesc | 生成详情 | 参数/卖点 | 结构固定、禁止联系方式/促销 |
| mainImage/detailImage | 处理/生成 | 原图 | 白底/去水印/翻译/人机比例/合规 |
| attributes | 推断候选 | 标题/详情/图片 + 类目属性候选 | 只允许映射到平台枚举值，否则回退 Other/预设 |
| companyDesc / companyFaqDesc | 生成 | 公司预设 | 禁止编造认证与数字 |
| tariffsHsCode | 建议 | 品类/材质/用途 | 仅给候选，默认需确认 |
| weight / package_size | 估算 | 图片/同类设备 | 标记估算，可手改 |
| price / MOQ / unit / groupId | ❌ AI 不决定 | 规则/预设 | 成本倍率/阶梯/店铺分组 |

**绝不允许 AI 编造**：Model Number、Certification、Place of Origin、Warranty、Voltage、Power、Weight(kg)、Machinery Capacity（只能来自原文或预设）。

---

## 8. 当前实现状态

| 能力 | 状态 |
|---|---|
| 详情/列表/保存(op1/2/3/5) | ✅ 实测保存成功 |
| 类目树/搜索/历史/必填属性/schema | ✅ 实测 |
| 分组/运费模板/单位/国家 | ✅ 实测 |
| 三种价格类型 + 批发 + 运费模板 | ✅ 载荷已支持（FOB 干跑校验通过） |
| 必填属性自动补齐 | ✅ 预设 > Other/China > 首值 |
| 保存前校验 | ✅ 硬校验矩阵 |
| AI 触点定义/提示词/属性映射 | ✅ 模块 + 测试 |
| AI 批量流水线接入 Alibaba 保存 | ⏳ 下一步 |
| 审核结果拉取/重提 | ⏳ 待接口（发布后状态查询） |
| 12 页 UI 逐页替换 | ⏳ 进行中（设置页 Alibaba 已可用） |

## 9. 验证记录

- 保存：`op=1` → `您的产品编辑成功！`，回读标题/关键词/阶梯价/MOQ/包装/重量/属性一致。
- 类目：`category=包装机` → 32 个叶子；`100010668` 子级 27 个。
- 必填：泳池 5 项；灌装机 10 项；涂层机械 6 项。
- 自动补齐：Color=Blue（预设）、Material/Applicable People=Other。
- FOB 干跑：`fob_price` 载荷生成 + 校验 0 问题。

---

## 10. 发布/审核硬规则（2026-11-22 实测，详见 ALIBABA_PUBLISH_RULES.md）

`op=1` 保存成功 **不等于** 能发布；`op=2` 的真实规则：

1. **发布是异步的**：`data.code=0` 只代表已提交；随后 `edit.json.errMsg` 会给出审核结果。
   `PUB_BIZCHECK_PRODUCT_IN_AUDITING`（审核中）应判为「已提交成功」，重复提交会被平台拒绝。
2. **经营类目范围**：`PUB_BIZCHECK_CAT_PUB_RESTRICT` = 店铺经营大类未包含该类目，
   需卖家中心 → 店铺管理 → 管理公司信息 → 经营类目开通；软件用在线商品类目样本做预检+提醒。
3. **textDesc 只收纯文本且 ≤2000 字符/1000 汉字**；HTML（含 style/img/script）会被拒
   「不能输入HTML脚本」。保存前必须 `text_desc_plain()`。
4. **发货期必填**：`wholesaleTrade.deliver_periods=[{"quantity":N,"process_period":D}]`；
   缺失报「发货期信息未填写」。
5. **重量/长宽高/物流属性全填或全清**：只填重量尺寸不填 `logisticsProperty` 会被拒；
   普货示例 `general_cargo_0`，同时填 `shipping_line_template_id`。
6. **发布门禁**：`op=2` 要求刊登评分 ≥4.5（`services/alibaba_score.py`），
   评分覆盖类目属性/标题/关键词/详情/图片/视频/价格贸易/合规，不达标列出修复项。

真实结果：机械类目商品已提交并进入审核；泳池类目因经营类目未开通被平台拒绝（评分 4.8 仍拒）。
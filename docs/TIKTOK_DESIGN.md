# TikTok Shop 刊登 · 设计与实现契约（2026-11-22）

> 目标：把 AuroraList 的 TikTok Shop 平台从「未实现（implemented=False）」升级为
> 真正可用、可验证的刊登工作台：**采集箱商品 → 店铺/站点 → 类目与属性 → AI 文案 →
> 图片/视频 → 价格/库存/物流 → 预演 → 保存/移入待发布/发布**，并逐按钮验证。
>
> 本文件是**实现契约**：前端 JS 逆向结论 + 实测接口事实 + 模块接口 + UI 规范 + 验收标准。
> 所有 teammate 必须按此契约实现；接口签名不得随意更改（UI 与后端并行开发）。

---

## 0. 环境事实（2026-11-22 实测，只读）

| 项 | 值 |
|---|---|
| 店小秘账号（`.tmp-probe/cookies.txt`） | `QINGHEMAOYI` / puid `2728813` |
| 该账号已授权店铺 | 仅 Amazon 店铺 `9408005` QingHe + 手工订单；**没有 TikTok 店铺** |
| 本机浏览器另一个账号 | `wangmengruie` / puid `2447945`，仅速卖通 + Alibaba 店铺，也**无 TikTok 店铺** |
| TikTok 采集箱 | **0**（`getOfflineCounts.draftNum=0`，pageList totalSize=0） |
| TikTok 站点列表端点 | `/api/tiktokProduct/getL7ShopIdAndSite.json` **可用**，返回 23 个 L7 站点 + shopId=ALL |
| 类目树端点 | `/api/tiktokCategory/list.json` **无需店铺即可用**（`site` + `crossBorder=1` + `categoryParentId`） |
| 类目搜索 | `/api/tiktokCategory/searchCategory.json`（`category` 关键词）**可用**，GB "phone" 返回 73 条 |
| 类目属性 | `/api/tiktokCategory/attributeList.json`（`type=3`）**可用**，GB 叶子类目返回 10 条属性 |
| 站点限额 | `/api/tiktokProduct/getLimitBySite.json` **可用**（标题 25–255、描述 10000、主图 1–9、价格 0.01–1.8e8、库存 ≤99999） |
| 保存传输 | POST multipart `/api/tiktokProduct/add.json`：`file`=ZIP(内含 `tiktokSave.txt` UTF-8 JSON) + `op`(+可选 `saveMode`) **已实测**（空 payload 返回 `code=-1 产品未选择店铺或店铺无效！` → 传输格式正确） |
| 详情端点 | `/api/tiktokProduct/getByProductId.json`（`productId`）code=0 data=null（无 TikTok 商品）；`gettiktokProduct.json` 需真实 id |
| 写入门槛 | 无 TikTok 店铺时**所有写操作被服务端拒绝**（`产品未选择店铺或店铺无效！`）；UI 保存前置校验：`请选择商家账号 / 请选择分类 / 请选择图片` |

**结论（对验证范围的影响，必须写进最终报告）**：
- 站点选择、类目树/搜索/属性、限额、列表/计数、保存传输格式 = **可真实只读验证**；
- 「采集箱商品编辑 → 保存/发布」的**真实写入回环**需要账号先绑定 TikTok Shop。
  代码按「详情原样透传 + overrides」实现；绑定店铺后跑
  `tools/verify_tiktok.py --write-roundtrip` 即可完成真实回环验证（脚本先只读，写入需显式确认）。

Cookie 读取优先级（与 Amazon 一致）：`cfg.tiktok_cookies` → `cfg.manual_cookies` →
本机浏览器（`integrations.browser_cookies.resolve("dianxiaomi.com")`）。

---

## 1. 逆向结论：TikTok 刊登真实接口

Base：`https://www.dianxiaomi.com`（前端静态资源：`https://s13.dianxiaomi.com/dxm-web/2026-11/assets/`）

### 1.1 登录 / 店铺 / 站点

| 用途 | 方法 | 路径 | 参数 | 返回要点 |
|---|---|---|---|---|
| 登录态 | GET | `/api/userIn.json` | – | `data.shopMap`；TikTok 店铺 `platform=="tiktok"`，含 `id/name/marketplaceIds/shopSiteList/marketPlaceId` |
| **站点列表** | GET | `/api/tiktokProduct/getL7ShopIdAndSite.json` | – | `data.tiktokL7Site=["TH","VN","MY","PH","SG","ID","GB","DE","ES","IT","FR","IE","PL","NL","BE","CZ","AT","GR","PT","HU","JP","BR","MX"]`，`data.tiktokL7ShopId="ALL"` |
| 多店铺站点 | POST | `/api/multiShopProduct/tiktok/shopList.json` | – | `{siteShop, popShop}`（无店铺时均为 null） |
| 可发布站点 | POST | `/api/tiktokProduct/listWarehouse.json` / `getPublishableSite.json` | `shopId` | 无店铺时 `[]`（best-effort，失败不阻断） |
| 站点限额 | POST | `/api/tiktokProduct/getLimitBySite.json` | `site` | 见 §1.6；实测各站点返回同一套默认值 |

> **站点选择语义**：TikTok 是「店铺 × 站点」模型。
> - 有店铺：站点来自店铺 `shopSiteList`/`marketplaceIds`，发布时按站点建 `publishableShop`；
> - 无店铺（或全球模式）：回退到 23 个 L7 站点，用于**选类目 / 准备草稿 / 预演**，保存时服务端会要求店铺。
> UI 必须把「店铺（必填，用于保存）」和「站点（必填，用于类目/语言/币种）」分开展示，不能混为一个下拉。

### 1.2 商品列表 / 计数

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 列表 | POST | `/api/tiktokProduct/pageList.json` | `pageNo`、`pageSize`、`shopId`（`-1`=全部）、`dxmState`=`draft`/`offline`/`online`、`searchValue`、`site`（可选）、`categoryId`（可选） |
| 采集箱计数 | POST | `/api/tiktokProduct/getOfflineCounts.json` | `shopId` → `{draftNum, publishingTiming, waitPublish, publishFail, publishing}` |
| 在线计数 | POST | `/api/tiktokProduct/getOnlineCounts.json` | `shopId` → `{}`（无店铺时） |

`pageList` 返回 `data.page.list[]`（行字段：`id/idStr/productName/title/images/imgUrl/shopId/site/categoryId/categoryName/dxmState/updateTime/price/quantity/...`，无数据时为空）。
**归一化必须防御式**：每行取第一个非空字段，未知字段忽略，不得因字段缺失崩溃。

### 1.3 详情 / 变种（读）

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 详情（主） | POST | `/api/tiktokProduct/gettiktokProduct.json` | `idStr` |
| 详情（兼容） | POST | `/api/tiktokProduct/getByProductId.json` | `productId` |
| 变种列表 | POST | `/api/tiktokProduct/getVariantByProductId.json` | `id` |

返回 `data`：站点编辑器为 `{product:{...}, category, categoryList, categoryTree, shopId, isGlobalMode, ...}`
（`setFormData(o)` 直接吃 `product`，字段见 §1.5）。详情读取失败/为空时抛 `TikTokError`。

### 1.4 类目 / 属性 / 品牌（只读）

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 类目根/子级 | POST | `/api/tiktokCategory/list.json` | `shopId`（无店铺传 `-1`）、`site`（站点代码 GB/US/…）、`crossBorder`（**跨境=1**）、`categoryParentId`（顶层 `"0"`，子级=父节点 `categoryId`） |
| 类目搜索 | POST | `/api/tiktokCategory/searchCategory.json` | 同上 + `category`=关键词（实测该字段名；`keyword/searchValue/categoryName` 均无效） |
| 类目属性 | POST | `/api/tiktokCategory/attributeList.json` | `shopId`、`site`、`crossBorder`、`categoryId`、`type=3`（新类目属性；不带 type 只返回 1 条） |
| 类目资质 | 同上 `list.json` 节点字段 | – | `certifications` 为 JSON 字符串：`[{id,name,is_required,sample_image_url,...}]` |
| 最近使用 | POST | `/api/tiktokCategory/getHistoryCategory.json` / `historyList.json` | `shopId`、`site` |
| 品牌 | POST | `/api/tiktokCategory/listCategoryBrands.json` | `site`、`crossBorder`、`categoryId`（best-effort；失败返回 `[]`） |
| 尺码表 | POST | `/api/tiktokSizeChartProduct/templateOptions.json` / `rule.json` | 可选，v1 不做 |

类目节点字段：`id`（内部）、`categoryId`（平台）、`categoryName`/`transName`、`categoryParentId`、
`nodePath`、`nodePathId`（`a/b/c`）、`isLeaf`（1=叶子）、`certifications`、`supportSizeChart`、
`supportCod`、`supportIdentifierCodeExemption`。

### 1.5 保存包（`tiktokSave.txt`）字段形状（前端逆向）

前端 `service-DsNnR49g.js`：
```js
async function L(payload, op, saveMode) {
  const file = await zip(JSON.stringify(payload), "tiktokSave.txt");
  const form = { file, op };
  if (saveMode) form.saveMode = saveMode;
  return postMultipart(`/api/tiktokProduct/add.json`, form);      // 全球= tiktokGlobalProduct
}
```
op 语义（前端按钮逆向，**已确认**）：
- `1` = **保存**（留在采集箱）
- `3` = **保存并移入待发布**（仅 draft 状态出现）
- `2` + `saveMode="AS_DRAFT"` = **发布为草稿**
- `2` = **发布**（在线商品为「更新」）
- `5` = **定时发布**（需 `dxmScheduleTimeStr`，v1 不做）

payload 关键字段（编辑器各 store 汇总；与详情 `product` 结构基本对称，**采用详情原样透传 + overrides**）：
```jsonc
{
  // 标识/状态
  "id":"<idStr>", "op":1, "shopId":"<店铺>", "site":"GB", "siteIds":["<siteShopId>…"],
  "dxmState":"draft", "dxmOfflineState":null, "productStatus":null, "preSaleFlag":0, "productId":null,
  // 类目
  "categoryId":"605196", "categoryList":[{"categoryId":"605196","categoryName":"…"}], "isNewCategory":0,
  // 文案
  "productName":"…",                 // 25–255
  "description":"<p>HTML</p>",       // ≤10000
  "keyProductFeatures":["…"],        // 仅 US/4Seller，最多 5 条且总长 ≤1500
  "searchTerms":["…"],               // 仅 US/4Seller，最多 15 个且总长 ≤250
  // 刊登类型/变种
  "productType":1,                   // 1=多变种；0=单品（isSingleProduct）
  "option1":…, "option1Value":…, "option2":…, "option2Value":…, "option3":…, "option3Value":…,
  "skuImgs":"…", "variationList":[ …sku 数组，见下… ],
  // 图片/视频
  "mainImage":"url1|url2|…",         // `|` 分隔，第 1 张为主图；1–9
  "draftImgUrl":"url1|url2|…",
  "sizeChart":"", "videoId":"", "videoData":{...},
  // 物流/保修
  "packageWeight":0.5, "packageLength":10, "packageWidth":10, "packageHeight":10,
  "packageDimensionUnit":"CENTIMETER", "packageDimensionMode":"PRODUCT",
  "warehouseId":"", "isCodOpen":0,
  // 店小秘信息 / 资质
  "sourceUrl":"", "fullCid":"", "productUnit":"100000015", "lotNum":null, "packageType":null,
  "productAttributes":"[{...}]"（JSON 字符串）, "productCertifications":"", "brandInfo":"", "brandId":"",
  "manufacturerId":"", "responsiblePersonId":"", "productCertificationsRules":{},
}
```
SKU 行（variationList）字段：`_combineId`、`id/skuId/dxmProductId/productId`、`skuProperties`
（`[{attrId,attrName,sku_property_id,sku_property_name}]`）、`shopId`、`sellerSku`、`originalPrice`、
`sourceCurrency/sourcePrice/sourcePriceUsd`、`sourceDiscountPrice/sourceDiscountPriceUsd`、
`priceIncludeVat`、`startingBidPrice`、`availableStock`、`identifierCode`。

保存包发出前必须先 `delete payload.variationBos`（前端 `ve()` 实测逻辑）。

### 1.6 站点限额（`getLimitBySite`，实测）

```jsonc
{"priceMin":0.01,"priceMax":180000000.0,"stockMax":99999,
 "nameMax":255,"nameMin":25,"descMax":10000,"descImgMax":30,
 "imgCountMax":9,"imgCountMin":1,
 "packageMin":1000,"packageMid":1000,"packageMax":1000,
 "variationSizeMax":100,"variationNameMax":20,"variationOptionMax":50}
```
（`packageMin/Mid/Max` 为重量/尺寸档位参数，实测各站点相同；保存前用 `nameMin/nameMax/descMax/priceMin/stockMax` 做校验。）

### 1.7 写接口（绑定店铺后可用）

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 保存/发布 | POST multipart | `/api/tiktokProduct/add.json` | `file`=ZIP(`tiktokSave.txt`)、`op`、`saveMode?` |
| 批量发布 | POST | `/api/tiktokProduct/batchPublish.json` | `ids`、`shopIds`、`saveMode?` |
| 批量移入待发布 | POST | `/api/tiktokProduct/batchSetPublish.json` | `ids` |
| 批量删除 | POST | `/api/tiktokProduct/batchProductDelete.json` | `ids` |
| 改价/改库存 | POST | `/api/tiktokProduct/updatePriceOrStock.json` | `id`、SKU 行 |
| 同步商品 | POST | `/api/tiktokProduct/syncProductById.json` | `id` |

**写安全**：`save_to_tiktok` 是唯一真实写收口；`AURORA_NO_SAVE`/`AURORA_TEST` 下拒绝；
`dry_run=False` 必须显式 `confirm_write=True`；测试/脚本注入 `client` 时必须复用，绝不内部新建真实客户端
（Amazon 曾因违反此条发生未授权写入，见 `docs/AMAZON_VERIFICATION.md §3.3`）。

---

### 1.8 实测修订（2026-11-22 只读核验 18/18 PASS 后，与本文早期推断不同之处）

| # | 实测 | 实现对策 |
|---|---|---|
| 1 | `gettiktokProduct.json` 对不存在的 `idStr` 返回 **HTTP 404 HTML**（不是 JSON `code=-1`） | `product_detail` 先试 `idStr`，失败回退 `getByProductId.json {productId}`；最终仍空则抛中文 `TikTokError` |
| 2 | `getHistoryCategory.json` 返回**空 body**（非 JSON），不可用 | `category_history` 以 `historyList.json` 为主，`getHistoryCategory` 仅兜底 |
| 3 | POST `listCategoryBrands.json` 实测 **HTTP 500 HTML** | `brands()` best-effort 返回 `[]`（再试 GET `listCategoryBrand.json`），不阻断 |
| 4 | `getPublishableSite.json` 实测 **404** | 不纳入 client 必需签名；站点映射以店铺 `shopSiteList`/L7 站点为准 |
| 5 | `getLimitBySite` 实际返回 **20 字段**（文档 15 + `id/site/crossBorder/createTime/updateTime` 空值） | 取文档 15 个限额字段，其余忽略 |
| 6 | `pageList` 响应**不含** `draftNum/waitPublish/publishFail/publishing` | `list_products` 额外调一次 `getOfflineCounts` 补齐（best-effort，失败回 0）；假 session 测试需允许第二次请求 |
| 7 | `requests` 先编码 `data` 再编码 `files`，默认 multipart 顺序是 `file` 在后 | `save_product` 用有序 `files=[(file,ZIP),(op,..),(saveMode,..)]` 保证 `file` 在前；真实自检 `file_field_first=true` |
| 8 | 类目下钻确认用父节点 **`categoryId`**（不是内部 id `id`） | `categories(site, parent_id=父 categoryId)` |
| 9 | 前端对 `getL7ShopIdAndSite` 用 **GET** | client 同 |

实测证据：`docs/tiktok-readonly-verification.json`（18/18 PASS，19 请求，0 拦截，Cookie 未落盘）。

---

## 2. 站点 → 国家 / 币种 / 语言（AI 与校验用）

`services/tiktok_prompts.py` 内置 `SITE_META`（未连店铺也能用）：

| site | 国家/地区 | 币种 | 语言 | lang_name |
|---|---|---|---|---|
| US | 美国 | USD | en | 英语 |
| GB | 英国 | GBP | en | 英语 |
| IE | 爱尔兰 | EUR | en | 英语 |
| DE | 德国 | EUR | de | 德语 |
| AT | 奥地利 | EUR | de | 德语 |
| FR | 法国 | EUR | fr | 法语 |
| BE | 比利时 | EUR | nl | 荷兰语 |
| NL | 荷兰 | EUR | nl | 荷兰语 |
| ES | 西班牙 | EUR | es | 西班牙语 |
| IT | 意大利 | EUR | it | 意大利语 |
| PT | 葡萄牙 | EUR | pt | 葡萄牙语 |
| PL | 波兰 | EUR | pl | 波兰语 |
| CZ | 捷克 | CZK | cs | 捷克语 |
| GR | 希腊 | EUR | el | 希腊语 |
| HU | 匈牙利 | EUR | hu | 匈牙利语 |
| JP | 日本 | JPY | ja | 日语 |
| TH | 泰国 | THB | th | 泰语 |
| VN | 越南 | VND | vi | 越南语 |
| MY | 马来西亚 | MYR | ms | 马来语 |
| SG | 新加坡 | SGD | en | 英语 |
| PH | 菲律宾 | PHP | en | 英语 |
| ID | 印尼 | IDR | id | 印尼语 |
| BR | 巴西 | BRL | pt | 葡萄牙语 |
| MX | 墨西哥 | MXN | es | 西班牙语 |

规则：
- 站点非英语 → AI 输出该站点语言（标题/描述/卖点/关键词全部同语言）；`lang_name` 进提示词。
- **全球产品（tiktokGlobal）只允许英文**（前端提示原文）。
- US/4Seller 才有 `keyProductFeatures`（5 点）与 `searchTerms`；其它站点不生成这两项。

---

## 3. 后端模块与接口（UI 按此签名编码）

文件所有权（并行开发，避免写冲突）：
- `integrations/tiktok.py`、`tools/verify_tiktok_read.py` → **tiktok-client**
- `services/tiktok_payload.py`、`services/tiktok_prompts.py` → **tiktok-content**
- `services/tiktok_pipeline.py`、`services/tiktok_publish.py` → **tiktok-pipeline**
- `config.py`、`platforms.py`、`navigation.py`、`ui/app.py` → **tiktok-ui**
- `tests/test_tiktok_contract.py`、`tools/verify_tiktok_ui.py`、`docs/TIKTOK_VERIFICATION.md` → **tiktok-verify**

### 3.1 `src/aeclone/integrations/tiktok.py`

```python
class TikTokError(RuntimeError): ...
class TikTokNotReady(TikTokError): ...   # 未绑定店铺/未授权

OP_SAVE="1"; OP_PUBLISH="2"; OP_MOVE_PENDING="3"; OP_SCHEDULE="5"
SAVE_MODE_AS_DRAFT="AS_DRAFT"; SAVE_NAME="tiktokSave.txt"

class TikTokClient:
    def __init__(self, cookies: str | dict, timeout: int = 30,
                 base: str = "https://www.dianxiaomi.com"): ...

    # 登录 / 店铺 / 站点
    def check_login(self) -> bool
    def user_in(self) -> dict
    def shops(self) -> list[dict]
        # [{id,name,platform,merchant_id,marketplace_id,marketplace_ids,
        #   sites:[{site,site_shop_id,name}], is_tiktok:True}]
        # 来源：userIn.shopMap 中 platform=="tiktok"（含 childShopList/shopSiteList 展开）
    def sites(self, shop_id: str = "") -> list[dict]
        # [{site,code,country,currency,language,language_name,cross_border,site_shop_id}]
        # 有店铺：店铺站点 ∪ L7；无店铺：23 个 L7 站点（cross_border=True）
    def site_limits(self, site: str = "") -> dict          # getLimitBySite；失败返回内置默认
    def warehouses(self, shop_id: str = "") -> list[dict]  # best-effort，失败 []

    # 商品
    def list_products(self, state="draft", page=1, page_size=20,
                      shop_id="-1", keyword="", site="") -> dict
        # {"items":[...],"total","page_no","total_page",
        #  "drafts","wait_publish","publish_fail","publishing","markets"}
    def counts(self, shop_id="-1") -> dict
    def product_detail(self, product_id: str) -> dict
        # gettiktokProduct(idStr) → 失败回退 getByProductId(productId)；
        # 返回 data 原样（含 product/category/categoryList/categoryTree）
    def product_variants(self, product_id: str) -> list[dict]

    # 类目
    def categories(self, site, parent_id="0", shop_id="-1", cross_border=True) -> list[dict]
    def search_categories(self, keyword, site, shop_id="-1", cross_border=True) -> list[dict]
    def category_attributes(self, site, category_id, shop_id="-1",
                            cross_border=True, attr_type=3) -> list[dict]
    def category_history(self, site, shop_id="-1") -> list[dict]
    def brands(self, site, category_id="", shop_id="-1") -> list[dict]  # 失败 []

    # 只读检查
    def check_banned_words(self, title="", description="") -> list[str]  # 失败 []（仅提示）

    # 唯一写入口
    def save_product(self, payload: dict, op: str = OP_SAVE,
                     save_mode: str = "") -> dict
        # ZIP(tiktokSave.txt)+multipart(file,op[,saveMode])；返回原始 JSON
        # 未绑定店铺的错误（"店铺"/"产品未选择店铺"）→ 抛 TikTokNotReady(中文可操作提示)
    def publish_products(self, ids, shop_ids="", save_mode="") -> dict
    def delete_products(self, ids) -> dict
```

兼容/工具：`parse_cookie`、`zip_payload`、`normalize_product`、`extract_product` 保留并单测。

### 3.2 `src/aeclone/services/tiktok_payload.py`

```python
SAVE_KEYS = [...]   # §1.5 顶层键（保序，用于契约测试）
SITE_META = {...}   # 与 prompts 共用同一份（从 prompts 导入，避免两处漂移）
DEFAULT_LIMITS = {...}

def split_pipe(raw: str) -> list[str]                # `|` 分隔图片，去空
def join_pipe(items: list[str]) -> str
def extract_detail_product(detail: dict) -> dict      # data.product 优先，其次 data 本身；深拷贝
def build_tiktok_payload(detail: dict, cfg=None, overrides: dict | None = None,
                         *, site: str = "", shop_id: str = "",
                         op: str = OP_SAVE) -> dict
    # 1) product = extract_detail_product(detail)（深拷贝，保留全部未知字段）
    # 2) 应用 overrides（见 3.4）：productName/description/categoryId/categoryList/
    #    mainImage/draftImgUrl/packageWeight/packageLength/Width/Height/
    #    packageDimensionUnit/warehouseId/sourceUrl/productUnit/videoId/
    #    keyProductFeatures/searchTerms/productType/option*/variationList
    # 3) 同步 site/shopId/op；删除 variationBos；数值字段规范化
    # 4) 未知的 camelCase 键原样保留（透传策略，避免破坏详情里的必填结构）

def validate_tiktok_payload(payload: dict, *, limits: dict | None = None,
                            site: str = "", is_global: bool = False) -> list[str]
    # 返回阻断项；空=可保存。规则见 §5
def build_warnings(payload: dict, *, site: str = "") -> list[str]
def apply_site(payload: dict, site: str, *, shop_id: str = "") -> dict
    # 站点变更：site/siteIds 同步；清空 categoryId/categoryList/warehouseId（需重选）
def price_stock_overrides(payload: dict, price, stock, sku="") -> dict
    # 单品：variationList[0]/顶层价格；多变种：仅当唯一 SKU 时允许
```

### 3.3 `src/aeclone/services/tiktok_prompts.py`

```python
SITE_META: dict[str, dict]                     # §2
def language_for_site(site: str) -> str
def site_meta(site: str) -> dict
def is_us_site(site: str) -> bool              # US/4Seller（keyProductFeatures/searchTerms 可用）
def build_title_prompt(product, cfg, site) -> str
def build_description_prompt(product, cfg, site) -> str
def build_key_features_prompt(product, cfg, site, count=5) -> str
def build_search_terms_prompt(product, cfg, site) -> str
def parse_key_features(text, count=5) -> list[str]
def parse_search_terms(text) -> list[str]
def html_description(text) -> str
DEFAULT_PROMPTS: dict[str, str]
```
硬约束（system prompt + 本地后处理双校验）：
- 标题：站点语言、25–255 字符、不得含中文（非中文站点）、emoji、HTML、联系方式、促销词、竞品品牌；
  保留真实型号/规格，不编造认证参数。
- 描述：HTML 片段，只允许 `<p><ul><li><b><br>`，≤10000 字符；3–5 段 + 参数列表；
  禁 `<script>/<style>/<img>`、联系方式、网址、促销、医疗/绝对化表述。
- 五点（仅 US）：恰好 `count`（3–5），单条不换行，总长 ≤1500。
- 关键词（仅 US）：≤15 个、总长 ≤250，小写、去重、空格分隔、无停用词/标点。
- 所有 AI 结果必须可编辑；保存前本地二次校验。

### 3.4 `overrides` 唯一键名约定（UI→后端，必须两侧一致）

```python
{
  "title": str, "description": str,
  "key_features": list[str], "search_terms": list[str],
  "category_id": str, "category_name": str, "category_list": list[dict] | str,
  "images": list[str],               # 主图 URL 列表（第 1 张=主图）
  "video_id": str, "source_url": str,
  "product_type": int,               # 0 单品 / 1 多变种
  "price": float|str|None, "stock": int|str|None, "seller_sku": str,
  "weight": float|str|None, "length": float|str|None,
  "width": float|str|None, "height": float|str|None,
  "dimension_unit": "CENTIMETER"|"INCH",
  "warehouse_id": str, "product_unit": str,
  "brand_id": str, "manufacturer_id": str, "responsible_person_id": str,
  "product_attributes": str | list,  # 原样 JSON 字符串或列表
  "certifications": str | list,
}
```
> UI 只传这些 snake_case 键；空串/None 表示「不覆盖」。后端负责映射到保存包 camelCase 键。

### 3.5 `src/aeclone/services/tiktok_pipeline.py`

```python
ENV_NO_SAVE="AURORA_NO_SAVE"; ENV_TEST="AURORA_TEST"

@dataclass TikTokStep: name:str; ok:bool; detail:str=""
@dataclass TikTokRunResult:
    product_id:str=""; ok:bool=False; error:str=""; steps:list[TikTokStep]=...
    preview:bool=False; saved:bool=False; issues:list[str]=[]; warnings:list[str]=[]
    title:str=""; description:str=""; key_features:list[str]=[]; search_terms:list[str]=[]
    site:str=""; site_name:str=""; currency:str=""; category_id:str=""; category_name:str=""
    op:str=OP_SAVE; banned_words:list[str]=[]; save_result:dict={}
    ai_used:bool=False; limits:dict={}
    @property def completion_label(self) -> str
    def log(self) -> str

def forced_preview() -> bool
def prepare_save(product_id: str, cfg, overrides: dict|None=None,
                 client=None) -> tuple[dict, dict, list[str], list[str]]
    # (detail, payload, issues, warnings)；只读
def run_tiktok_product(product_id: str, cfg, overrides: dict|None=None,
                       *, dry_run: bool = True, use_ai: bool = False,
                       op: str = OP_SAVE, save_mode: str = "",
                       client: TikTokClient|None = None,
                       confirm_write: bool = False) -> TikTokRunResult
    # 步骤：① 连接/读详情 ② 店铺+站点 ③ 类目校验 ④ AI 生成（use_ai，已有值不覆盖）
    #      ⑤ 组装保存包 ⑥ 必填/限额校验 ⑦ 违禁词提示 ⑧ 预演 或 保存
    # 真实保存条件：dry_run=False 且 confirm_write=True 且非 forced_preview
    # 且 core.guard.require(cfg) 且无 issues
def ai_generate_field(product, cfg, field, site="", count=5) -> tuple[object, list[str]]
    # field ∈ title/description/key_features/search_terms
def ai_generate_all(product, cfg, site="", count=5) -> tuple[dict, list[str]]
```

### 3.6 `src/aeclone/services/tiktok_publish.py`

```python
def make_client(cfg) -> TikTokClient     # Cookie 优先级见 §0
def save_to_tiktok(product_id, cfg, overrides=None, allow_invalid=False,
                   op=OP_SAVE, save_mode="", client: TikTokClient|None = None,
                   confirm_write: bool = False) -> dict
    # 唯一真实写收口；返回 {"result","issues","warnings","payload","product_id"}
def publish_tiktok(ids, cfg, shop_ids="", client=None, confirm_write=False) -> dict
```

### 3.7 `config.py` 新增 DEFAULTS（TikTok 段）

```python
"tiktok_cookies": "",
"tiktok_shop_id": "",
"tiktok_shop_name": "",
"tiktok_site": "",               # GB/US/…（L7 站点代码）
"tiktok_site_name": "",
"tiktok_list_state": "draft",
"tiktok_test_product_id": "",
"tiktok_category_id": "",
"tiktok_category_name": "",
"tiktok_category_path": "",      # nodePathId a/b/c
"tiktok_category_list": "[]",    # JSON
"tiktok_title": "",
"tiktok_description": "",
"tiktok_key_features": "[]",     # JSON（US）
"tiktok_search_terms": "[]",     # JSON（US）
"tiktok_images": "[]",           # JSON：主图 URL 列表
"tiktok_video_id": "",
"tiktok_source_url": "",
"tiktok_product_type": 1,        # 0 单品 / 1 多变种
"tiktok_price": "",
"tiktok_stock": 0,
"tiktok_sku": "",
"tiktok_weight": "",
"tiktok_length": "",
"tiktok_width": "",
"tiktok_height": "",
"tiktok_dimension_unit": "CENTIMETER",
"tiktok_warehouse_id": "",
"tiktok_brand_id": "",
"tiktok_manufacturer_id": "",
"tiktok_responsible_person_id": "",
"tiktok_product_attributes": "",
"tiktok_op": 1,
"tiktok_save_mode": "",
"tiktok_use_ai": False,
"tiktok_key_feature_count": 5,
"tiktok_title_prompt": "",
"tiktok_description_prompt": "",
"tiktok_key_features_prompt": "",
"tiktok_search_terms_prompt": "",
"tiktok_last_status": "",
```

---

## 4. 平台 UI（页面 14「TikTok 刊登」）

### 4.1 平台注册与导航

- `platforms.py`：`TIKTOK.implemented=True`；页面清单：
  `0 概览 / 14 TikTok 刊登 / 10 任务与日志 / 11 设置`（与 Amazon 的「少而精」一致）。
- `navigation.py`：`Destination(14, "刊登", "TikTok 刊登", "PUBLISH", "TikTok 采集箱编辑、店铺/站点、类目、AI、保存", "tiktok ...")`。
- `PLATFORM_CAPS["tiktok"]`：只关闭共享页里的 SMT 专属能力（保持与 Amazon 一致的最小集合），
  新增能力键（默认 True，TikTok 关闭）：
  `smt_wizard/smt_pipeline_actions/smt_run_stop/smt_pipeline_stats/smt_store_selector/smt_resume/
  smt_extra_ai/smt_prompt_library/smt_detail_assembly/marketing_images/smt_queue/scheduled_publish/
  smt_sku_page/legacy_price_page/smt_category_source/smt_product_settings/smt_attr_templates/
  eu_qualification/smt_shop_template/smt_template_ready` → 全部 False（TikTok 走 14 号页）。
- `licensing.py`：`tiktok` 功能包保持 `["listing","ai_text","ai_image"]`，`implemented` 参与锁定页判定。

### 4.2 页面 14 卡片（数据流）

```
① 连接与店铺/站点   Cookie/读取本机/测试连接 | 店铺下拉（无店铺显示引导）| 站点下拉（23 L7 + 店铺站点）
                    | 站点限额（标题/描述/图片/价格/库存）| 状态行
② 商品列表          采集箱/待发布/在线 计数按钮 | 搜索 | 刷新 | 分页 | 行：图片/标题/ID/站点/类目/更新时间/编辑
③ 类目与属性        站点感知：逐级类目下拉 + 子级列表 + 搜索框 + 最近使用 | 当前类目路径
                    | 属性表（必填高亮，可编辑 JSON）| 资质提示（certifications）
④ 文案与 AI         标题（/255）| 描述 HTML（/10000）| 五点（US，≤5，总长/1500）| 关键词（US，≤15）
                    | 用 AI 开关 | AI 一键生成 | 每字段 AI | 一键翻译（提示词）
⑤ 图片与视频        主图 URL 列表（1–9，第 1 张主图）| 附加图提示 | 视频 ID/URL | 预览缩略图
⑥ 价格/库存/物流     价格（币种随站点）| 库存（≤99999）| SKU | 重量/长宽高+单位 | 仓库下拉 | 来源 URL
⑦ 预演与保存         动作下拉（保存/保存并移入待发布/发布为草稿/发布）| 预演 | 保存（二次确认）
                    | 结果面板：步骤/阻断项/提醒/接口返回/保存包预览
⑧ 诊断              逐项检查：登录/店铺/站点/类目/详情/保存前置；未绑定店铺给出可操作指引
```

**必须移除/不展示**：SMT 模板、营销图、定时发布（v1）、EU 资质等与 TikTok 无关的共享卡片。
**不得展示**未实现动作的按钮；所有按钮回调 `try/except` + toast，不抛到界面。

### 4.3 站点选择规则（重点）

1. 店铺下拉：TikTok 店铺（`platform=="tiktok"`）；无店铺时下拉为空并显示
   「未检测到 TikTok 店铺：请在店小秘『授权店铺』绑定 TikTok Shop 后点刷新」。
2. 站点下拉：有店铺 → 店铺可用站点；无店铺 → 23 个 L7 站点（标注「未绑定店铺，可先选站点配类目/AI」）。
3. 切站点：更新币种/语言/限额；清空类目（须重选）；AI 与校验语言随之切换；写入 `tiktok_site`。
4. 切店铺：重载站点；若当前站点不在新店铺站点内，自动落到第一个可用站点并提示。
5. 保存前置：店铺 + 站点 + 类目 + 至少 1 张主图；缺失时在结果面板列出，不发起写请求。

### 4.4 概览页（0）与侧栏

- 概览：TikTok 计数卡（采集箱/待发布/在线/发布失败）来自 `tiktok_counts`；「去 TikTok 刊登」入口；
  就绪项：店铺绑定 / 站点 / 测试商品；不含 SMT 批量管线入口。
- 侧栏：启动按钮文案「去 TikTok 刊登」；`smt_pipeline_actions` 等 cap 关闭后不展示 SMT 入口。
- 其它平台的现有行为**不得改变**（测试断言）。

---

## 5. 校验规则（保存前，阻断项）

1. `shopId` 必填且为已授权 TikTok 店铺（无店铺 → `TikTokNotReady`，UI 给出绑定指引）。
2. `site` 必填且在站点列表内。
3. `categoryId` 必填；叶子类目（`isLeaf==1`）才能保存；站点不匹配的类目拒绝。
4. `productName` 长度 25–255（限额来自 `getLimitBySite`）；不得为空/纯空格。
5. `description` 非空且 ≤10000 字符；HTML 只允许白名单标签。
6. `mainImage` 至少 1 张、至多 9 张；URL 必须 http(s)；第 1 张为主图。
7. 价格：单品必填，0.01–180000000；多变种每个 SKU 必填。
8. 库存：0–99999。
9. 重量：>0（TikTok 物流必填）；尺寸单位 CM/INCH 合法。
10. US/4Seller：`keyProductFeatures` ≤5 且总长 ≤1500；`searchTerms` ≤15 且总长 ≤250；
    其它站点若存在这两项 → 提醒移除（不阻断草稿保存，发布前阻断）。
11. `variationList` 里每个 SKU 必须有 `sellerSku`（可自动生成）与价格/库存。
12. 违禁词检查仅提示，不阻断。

警告（不阻断）：标题 <25 但草稿可存、附加图 <3、缺少品牌/资质、库存 0、价格低于成本、
描述缺少参数列表、认证资质必填项未上传。

---

## 6. 验证与验收标准

### 6.1 必跑
```bash
.venv/bin/python tests/test_tiktok_client.py
.venv/bin/python tests/test_tiktok_payload.py
.venv/bin/python tests/test_tiktok_prompts.py
.venv/bin/python tests/test_tiktok_pipeline.py
.venv/bin/python tests/test_tiktok_contract.py
.venv/bin/python tests/test_tiktok_ui_page.py
.venv/bin/python tests/test_platform_ui_filter.py
.venv/bin/python tests/test_ui_redesign.py
.venv/bin/python tools/verify_regressions.py
.venv/bin/python tools/test_offline.py
.venv/bin/python tools/verify_tiktok.py            # 只读真实接口
.venv/bin/python tools/verify_tiktok_ui.py         # 页面 14 按钮遍历 + 截图
.venv/bin/python tools/button_audit.py --json docs/ui-button-audit.json
```

### 6.2 真实账号只读验收（必须全 PASS）
1. `userIn` 登录成功，账号 `QINGHEMAOYI`/puid `2728813`；店铺列表如实反映「无 TikTok 店铺」。
2. `getL7ShopIdAndSite` → 23 站点，与 §0 完全一致。
3. `pageList` 三态与计数端点 code=0；采集箱 0 与事实一致。
4. `tiktokCategory/list` GB 顶层 = 30 条；`categoryParentId` 下钻可用；
   `searchCategory` category="phone" 返回 >0；叶子 `attributeList?type=3` 返回 >0。
5. `getLimitBySite` 字段与 §1.6 一致。
6. 保存传输格式自检：不带店铺的 payload → 服务端返回含「店铺」的中文错误（证明 ZIP/multipart/字段正确）。
7. 报告写 `docs/tiktok-readonly-verification.json`；Cookie 不落盘、不打印。

### 6.3 UI 验收
- 页面 14 在 FakePage 沙箱中可构建（明/暗主题），站点切换/类目切换/AI/预演/保存按钮回调 0 异常。
- 全部按钮审计：TikTok 4 页所有回调 ERROR=0；新增按钮必须有人工可解释的行为（含 NOT_READY 分支）。
- 截图：`docs/design/tiktok-listing-page.png`、`-2-save.png`、`-3-diagnose.png`。
- 平台切换：Alibaba/速卖通/Amazon 页面集合与裁剪行为不变（回归断言）。

### 6.4 真实写入回环（绑定 TikTok 店铺后执行，需用户显式确认）
1. 在采集箱建一个测试草稿（业务：标题 `AURORA-TEST-<时间>`，1 图，最小必填）。
2. `op=1` 原样回写 → `code=0`，回读字段零变更。
3. overrides 改标题/描述 → 保存 → 回读一致。
4. 删除测试草稿（`batchProductDelete`），计数回到原值。
> 脚本：`tools/verify_tiktok.py --write-roundtrip --confirm-write`；默认 dry-run，未确认绝不写。

---

## 7. 安全与边界

- 所有真实写只经 `services/tiktok_publish.save_to_tiktok`；`confirm_write=True` 为硬门槛。
- `AURORA_TEST`/`AURORA_NO_SAVE` 下真实写必须失败并给出中文原因。
- Cookie 只进内存；日志/报告脱敏（前 6 位）。
- 不修改其它平台文案/行为；不在共享页面到处写 `if platform=="tiktok"`，用 `PLATFORM_CAPS`。
- 追加式实现：不改动 Alibaba/Amazon 既有接口签名；新代码失败不得影响其它平台启动。

---

## 8. 结论口径（最终报告必须如实）

- 已真实验证：登录/店铺/23 站点/计数/列表/类目树/类目搜索/类目属性/限额/保存传输格式。
- **无法在本账号真实验证**：TikTok 采集箱商品读写回环（账号未绑定 TikTok 店铺）。
  已提供 `--write-roundtrip` 脚本与完整契约测试；绑定店铺后 1 条命令完成真实回环。
- 不得在报告里把「离线 fixture 通过」写成「真实接口写入成功」。

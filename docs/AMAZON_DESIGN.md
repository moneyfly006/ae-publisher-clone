# Amazon 刊登功能 · 设计与实现契约（2026-11-22）

> 目标：在现有 AuroraList（Flet 桌面端）里，把 Amazon 平台从「骨架/速卖通管线」升级为
> 真正可用、可验证的「采集箱商品 → 编辑 → AI 文案 → 预演 → 保存/移入待发布」刊登工作台。
> 本文件是**实现契约**：逆向抓包结论 + 模块接口 + UI 规范 + 验收标准。
> 所有 teammate 必须按此契约实现，接口签名不得随意更改（UI 与后端并行开发）。

---

## 0. 已确认的环境事实（真实账号，只读抓包 2026-11-22）

| 项 | 值 |
|---|---|
| 店小秘账号 | `QINGHEMAOYI` / puid `2728813` |
| Amazon 店铺 | shopId `9408005`，店铺名 `QingHe`，merchantId `A26OV9RTCAYJMS`，默认站点 US |
| 账号可用站点（该店铺） | 美国 US `ATVPDKIKX0DER` / 加拿大 CA `A2EUQ1WTGCTBG2` / 墨西哥 MX `A1AM78C64UM0Y8` / 巴西 BR `A2Q3Y263D00KWC` |
| 采集箱（draft） | **16** 个 |
| 待发布（offline） | **5** 个；`leftStat.waitPublish=5`，`publishFail=21` |
| 在线（online） | **142** 个 |
| 样例采集箱商品 | id `220114595363182310`，站点 CA，类目 `13553005011` Backhoes，SKU `WY-QHCA-LTM-1`，价格 0，数量 0，图片 6 张 |
| 验证用 Cookie | 放 `.tmp-probe/cookies.txt`（**已在 .gitignore，禁止提交**）；脚本支持 `DXM_COOKIE_PATH` 环境变量 |

Cookie 读取优先级（与 Alibaba 保持一致）：`cfg.amazon_cookies` → `cfg.manual_cookies` → 本机浏览器
（`integrations.browser_cookies.resolve("dianxiaomi.com")` 或 `integrations.chrome_cookies.read_cookies`）。

---

## 1. 逆向结论：Amazon 刊登真实接口（全部已实测）

Base：`https://www.dianxiaomi.com`

### 1.1 登录 / 店铺 / 站点

| 用途 | 方法 | 路径 | 参数 | 返回要点 |
|---|---|---|---|---|
| 登录态 | GET | `/api/userIn.json` | – | `code=0`；`data.id/account/shopMap` |
| 店铺站点 | POST | `/api/amazonProduct/getCountryByShopId.json` | `shopId` | `data.strCountry` 形如 `美国,na,USD,US,ATVPDKIKX0DER#加拿大,...`；`data.markeyPlaceLang` = `{marketPlaceId: "en_US"}` |

`strCountry` 每段 `国家,区域,币种,站点代码,marketPlaceId`（用 `#` 分隔）。

### 1.2 商品列表（采集箱 / 待发布 / 在线）

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 分页列表（首选） | POST | `/api/amazonProduct/pageList.json` | `pageNo`、`pageSize`、`shopId`（`-1`=全部）、`dxmState`（`draft`/`offline`/`online`）、`searchValue` |
| 在线列表（旧） | POST | `/api/amazonProduct/list.json` | `pageNo/pageSize/shopId` |

返回：
```jsonc
{"code":0,"data":{
  "page":{"list":[...],"pageNo":1,"pageSize":20,"totalSize":16,"totalPage":1},
  "draftNum":16,"onlineNum":0,
  "leftStat":{"waitPublish":"5","publishFail":"21","publishing":0},
  "marketPids":{"ATVPDKIKX0DER":"美国", ...},
  "countryMap":{...}
}}
```
列表行关键字段：`id`、`shopId`、`marketPlaceId`、`categoryId`、`categoryBrowsePath`、`sku`、`title`、
`mainImage`、`extraImages`、`standardPrice`、`saleSalePrice`、`standardPriceCurrency`、`quantity`、
`dxmState`、`errMsg`/`warningMsg`/`importWarningMsg`、`imgUrl`、`sourceUrl`、`isVariation`、`variations`、
`onlineTime`、`updateTime`、`variant` 相关（`variationChildStr` 等）。

### 1.3 编辑器详情（读）

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| 商品详情 | GET | `/api/amazonProductSp/editSp.json` | `id`（商品 id） |
| 新建默认值 | GET | `/api/amazonProductSp/addNew.json` | –（返回 `marketPids/marketPids3/categoryTree`） |
| 类目属性值 | POST | `/api/amazonProductSp/getProductAttrById.json` | `id`、`site`（= **marketPlaceId**） |
| 属性 schema URL | POST | `/api/amazonProductSp/getAttrJsUrl.json` | `site`（站点代码如 `CA`）、`productType`、`id`（类目内部 id） |

`editSp` 返回 `data`：
```jsonc
{"site":"CA","product":{...164 字段...},
 "marketPids":{...},"marketPids3":{...},
 "itemTypeKeyword":"","categoryTree":[...],"categoryName":"未分类","productType":"OUTDOOR_LIVING"}
```
`product` 里本功能会用到的字段：`id/shopId/marketPlaceId/categoryId/categoryBrowsePath/fullCid/sku/title/
titleDifferentiation/brand/manufacturer/description/bulletPoint/searchTerms/standardPrice/saleSalePrice/
standardPriceCurrency/quantity/conditionType/conditionValue/mainImage/extraImages/extraImagesStr/draftImgUrl/
audience/isVariation/brandSettledType/standardProductType/standardProductId/sourceUrl/variationTheme/skuTitle/
gpsrManufacturerReference/dsaResponsiblePartyAddress/gpsrSafetyAttestation/switchFulfillmentTo/
merchantShippingGroup/productAttr/productData/dxmState/isNew/variations`。

**关键约定（实测）**
- 五点描述 = `product.bulletPoint`，**多个卖点用 `±` 连接成一个字符串**（拆分 `split("±")`，拼接 `join("±")`）。
- 产品描述 = `product.description`，**HTML 字符串**（`<br>`、`<p>` 等），不是纯文本。
- `searchTerms` 纯文本；`productAttr` 是 JSON **字符串**（读取后可直接原样回写）。
- `quantity`、`isVariation`、`gpsrSafetyAttestation`、`skuTitle` 在详情里是字符串，保存包里要转数字。

### 1.4 保存 / 移入待发布 / 发布（写）

| 用途 | 方法 | 路径 | 参数 |
|---|---|---|---|
| **保存/发布（唯一写入口）** | POST（multipart） | `/api/amazonProductSp/add.json` | `file`=**ZIP 文件**（内含 `amazonSave.txt`，内容是 JSON 字符串），`op`=操作码 |

`op` 语义（前端逆向确认）：
- `1` = 保存（采集箱内保存 / 编辑，不离开采集箱）
- `3` = 保存并移入「待发布」
- `2` = 保存并发布（提交平台；在线商品为在线更新；返回 `data.uuid` 时前端会轮询进度）
- `5` = 定时发布（需要定时字段，**v1 不做**）
- `6` = 多变种特殊操作（v1 不做）

ZIP 构造等价于前端 `_e(JSON.stringify(payload), "amazonSave.txt")`（zip 里一个条目 `amazonSave.txt`，
UTF-8 JSON）。multipart 字段顺序：`file` 在前，`op` 在后。请求头带 Cookie 与 `X-Requested-With: XMLHttpRequest`。

**保存包（39 个键，实测抓包，必须原样结构）**：
```jsonc
{
 "id":"220114595363182310","shopId":"9408005","marketPlaceId":"A2EUQ1WTGCTBG2",
 "currency":"CAD","title":"...","titleDifferentiation":null,
 "categoryId":"13553005011","categoryBrowsePath":"6299024011/6256963011/6256964011/13553005011",
 "audience":"all","isVariation":0,"brandSettledType":"external_product_id",
 "fullCid":"5405448-","sourceUrl":"https://...",
 "standardProductType":"upc","standardProductId":null,"sku":"WY-QHCA-LTM-1",
 "brand":"Generic","manufacturer":"Generic","productAttr":"{...JSON 字符串...}",
 "standardPrice":null,"saleSalePrice":null,"saleStartDateStr":null,"saleEndDateStr":null,
 "quantity":0,"conditionType":null,"conditionValue":"",
 "mainImage":"https://...","extraImagesStr":"https://a;https://b","draftImgUrl":"https://a|https://b",
 "variationTheme":"","skuTitle":0,"description":"<p>HTML</p>",
 "bulletPoint":"卖点1±卖点2±卖点3±卖点4±卖点5",
 "gpsrManufacturerReference":"","dsaResponsiblePartyAddress":"","gpsrSafetyAttestation":0,
 "switchFulfillmentTo":"MFN","merchantShippingGroup":"legacy-template-id",
 "searchTerms":""
}
```
数值约定：`standardPrice`/`saleSalePrice` 为 0 时前端发 `null`；有价发数字（两位小数）。
`extraImagesStr` = 附加图 `;` 连接；`draftImgUrl` = 附加图 `|` 连接。`mainImage` 单张。

成功判定：HTTP 200 且 JSON `code == 0`；`code != 0` 时 `msg` 即失败原因（如「您的产品编辑成功！」在
成功后会由前端提示，但接口仍以 `code==0` 为准）。**禁止**把 `code=0` 但 `msg` 含失败语义的响应当成功
（复用 `services.publisher.is_failure_msg` 判一次）。

### 1.5 类目 / 站点 / 物流 / 币种 / 品牌 / 违禁词（只读）

| 用途 | 方法 | 路径 | 参数 | 备注 |
|---|---|---|---|---|
| 类目树（逐级） | POST | `/api/amazonProductSp/list.json` | `site`（站点代码 `CA`）、`parentId`（内部 id，顶层 0） | 节点：`id`(内部)、`categoryId`、`categoryName`、`chineseName`、`nodePath`、`nodePathId`、`leafCategory`、`childIds`、`productType` |
| 按路径取类目 | POST | `/api/amazonProductSp/getCategoryBySiteNodePath.json` | `site`（站点代码）、`nodePath`（`a/b/c`） | 返回单个节点；失败即路径不属于该站点 |
| 最近使用类目 | POST | `/api/amazonProductSp/listHistory.json` | `shopId`、`site`（站点代码） | 数组：`categoryId/categoryName/categoryBrowsePath/num` |
| 产品类型列表 | POST | `/api/amazonProductSp/gerProductTypes.json` | `site`（站点代码） | 字符串数组（`OUTDOOR_LIVING`…） |
| 产品类型/类目预测 | POST | `/api/amazonProductSp/productTypePredictCatesByItemName.json` | `shopId`、`site`（站点代码）、`itemName` | 返回 `{productType, amaoznCategorySearchVos}`（可能 `null`） |
| 类目中文翻译 | POST | `/api/amazonProductSp/getCategoryAttrTranslateById.json` | `dataId`（类目内部 id） | 映射表 |
| 币种 | POST | `/api/amazon/currency/list.json` | `shopId`、`marketPlaceId` | `{currency:"CAD"}` |
| 运费模板 | POST | `/api/amazon/logistics/getLogisticsTemplates.json` | `shopId`、`marketPlaceId` | `[{templateId,templateName}]` |
| 品牌 | POST | `/api/amazon/brand/getBrands.json` | `shopId`、`marketPlaceId` | 数组，可为空 |
| 违禁词检查 | POST | `/api/bannedWord/amazonCheckBannedWord.json` | `title`、`description`、`bulletPoint`、`searchTerms` | `data` 可为 null；**仅提示，不阻断** |
| 价格检测 | POST | `/api/priceDetection/amazonProtectPriceDetection.json`（草稿）/ `amazonAdjustPriceDetection.json`（在线） | `standardPrice,saleSalePrice,variationString,id,title,marketPlaceId,sku,isDataToZip=true` | v1 可选，失败不影响保存 |

> 注意：`gerProductTypes` / `listHistory` / `category list` 的 `site` 参数用**站点代码**（`CA`），
> 而 `getProductAttrById` / `currency` / `logistics` / `brand` 用 **marketPlaceId**（`A2EUQ1WTGCTBG2`）。
> 抓包里 `gerProductTypes` 传 marketPlaceId 会返回空数组，务必按此区分。

---

## 2. 站点 → 语言 / 币种（AI 与校验用）

`services/amazon_prompts.py` 内置表（未接入店铺时也能用），实际站点以 `sites()` 返回覆盖：

```python
SITE_META = {
 "ATVPDKIKX0DER": dict(code="US", country="美国", currency="USD", lang="en", lang_name="英语"),
 "A2EUQ1WTGCTBG2": dict(code="CA", country="加拿大", currency="CAD", lang="en", lang_name="英语"),
 "A1AM78C64UM0Y8": dict(code="MX", country="墨西哥", currency="MXN", lang="es", lang_name="西班牙语"),
 "A2Q3Y263D00KWC": dict(code="BR", country="巴西", currency="BRL", lang="pt", lang_name="葡萄牙语"),
 "A1F83G8C2ARO7P": dict(code="GB", country="英国", currency="GBP", lang="en", lang_name="英语"),
 "A1PA6795UKMFR9": dict(code="DE", country="德国", currency="EUR", lang="de", lang_name="德语"),
 "A13V1IB3VIYZZH": dict(code="FR", country="法国", currency="EUR", lang="fr", lang_name="法语"),
 "APJ6JRA9NG5V4": dict(code="IT", country="意大利", currency="EUR", lang="it", lang_name="意大利语"),
 "A1RKKUPIHCS9HS": dict(code="ES", country="西班牙", currency="EUR", lang="es", lang_name="西班牙语"),
 "A1VC38T7YXB528": dict(code="JP", country="日本", currency="JPY", lang="ja", lang_name="日语"),
 "A39IBJ37TRP1C6": dict(code="AU", country="澳大利亚", currency="AUD", lang="en", lang_name="英语"),
 "A21TJRUUN4KGV": dict(code="IN", country="印度", currency="INR", lang="en", lang_name="英语"),
 "A1805IZSGTT6HS": dict(code="NL", country="荷兰", currency="EUR", lang="nl", lang_name="荷兰语"),
 "A2NODRKZP88ZB9": dict(code="SE", country="瑞典", currency="SEK", lang="sv", lang_name="瑞典语"),
 "A1C3SOZRARQ6R3": dict(code="PL", country="波兰", currency="PLN", lang="pl", lang_name="波兰语"),
 "A33AVAJ2PDY3EV": dict(code="TR", country="土耳其", currency="TRY", lang="tr", lang_name="土耳其语"),
 "A2VIGQ35RCS4UG": dict(code="AE", country="阿联酋", currency="AED", lang="en", lang_name="英语"),
 "A17E79C6D8DWNP": dict(code="SA", country="沙特", currency="SAR", lang="ar", lang_name="阿拉伯语"),
 "A19VAU5U5O7RUS": dict(code="SG", country="新加坡", currency="SGD", lang="en", lang_name="英语"),
 "A28R8C7NBKEWEA": dict(code="IE", country="爱尔兰", currency="EUR", lang="en", lang_name="英语"),
 "AMEN7PMS3EDWL": dict(code="BE", country="比利时", currency="EUR", lang="fr", lang_name="法语"),
 "ARBP9OOSHTCHU": dict(code="EG", country="埃及", currency="EGP", lang="ar", lang_name="阿拉伯语"),
 "AE08WJ6YKNBMC": dict(code="ZA", country="南非", currency="ZAR", lang="en", lang_name="英语"),
 "AAHKV2X7AFYLW": dict(code="CN", country="中国", currency="CNY", lang="zh", lang_name="中文"),
}
```

AI 输出语言：站点非英语 → 输出该站点语言（产品/卖点/描述/关键词全部同语言）；`lang_name` 用于提示词。

---

## 3. 后端模块与接口（UI 调用方按此签名编码）

### 3.1 `src/aeclone/integrations/amazon.py`（重写，保留 `AmazonNotReady` 兼容）

```python
class AmazonError(RuntimeError): ...

class AmazonClient:
    def __init__(self, cookies: str | dict, timeout: int = 30, base: str = "https://www.dianxiaomi.com"): ...

    # 登录 / 店铺 / 站点
    def check_login(self) -> bool
    def shops(self) -> list[dict]          # [{id,name,merchant_id,default_marketplace}]
    def sites(self, shop_id: str) -> list[dict]
        # [{marketplace_id, code, country, currency, language, language_name}]
        # 用 getCountryByShopId 解析，再用 SITE_META 补全语言

    # 商品
    def list_products(self, state="draft", page=1, page_size=20,
                      shop_id="-1", keyword="") -> dict
        # {"items":[normalized...], "total":int, "page_no":int, "total_page":int,
        #  "drafts":int, "wait_publish":int, "publish_fail":int,
        #  "markets":{marketplace_id:name}}；state ∈ draft/offline/online
    def product_detail(self, product_id: str) -> dict
        # 返回 editSp 的 data 原样（含 site/product/...），失败抛 AmazonError
    def product_attributes(self, product_id: str, marketplace_id: str) -> dict
        # getProductAttrById，data 是 JSON 字符串 → 解析成 dict；失败返回 {}

    # 类目
    def category_roots(self, site_code: str) -> list[dict]
    def category_children(self, site_code: str, parent_id) -> list[dict]
    def category_by_path(self, site_code: str, node_path: str) -> dict
    def category_history(self, shop_id: str, site_code: str) -> list[dict]
    def product_types(self, site_code: str) -> list[str]
    def predict_category(self, shop_id: str, site_code: str, item_name: str) -> dict  # best-effort

    # 站点辅助
    def currency(self, shop_id: str, marketplace_id: str) -> str   # "" 失败
    def logistics(self, shop_id: str, marketplace_id: str) -> list[dict]
    def brands(self, shop_id: str, marketplace_id: str) -> list[dict]

    # 只读检查
    def check_banned_words(self, title="", description="", bullet_point="",
                           search_terms="") -> list[str]   # 失败返回 []（不抛）

    # 唯一写入口
    def save_product(self, payload: dict, op: str = "1") -> dict
        # 构造 ZIP(amazonSave.txt) + multipart（file, op）；成功返回原始 JSON
        # 失败抛 AmazonError(msg)；网关/登录失效给出中文提示
```
兼容旧 API：`normalize_product`、`extract_list` 保留（测试在用）。

### 3.2.0 `overrides` 唯一键名约定（UI→后端，必须两侧一致）

UI 传给 `build_amazon_payload/prepare_save/run_amazon_product` 的 `overrides` 只用下列 **snake_case** 键，
后端负责映射到保存包 camelCase 键；其它键忽略：

```python
{
  "title": str, "bullets": list[str], "description": str, "search_terms": str,
  "brand": str, "manufacturer": str, "sku": str,
  "standard_price": float | str | None, "sale_price": float | str | None,
  "quantity": int | str,
  "category_id": str, "category_path": str,        # nodePath a/b/c
  "marketplace_id": str, "currency": str,
  "fulfillment": "MFN" | "FBA", "handling_time": int | str, "shipping_template": str,
}
```
> UI 只传这些键（值可为空串，空串表示不覆盖）；后端不得要求 UI 传 camelCase 保存包键。

### 3.2 `src/aeclone/services/amazon_payload.py`

```python
SAVE_KEYS = [...]  # 上表 39 个键，保序

def split_bullets(bullet_point: str) -> list[str]     # split("±")，strip，去空
def join_bullets(bullets: list[str]) -> str           # "±".join(非空 strip)

def build_amazon_payload(product: dict, cfg=None, overrides: dict | None = None,
                         *, currency: str = "") -> dict
    # product = editSp 的 product（或列表行）；overrides 见 3.4
    # 规则：
    #  - 只取 SAVE_KEYS；数值字段转 int/float；price<=0 → None
    #  - bulletPoint 由 overrides["bullets"] join("±")；description 原样（HTML）
    #  - site/marketPlaceId 变更时同步 currency，并把 productAttr 中
    #    所有 "marketplace_id"/"marketplaceId" 值替换为新 marketPlaceId（深度遍历 JSON）
    #  - categoryId/categoryBrowsePath 由 overrides 覆盖（先 trim）
    #  - quantity/isVariation/gpsrSafetyAttestation/skuTitle 转 int

def validate_amazon_payload(payload: dict, *, product: dict | None = None,
                            site_meta: dict | None = None) -> list[str]
    # 返回「阻断项」字符串列表；空列表=可保存（见第 6 节规则）

def build_warnings(payload: dict, *, product: dict | None = None) -> list[str]
    # 非阻断提醒（标题>150 字符、卖点<3、缺图片、类目未分类、无品牌等）

def apply_site(payload: dict, marketplace_id: str, currency: str,
               site_meta: dict) -> dict
```

### 3.3 `src/aeclone/services/amazon_prompts.py`

```python
SITE_META: dict[str, dict]
def language_for_site(marketplace_id: str) -> str
def site_meta(marketplace_id: str) -> dict
def build_title_prompt(product, cfg, marketplace_id) -> str
def build_bullets_prompt(product, cfg, marketplace_id, count=5) -> str
def build_description_prompt(product, cfg, marketplace_id) -> str
def build_search_terms_prompt(product, cfg, marketplace_id) -> str
def parse_bullets(text: str, count=5) -> list[str]   # 支持 JSON 数组/编号/行分隔/`●` 分隔
def parse_search_terms(text: str) -> str             # 小写、去重、空格连接、≤250 字节
def DEFAULT_PROMPTS: dict[str, str]                  # 可在设置页覆盖
```
提示词硬约束（写入 system prompt + 本地后处理二次校验）：
- 标题：站点语言；≤200 字符（超过自动截断到词边界）；不得含中文（非中文站点）、emoji、HTML、促销词、
  联系方式、竞品品牌；保留原文真实型号/规格，不编造认证与参数。
- 五点：**恰好 `count` 条（默认 5，上限 10）**，每条 ≤500 字符（建议 150–250；schema 硬上限 700），单条不换行、不含 `±`/`●`/emoji/
  HTML/联系方式/促销词/绝对化表述；以「利益点」开头（可用 `FEATURE:` 前缀），自然融入关键词。
- 描述：HTML 片段（只用 `<p><ul><li><b><br>`），建议 ≤2000 字符（schema 硬上限 20000 字节）；3–5 段 + 参数列表；禁 `<script>/<style>/<img>`、
  联系方式、网址、促销、医疗/绝对化表述。
- Search Terms：建议 ≤250 **字节**（UTF-8；schema 硬上限 500 字符 / 2000 字节），小写、空格分隔、去重、不含品牌词/他人品牌/"a the for with" 等停用词、
  不含标点与单复数重复。
- 所有 AI 结果必须在 UI 中「可编辑」；保存前二次本地校验。

### 3.4 `src/aeclone/services/amazon_pipeline.py`（核心）

```python
ENV_NO_SAVE = "AURORA_NO_SAVE"; ENV_TEST = "AURORA_TEST"
OP_SAVE="1"; OP_MOVE_PENDING="3"; OP_PUBLISH="2"
OP_LABELS = {"1":"保存草稿","3":"保存并移入待发布","2":"保存并发布"}

@dataclass AmazonStep: name:str; ok:bool; detail:str=""
@dataclass AmazonRunResult:
    product_id:str=""; ok:bool=False; error:str=""; steps:list[AmazonStep]=field(default_factory=list)
    preview:bool=False; saved:bool=False; issues:list[str]=field(default_factory=list)
    warnings:list[str]=field(default_factory=list); title:str=""; bullets:list[str]=field(default_factory=list)
    description:str=""; search_terms:str=""; site:str=""; site_code:str=""; currency:str=""
    category_id:str=""; op:str=OP_SAVE; banned_words:list[str]=field(default_factory=list)
    save_result:dict=field(default_factory=dict); ai_used:bool=False
    @property
    def completion_label(self)->str
    def log(self)->str

def forced_preview() -> bool

def prepare_save(product_id:str, cfg, overrides:dict|None=None,
                 client:AmazonClient|None=None) -> tuple[dict, dict, list[str], list[str]]
    # (detail, payload, issues, warnings)；只读

def run_amazon_product(product_id: str, cfg, overrides: dict | None = None,
                       *, dry_run: bool = True, use_ai: bool = False,
                       op: str = OP_SAVE, client: AmazonClient | None = None,
                       confirm_write: bool = False) -> AmazonRunResult
    # 步骤：① 连接/读详情 ② 站点+币种 ③ 类目校验 ④ AI 生成（use_ai 时，overrides 已有值不覆盖）
    #      ⑤ 组装保存包 ⑥ 必填/合规校验 ⑦ 违禁词提示 ⑧ 预演 或 保存(op)
    # 任何异常 → ok=False + 失败步骤，不静默吞
    # 真实保存：dry_run=False 且 confirm_write=True 且非 forced_preview 且通过
    # core.guard.require(cfg) 才调用 save_product；save_to_amazon 必须复用传入的 client
    # （禁止 pipeline 内部另建真实客户端——2026-11-22 曾因此发生一次脚本误写）。

def ai_generate_field(product: dict, cfg, field: str, marketplace_id: str = "",
                      count: int = 5) -> tuple[object, list[str]]
    # field ∈ title/bullets/description/search_terms；返回 (value, notes)
def ai_generate_all(product: dict, cfg, marketplace_id: str = "",
                    count: int = 5) -> tuple[dict, list[str]]
    # {"title":..,"bullets":[..],"description":..,"search_terms":..}; UI「AI 一键生成」用
```
AI 调用复用 `ApiClient(cfg.api_base, cfg.api_key, cfg.timeout).chat(model_of(cfg,"text"), ...)`；
每字段独立 try/except，失败只在 notes 里提示，不阻断其它字段。

### 3.5 `src/aeclone/services/amazon_publish.py`

```python
from ..integrations.amazon import AmazonClient, AmazonError
OP_SAVE="1"; OP_MOVE_PENDING="3"; OP_PUBLISH="2"
def make_client(cfg) -> AmazonClient          # Cookie 优先级见 §0
def save_to_amazon(product_id, cfg, overrides=None, allow_invalid=False,
                   op=OP_SAVE, client: AmazonClient | None = None) -> dict
    # 唯一真实写收口：forced_preview/授权失败 → RuntimeError；issues 且非 allow_invalid → ValueError
    # client 非空时必须复用它（测试/脚本注入 fake 时绝不可新建真实客户端）
    # 返回 {"result":..., "issues":[...], "warnings":[...], "payload":{...}, "product_id":...}
```

### 3.6 `src/aeclone/config.py` 新增 DEFAULTS（Amazon 段）

```python
"amazon_cookies": "",
"amazon_shop_id": "",
"amazon_shop_name": "",
"amazon_site": "",              # marketPlaceId
"amazon_site_code": "",         # US/CA/...
"amazon_site_name": "",
"amazon_test_product_id": "",
"amazon_list_state": "draft",
"amazon_category_id": "",
"amazon_category_path": "",     # nodePath a/b/c
"amazon_category_name": "",
"amazon_last_category_required": "",
"amazon_price": "",
"amazon_sale_price": "",
"amazon_quantity": 0,
"amazon_fulfillment": "MFN",    # MFN/FBA
"amazon_handling_time": 2,
"amazon_shipping_template": "",
"amazon_brand": "",
"amazon_manufacturer": "",
"amazon_sku": "",
"amazon_bullet_count": 5,   # AI 生成条数（5）；加载已有商品最多 10 条，保存不得截断
"amazon_op": 1,
"amazon_use_ai": False,
"amazon_ai_preset": "generic",
"amazon_title_prompt": "",        # 空=内置
"amazon_bullet_prompt": "",
"amazon_description_prompt": "",
"amazon_search_terms_prompt": "",
"amazon_last_status": "",
"amazon_autofill_attrs": True,     # 保存/发布前自动补齐缺失的必填属性（只补缺失，不覆盖已有）
```
> `config.py` 只由 backend 修改；UI 只读写这些键。

---

## 4. UI 规范（只由 `amazon-ui` 修改 `ui/app.py` / `ui/navigation.py` / `platforms.py`）

### 4.1 导航：Amazon 平台「去掉不必要的 UI」

`platforms.AMAZON.pages` 改为（保持页面实现复用 + 新增 13 号）：
| index | label | group | feature |
|---|---|---|---|
| 0 | 概览 | 工作空间 | listing |
| **13** | **Amazon 刊登** | 刊登 | listing |
| 10 | 任务与日志 | 运行 | listing |
| 11 | 设置 | 工具 | listing |

从 Amazon 导航移除：店铺与登录(1)、类目与属性(2)、AI 文案(3)、内容审查(4)、AI 图片(5)、
草稿与待发布(8)、图片工作台(9)。这些页面实现保留给其它平台，不做删除。
> Alibaba/其它平台导航不变。`navigation.DESTINATIONS` 增加
> `Destination(13, "刊登", "Amazon 刊登", "PUBLISH", "Amazon 采集箱编辑、站点、五点描述、AI、保存")`；
> `platforms._KEYWORDS[13]` 增加搜索别名（amazon 采集箱 站点 五点 卖点 bullet 类目 价格 保存 发布 AI 翻译）。

### 4.2 `_switch` 分发表

`pages = [..., self._p_alibaba_listing, self._p_amazon_listing]`，`idx % len(pages)`（13 → 新页面）。

### 4.3 新页面 13 `_p_amazon_listing` + `_amazon_cards()`

页面头：`page_header("Amazon 刊登", "采集箱商品 → 站点/类目 → 五点描述与 AI 文案 → 预演 → 保存")`
+ 单行步骤提示 + 以下卡片（全部使用现有 `card()/muted()/h1()/page_header()` 风格与主题变量）：

**① 连接与店铺**
- `amazon_cookies` 多行 TextField（宽 520，密文不必，可粘贴 `a=1; b=2`）；按钮「读取本机 Cookie」「测试连接」
- 店铺 Dropdown（`client.shops()`）；站点 Dropdown（`client.sites(shop)`，显示 `加拿大 · CAD · en_CA`）
- 状态文本（成功绿/失败红），保存 cfg（`amazon_shop_id/site/site_code/site_name`）
- 「测试连接」= `check_login()` + `sites()` + 列表计数，显示 `采集箱 N / 待发布 N / 在线 N`
- 默认 Cookie：若 `amazon_cookies` 空，用 `manual_cookies` 或本机浏览器（与 backend 一致），在卡片上显示来源

**② 采集箱商品**（核心）
- 状态切换：`采集箱(draft) / 待发布(offline) / 在线(online)`，按钮上带计数
- 搜索框 + 「刷新」「上一页」「下一页」「第 x/y 页 · 共 N 个」
- 列表（每行可点击选中，选中行高亮）：标题（截断 64 字）、SKU、站点名、`价格+币种`、库存、
  `errMsg/warningMsg` 红色/黄色小标签；行尾「编辑」按钮 = 载入表单
- 选中商品后异步 `product_detail` → 填充 ③④⑤ 表单；显示「已载入：id · 标题」
- 失败（无 Cookie/登录失效/店铺为空）给出明确可操作提示，不静默

**③ 站点与类目**
- 只读显示当前站点（来自商品/下拉）：`站点 CA · 加拿大 · CAD · 英语`
- 类目：顶层 Dropdown（17 个大类）→ 子类目列表（逐级下钻，leaf 才可选）→ `nodePath` 面包屑显示；
  选叶子后写 `amazon_category_id/path/name`
- 「最近使用类目」Dropdown（`category_history`）；选择即填入
- 「AI/平台推荐产品类型」按钮（`predict_category`，best-effort，只显示建议不自动改）
- 手动路径 TextField + 「校验路径」（`category_by_path`），失败红字提示
- 变更站点时：清空旧类目选择并提示「需为新站点重新选择类目」（除非商品原本就在该站点）

**④ 标题与卖点（AI）**
- 标题：多行 TextField（2 行，宽 760）+ 实时计数 `n/200`（>200 红字）+ 「AI 生成标题」
- 五点描述：5 个多行 TextField（每个 2 行，宽 760，独立计数 `n/500`，超过红字）+ 每项「AI」按钮 +
  「AI 生成五点」「清空五点」；`amazon_bullet_count` 支持 3–5（默认 5）
- 产品描述：多行 TextField（8 行）+ `n/2000` + 「AI 生成描述」；保存时按 HTML 处理（纯文本自动包 `<p>`）
- Search Terms：多行 TextField（2 行）+ 字节计数 `n/250 bytes` + 「AI 生成关键词」
- 「AI 一键生成全部（付费）」按钮：先弹确认（显示当前模型/预估成本，来自 `_price_of` 或用固定提示），
  确认后调用 `amazon_pipeline.ai_generate_all` 并填入 4 个区域；AI 开关默认关（`amazon_use_ai`）
- 所有计数实时更新；AI 生成有 loading 状态与失败提示，不阻塞界面

**⑤ 价格 / 库存 / 物流**
- 标准价 TextField + 币种只读文本（来自站点）；促销价 TextField（可空，< 标准价）
- 数量 TextField（int）；配送方式 Dropdown `MFN/FBA`；处理时效 TextField（天）；
  运费模板 Dropdown（`client.logistics`，显示 templateName，值 templateId）；
  品牌 / 制造商 / SKU TextField（预填商品值）
- 图片只读摘要：`主图 1 张 + 附加图 N 张`（可显示首图缩略 `ft.Image`，失败不影响）

**⑥ 预演与保存**
- 操作 Dropdown：`保存草稿(op=1)`（默认）/ `保存并移入待发布(op=3)` / `保存并发布(op=2)`
- 按钮：「预演（不保存、不花钱）」= `run_amazon_product(dry_run=True)`；
  「保存」= 二次确认弹窗（列出商品 id/标题/站点/op/校验问题）→ `run_amazon_product(dry_run=False, op=...)`
- 结果区：步骤 ✓/✗ 列表（绿/红）、issues 红字列表、warnings 黄字、payload 摘要
  （标题长度、卖点数、描述长度、Search Terms 字节、价格、图片数）
- 保存成功：toast + `self._log` 写「任务与日志」；失败：显示 `result.error` 与该商品 `msg`
- 真实保存前必须 `core.guard.require`；`AURORA_NO_SAVE/AURORA_TEST` 存在时按钮文案显示「预演模式」

**⑦ 采集箱快捷操作**（可选，若时间允许）：列表行尾「移入待发布」按钮（op=3，需二次确认）。

### 4.4 Amazon 平台其它页面的守卫（防误用速卖通管线）

- 概览页（0）：`is_amazon` 时数据改为 Amazon 采集箱/待发布/在线/发布失败；「开始一次任务」按钮改为
  「去 Amazon 刊登」跳 13；「启动任务」/⌘R/定时器在 Amazon 平台一律跳 13 并写 WARN 日志（照抄 Alibaba 守卫）。
- 侧栏「启动任务」：Amazon 平台文案「去 Amazon 刊登」。
- 8 号发布队列若被其它入口打开（如旧配置 start_page=8），Amazon 平台 `_switch` 无该页 → 自动回首页；
  另在 `_start/_run_pipeline/_publish_one` 加 Amazon 平台守卫，禁止走 SMT 管线。
- 设置页（11）保留；`店铺与登录`（1）在 Amazon 平台不可达，但 `_p_store` 保留。

### 4.5 UI 验收（按钮必须可点且有效果）

| 按钮 | 验收 |
|---|---|
| 读取本机 Cookie | 点击后填入/提示来源；失败不崩溃 |
| 测试连接 | 显示登录+店铺+站点+三个计数；失败中文提示 |
| 刷新 / 上一页 / 下一页 | 列表与页码变化；空数据有空状态 |
| 商品行「编辑」 | 载入详情并填充 ③④⑤ |
| 状态切换 采集箱/待发布/在线 | 计数正确、列表切换 |
| 顶层类目/子类目/最近使用/校验路径 | 正确写 cfg 并显示 breadcrumb |
| AI 生成标题/五点/描述/关键词/全部 | 付费确认 → 填入 → 计数更新；无 API Key 时提示 |
| 预演 | 显示步骤与 issues，不写任何数据（可断言 `saved=false`） |
| 保存 | 二次确认后调用；`AURORA_NO_SAVE` 下拒绝并提示 |
| 去 Amazon 刊登（概览） | 切到 13 页 |

---

## 5. AI 接入方式（复用现有底座，不新增依赖）

- 模型：`from aeclone.services import model_of` → `model_of(cfg, "text")`；客户端
  `ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)`；`client.chat(model, messages, max_tokens=...)`。
- 一次「AI 一键生成」= 4 次调用（标题/五点/描述/关键词），每字段独立 try/except；UI 显示预估成本
  （`_price_of("qwen-plus")` 或 token 计价提示）。默认 `amazon_use_ai=False`，必须显式开启+确认。
- 输出解析：`parse_bullets` 兼容 JSON 数组、`1.`/`-`/`●` 分隔、纯换行；解析后不足补空、超出截断，
  每条再跑长度/字符清洗（去 emoji、`±`、HTML、中文（非中文站点））。
- AI 失败不阻断「预演」：保留用户手填内容继续校验保存。
- 提示词模板放 `amazon_prompts.DEFAULT_PROMPTS`，设置页/刊登页可覆盖（cfg 的 `amazon_*_prompt`）。

---

## 6. 校验规则（`validate_amazon_payload`）

**schema 实测上限（来自 `getAttrJsUrl` 下发的产品类型 schema，CA/OUTDOOR_LIVING）**
- `item_name.0.value`：`maxLength=200`
- `bullet_point.0.value`：`maxItems=10`、`maxLength=700`
- `product_description.0.value`：`maxUtf8ByteLength=20000`（required）
- `generic_keyword.0.value`：`maxLength=500`、`maxUtf8ByteLength=2000`
> 这些是 Amazon 产品类型 schema 的硬上限；UI 默认仍按「5 条卖点 / 每条建议 ≤500 字符」生成，
> 但**加载/保存已有商品时不得截断**：已有卖点最多 10 条原样保留。

**阻断（建议 `allow_invalid=False` 的保存必须拒绝）**
1. `title` 为空；`> 200` 字符（按站点语言字符数，非字节）
2. 非中文站点文案含中文字符（标题/卖点/描述/关键词任一）
3. 卖点数量 `> 10`；任一条 `> 700` 字符；含 `±`/`●`/HTML/emoji；
   **AI 生成的**卖点少于 3 条（AI 输出必须 5 条，解析不足则补齐/提示）
4. `description` 必填、字节数 `> 20000`、含 `<script|style|iframe>`
5. `searchTerms` 非空时 `> 500` 字符或 `> 2000` 字节（UTF-8）
6. `marketPlaceId` 为空或不在账号店铺站点列表（无法远程校验时用 SITE_META）
7. `categoryId` 为空/`None`/非数字；`categoryBrowsePath` 末段与 `categoryId` 不一致
8. `sku` 为空；`brand` 或 `manufacturer` 为空
9. `mainImage` 为空/非 http(s)
10. 价格：`standardPrice` 非空时 ≤0；`saleSalePrice` 非空且 ≥ `standardPrice`
11. 促销词/联系方式/绝对化词（`best `、`100%`、`free shipping`、`sale`、邮箱、电话、http 链接、
    `guarantee` 等）命中 → 阻断（可配置白名单）

**提醒（warnings，不阻断）**
- 标题 < 30 或 > 150 字符；卖点 < 5 或 > 5（Amazon 建议 5 条）；任一条卖点 > 500 字符
- `searchTerms` > 250 字节；描述 > 2000 字符
- 类目为「未分类」/`fullCid` 为空；无图片附加图；库存 0；价格 0（草稿允许）
- `productAttr` 为空或为 `{}`；品牌为 `Generic`（Amazon 可能要求品牌备案）
- 违禁词接口命中（只提示）
- 站点 ≠ 商品原站点（提示类目需重选）

---

## 7. 安全与真实验证边界

- 默认一切操作 **dry-run**：预演不调用 `save_product`，不写任何数据。
- 真实写唯一入口 `services.amazon_publish.save_to_amazon`（UI 与 pipeline 必须都走它），
  且 `run_amazon_product(dry_run=False)` 必须同时 `confirm_write=True`（防脚本/测试误写）：
  - `AURORA_NO_SAVE`/`AURORA_TEST` 存在 → 直接拒绝
  - `core.guard.require(cfg)` 授权校验（`AURORA_SKIP_LICENSE=1` 放行，测试用）
  - 校验 issues 非空且 `allow_invalid=False` → 拒绝
- **真实写入次数（用户已授权）**：
  1. 1 次 `op=1` 保存，商品 `220114595363182310`（不变更内容，验证往返一致性）
  2. 1 次 `op=1` 保存，同商品，写入 AI 生成的标题/五点/描述/关键词（验证真实编辑闭环）
  3. 可选 1 次 `op=3` 移入待发布（需 Lead 明确批准后再做；op=2 发布 v1 不测）
  每次写前记录 `editSp` 快照，写后回读对比，证据写 `docs/amazon-write-verification.json`。
- 所有验证脚本默认只读；真实写入需显式参数 `--allow-write` 且 Lead 批准。

---

## 8. 测试与验收标准

1. **离线**：新增 `tests/test_amazon_payload.py`、`test_amazon_pipeline.py`、`test_amazon_prompts.py`、
   `test_amazon_client.py`（mock requests）；加入 `tools/verify_regressions.py` + `tools/test_offline.py`。
2. **真实只读**：`tools/verify_amazon.py` 覆盖登录/店铺/站点/三种列表/详情/属性/类目树+路径/历史/物流/
   币种/品牌/违禁词/预演，全部记录 JSON 证据；不允许任何写操作。
3. **真实写**：按 §7 执行 1–2 次 op=1，写后回读对比；`docs/amazon-write-verification.json`。
4. **UI**：`tools/ui_sandbox.py` 渲染 13 页不崩溃；`tools/button_audit.py` 遍历 13 页所有按钮可调用；
   截图 `docs/design/amazon-listing-page.png`（沙箱截图）。
5. **回归**：`tools/verify_regressions.py`、`tools/test_offline.py`、`python -m compileall -q src` 全绿。
6. **文档**：本设计 + `docs/AMAZON_VERIFICATION.md`（结果、证据、限制）+ `docs/amazon-*.json`。

---

## 9. 文件归属（写作用域，禁止越界）

| 负责人 | 文件 |
|---|---|
| Lead | `docs/AMAZON_DESIGN.md`、`docs/AMAZON_VERIFICATION.md`、最终集成/真写验证、`docs/regression-verification.json` |
| backend (`amazon-core`) | `src/aeclone/integrations/amazon.py`、`src/aeclone/services/amazon_payload.py`、`amazon_prompts.py`、`amazon_pipeline.py`、`amazon_publish.py`、`src/aeclone/config.py`（仅新增 Amazon DEFAULTS）、`tests/test_amazon_payload.py`、`tests/test_amazon_prompts.py`、`tests/test_amazon_pipeline.py`、`tests/test_amazon_client.py`、`tools/amazon_live_read.py` |
| UI (`amazon-ui`) | `src/aeclone/ui/app.py`、`src/aeclone/ui/navigation.py`、`src/aeclone/platforms.py`、`tests/test_amazon_ui_page.py`（可新建） |
| verifier (`amazon-verifier`) | `tools/verify_amazon.py`、`tools/amazon_ui_shots.py`、`tools/verify_regressions.py`、`tools/test_offline.py`、`tests/test_amazon_contract.py`、`docs/amazon-readonly-verification.json`、`docs/AMAZON_VERIFICATION.md`（与 Lead 协作，Lead 终审） |

**禁止**：任何文件写入真实 Cookie / `.tmp-probe/`；修改 Alibaba 既有语义；删除既有测试。

---

## 10. 已知限制（v1 明确不做）

- 不编辑「产品属性」全量表单（保存时原样回写 `productAttr`；类目属性编辑留待 v2，UI 显示属性数量/原始 JSON 入口）
- 不编辑变种（variations 原样保留；`isVariation` 不变）
- 不做图片上传/替换（保留原图 URL；AI 图片写回 Amazon 留待 v2）
- 不做定时发布 op=5、不做多变种 op=6
- 不做在线商品在线更新（op=2 仅对采集箱/待发布验证；在线更新不在本轮）
- 类目只能逐级浏览 + 最近使用 + 手动路径校验（不做全站类目索引搜索）

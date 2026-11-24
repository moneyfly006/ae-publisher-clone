"""TikTok Shop 保存包（``tiktokSave.txt``）构造、站点切换与保存前校验。

事实来源：docs/TIKTOK_DESIGN.md §1.5（保存包字段）、§1.6（站点限额）、
§3.2（模块接口）、§3.4（overrides 键名）、§5（校验规则）、§8（前端 op 语义）。

关键约定
--------
- 保存包采用 **详情深拷贝原样透传 + snake_case overrides 映射**：
  详情里未知的 camelCase 键全部保留（避免破坏服务端必填结构），
  只对 overrides 指定的字段与少量数值/列表字段做规范化；
- 保存前必须删除 ``variationBos``（前端 ``ve()`` 实测）；
- ``overrides`` 只认 docs §3.4 的 snake_case 键，空串/None 表示「不覆盖」；
- ``siteIds`` / 类目 / 仓库属于「店铺×站点」维度：切站点后类目与仓库必须重选
  （``apply_site`` 负责清空）；
- 校验只返回中文「阻断项」列表，空列表=可保存；限额可从 ``getLimitBySite``
  注入，缺省 ``DEFAULT_LIMITS`` 与 §1.6 实测一致。
"""
from __future__ import annotations

import copy
import json
import re

from .tiktok_prompts import (
    DESCRIPTION_MAX_CHARS,
    KEY_FEATURES_MAX,
    KEY_FEATURES_MIN,
    KEY_FEATURES_TOTAL_MAX,
    SEARCH_TERMS_MAX,
    SEARCH_TERMS_TOTAL_MAX,
    SITE_META,
    TITLE_MAX,
    TITLE_MIN,
    build_attributes_prompt,
    has_chinese,
    html_description,
    is_us_site,
    language_for_site,
    normalize_site,
    parse_attributes_reply,
    site_meta as prompt_site_meta,
    strip_html,
)

# 前端 op 语义（docs §1.5；与 integrations.tiktok 的 OP_* 取值一致）
OP_SAVE = "1"
OP_PUBLISH = "2"
OP_MOVE_PENDING = "3"
OP_SCHEDULE = "5"

# §1.5 顶层键（保序；用于契约测试与「缺失补齐」，未知键不删除）
SAVE_KEYS: list[str] = [
    # 标识/状态
    "id", "op", "shopId", "site", "siteIds", "dxmState", "dxmOfflineState",
    "productStatus", "preSaleFlag", "productId",
    # 类目
    "categoryId", "categoryList", "isNewCategory",
    # 文案
    "productName", "description", "keyProductFeatures", "searchTerms",
    # 刊登类型/变种
    "productType", "option1", "option1Value", "option2", "option2Value",
    "option3", "option3Value", "skuImgs", "variationList",
    # 图片/视频
    "mainImage", "draftImgUrl", "sizeChart", "videoId", "videoData",
    # 物流/保修
    "packageWeight", "packageLength", "packageWidth", "packageHeight",
    "packageDimensionUnit", "packageDimensionMode", "warehouseId", "isCodOpen",
    # 店小秘信息/资质
    "sourceUrl", "fullCid", "productUnit", "lotNum", "packageType",
    "productAttributes", "productCertifications", "brandInfo", "brandId",
    "manufacturerId", "responsiblePersonId", "productCertificationsRules",
]

# 站点限额（docs §1.6 实测原样；键名与 getLimitBySite 返回一致，可整体注入覆盖）
DEFAULT_LIMITS: dict = {
    "priceMin": 0.01,
    "priceMax": 180000000.0,
    "stockMax": 99999,
    "nameMax": TITLE_MAX,
    "nameMin": TITLE_MIN,
    "descMax": DESCRIPTION_MAX_CHARS,
    "descImgMax": 30,
    "imgCountMax": 9,
    "imgCountMin": 1,
    "packageMin": 1000,
    "packageMid": 1000,
    "packageMax": 1000,
    "variationSizeMax": 100,
    "variationNameMax": 20,
    "variationOptionMax": 50,
}

# overrides 唯一键名（docs §3.4；其它键一律忽略）
OVERRIDE_KEYS: tuple[str, ...] = (
    "title", "description",
    "key_features", "search_terms",
    "category_id", "category_name", "category_list",
    "images", "video_id", "source_url",
    "product_type", "price", "stock", "seller_sku",
    "weight", "length", "width", "height", "dimension_unit",
    "warehouse_id", "product_unit",
    "brand_id", "manufacturer_id", "responsible_person_id",
    "product_attributes", "certifications",
    "variant_edits", "clear_images", "size_chart", "sku_images",
)

# 缺失的 §1.5 顶层键补这个默认值（有值字段保持原样）
_DEFAULTS: dict[str, object] = {
    "id": "", "op": 1, "shopId": "", "site": "", "siteIds": [],
    "dxmState": "draft", "dxmOfflineState": None, "productStatus": None,
    "preSaleFlag": 0, "productId": None,
    "categoryId": "", "categoryList": [], "isNewCategory": 0,
    "productName": "", "description": "", "keyProductFeatures": [], "searchTerms": [],
    "productType": 1, "option1": "", "option1Value": "", "option2": "",
    "option2Value": "", "option3": "", "option3Value": "", "skuImgs": "",
    "variationList": [],
    "mainImage": "", "draftImgUrl": "", "sizeChart": "", "videoId": "", "videoData": None,
    "packageWeight": None, "packageLength": None, "packageWidth": None,
    "packageHeight": None, "packageDimensionUnit": None,
    "packageDimensionMode": "PRODUCT", "warehouseId": "", "isCodOpen": 0,
    "sourceUrl": "", "fullCid": "", "productUnit": "", "lotNum": None,
    "packageType": None, "productAttributes": "", "productCertifications": "",
    "brandInfo": "", "brandId": "", "manufacturerId": "", "responsiblePersonId": "",
    "productCertificationsRules": {},
}

_DIM_UNITS = {
    "CM": "CENTIMETER", "CENTIMETER": "CENTIMETER", "CENTIMETERS": "CENTIMETER",
    "CENTIMETRE": "CENTIMETER", "CENTIMETRES": "CENTIMETER",
    "IN": "INCH", "INCH": "INCH", "INCHES": "INCH",
}

_TAG_NAME_RE = re.compile(r"<\s*/?\s*([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
_ALLOWED_DESC_TAGS = {"p", "ul", "li", "b", "br"}
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_PHONE_RE = re.compile(r"(?<![\d.])[+]?\d[\d\s\-()]{7,}\d(?![\d.])")
_PROMO_RE = re.compile(
    r"(?:\bbest\b|\bcheapest\b|\blowest\s+price\b|\bfree\s+shipping\b|\bhot\s+sale\b|"
    r"\bdiscount\b|\bsale\b|\bpromotion\b|\bcashback\b|\bguarantee(?:d)?\b|"
    r"100\s*%|\bno\.?\s*1\b|\bnumber\s+one\b|\bmiracle\b|\bcure\b|\bfda\s+approved\b)",
    re.I,
)


# ---------------- 基础工具 ----------------
def split_pipe(raw: str) -> list[str]:
    """``url1|url2`` → ``["url1", "url2"]``（去空、去首尾空白）。"""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split("|") if part and part.strip()]


def join_pipe(items) -> str:
    """图片 URL 列表 → ``url1|url2``（去空；第 1 张为主图）。"""
    if not items:
        return ""
    if isinstance(items, str):
        items = split_pipe(items)
    return "|".join(str(x).strip() for x in items if x is not None and str(x).strip())


def _text(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _is_blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _to_float(value) -> float | None:
    try:
        if _is_blank(value):
            return None
        f = float(str(value).strip())
    except Exception:
        return None
    if f != f:  # NaN
        return None
    return f


def _to_int(value, default: int | None = 0) -> int | None:
    try:
        if _is_blank(value):
            return default
        f = float(str(value).strip())
        if f != f:
            return default
        return int(f)
    except Exception:
        return default


def _override(ov: dict, key: str, default=None):
    """取 overrides 值；空串/空白视作「不覆盖」。"""
    if not isinstance(ov, dict) or key not in ov:
        return default
    value = ov.get(key)
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    if isinstance(value, (list, tuple, dict)) and not value:
        return default
    return value


def _cfg_value(cfg, key: str, default=None):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _normalize_unit(value) -> str:
    """尺寸单位归一化：CM/厘米 → CENTIMETER；IN/英寸 → INCH；未知原样大写。"""
    raw = _text(value).upper()
    return _DIM_UNITS.get(raw, raw)


def _json_field(value) -> str:
    """productAttributes / productCertifications：列表/字典 → JSON 字符串；字符串原样。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return str(value)
    return str(value)


def _as_str_list(value) -> list[str]:
    """任意 AI/UI 输出 → 字符串列表（JSON 数组 / 换行 / ``|`` / 逗号分隔）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, (dict, list)):
                out.append(json.dumps(item, ensure_ascii=False))
            elif item is not None and str(item).strip():
                out.append(str(item).strip())
        return out
    raw = str(value).strip()
    if not raw:
        return []
    if raw[:1] in "[{":
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, list):
            return _as_str_list(data)
    parts = re.split(r"[\r\n|;；]+", raw)
    return [part.strip() for part in parts if part.strip()]


def _as_url_list(value) -> list[str]:
    """图片 overrides → URL 列表（列表项内部允许再带 ``|``）。"""
    urls: list[str] = []
    for item in _as_str_list(value):
        urls.extend(split_pipe(item))
    return urls


def _op_int(op) -> int:
    value = _to_int(op, None)
    return value if value is not None else 1


# ---------------- 详情提取 ----------------
def extract_detail_product(detail: dict) -> dict:
    """从详情响应里取出商品字典并**深拷贝**（``data.product`` 优先，其次 ``data`` 本身）。

    支持三种入参形状：
    - 真实详情 ``{"product": {...}, "category": ..., ...}``；
    - 原始响应 ``{"code": 0, "data": {"product": {...}}}``；
    - 直接传商品 dict 或列表行 dict（编辑脚本/单测）。
    """
    if not isinstance(detail, dict):
        return {}
    node: dict = detail
    if "data" in node and "product" not in node:
        data = node.get("data")
        if isinstance(data, dict):
            node = data
        else:
            # 原始响应 envelope 且 data=null（详情读取失败）：没有商品字段
            return {}
    product = node.get("product")
    if isinstance(product, dict):
        return copy.deepcopy(product)
    return copy.deepcopy(node)


# ---------------- 保存包构造 ----------------
def _apply_category(out: dict, ov: dict) -> None:
    """category_id / category_name / category_list → categoryId + categoryList。"""
    cid = _text(_override(ov, "category_id"))
    cname = _text(_override(ov, "category_name"))
    raw_list = _override(ov, "category_list")
    current = out.get("categoryList")
    items: list = list(current) if isinstance(current, list) else []
    if raw_list is not None:
        items = _parse_category_list(raw_list)
    if cid:
        out["categoryId"] = cid
        last = items[-1] if items and isinstance(items[-1], dict) else None
        if last is None or _text(last.get("categoryId")) != cid:
            items = [{"categoryId": cid, "categoryName": cname}]
        elif cname:
            updated = dict(last)
            updated["categoryName"] = cname
            items = list(items[:-1]) + [updated]
        out["isNewCategory"] = 0
    elif cname and items:
        last = items[-1]
        if isinstance(last, dict) and _text(last.get("categoryId")) == _text(out.get("categoryId")):
            updated = dict(last)
            updated["categoryName"] = cname
            items = list(items[:-1]) + [updated]
    if items or raw_list is not None:
        out["categoryList"] = items


def _parse_category_list(raw) -> list:
    if isinstance(raw, list):
        return [copy.deepcopy(x) for x in raw]
    if isinstance(raw, dict):
        return [copy.deepcopy(raw)]
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, list):
        return [copy.deepcopy(x) for x in data]
    if isinstance(data, dict):
        return [copy.deepcopy(data)]
    return []


def _apply_overrides(out: dict, ov: dict, cfg) -> None:
    """docs §3.4 snake_case overrides → 保存包 camelCase 字段（空串/None 不覆盖）。"""
    # ---- 文案 ----
    title = _override(ov, "title")
    if title is not None:
        out["productName"] = _text(title)
        if "title" in out:  # 列表行形状的 title 同步，避免两处不一致
            out["title"] = _text(title)
    elif not _text(out.get("productName")):
        row_title = _text(out.get("title"))
        if row_title:
            out["productName"] = row_title

    description = _override(ov, "description")
    if description is not None:
        out["description"] = html_description(description, allow_images=True)

    features = _override(ov, "key_features")
    if features is not None:
        out["keyProductFeatures"] = _as_str_list(features)

    terms = _override(ov, "search_terms")
    if terms is not None:
        out["searchTerms"] = _as_str_list(terms)

    # ---- 类目 ----
    _apply_category(out, ov)

    # ---- 图片/视频 ----
    images = [] if ov.get("clear_images") else _override(ov, "images")
    if images is not None:
        urls = _as_url_list(images)
        joined = join_pipe(urls)
        out["mainImage"] = joined
        out["draftImgUrl"] = joined
    if "size_chart" in ov:
        out["sizeChart"] = _text(ov["size_chart"])
    if "sku_images" in ov:
        out["skuImgs"] = ov["sku_images"]
    video_id = _override(ov, "video_id")
    if video_id is not None:
        out["videoId"] = _text(video_id)
    source_url = _override(ov, "source_url")
    if source_url is not None:
        out["sourceUrl"] = _text(source_url)

    # ---- 刊登类型 ----
    product_type = _override(ov, "product_type")
    if product_type is not None:
        value = _to_int(product_type, None)
        if value is not None:
            out["productType"] = value

    # ---- 物流：重量/长宽高/单位/仓库（支持 str/float）----
    dims = (("weight", "packageWeight", 3), ("length", "packageLength", 2),
            ("width", "packageWidth", 2), ("height", "packageHeight", 2))
    has_dims = False
    for ov_key, key, digits in dims:
        value = _override(ov, ov_key)
        if value is None and _is_blank(out.get(key)):
            value = _cfg_value(cfg, "tiktok_" + ov_key, "")
        if value is None or _is_blank(value):
            continue
        has_dims = True
        number = _to_float(value)
        out[key] = round(number, digits) if number is not None else value
    unit = _override(ov, "dimension_unit")
    if unit is None and _is_blank(out.get("packageDimensionUnit")):
        unit = _cfg_value(cfg, "tiktok_dimension_unit", "")
    if unit is not None and not _is_blank(unit):
        out["packageDimensionUnit"] = _normalize_unit(unit)
    if has_dims and _is_blank(out.get("packageDimensionUnit")):
        out["packageDimensionUnit"] = "CENTIMETER"

    warehouse_id = _override(ov, "warehouse_id")
    if warehouse_id is None and _is_blank(out.get("warehouseId")):
        warehouse_id = _cfg_value(cfg, "tiktok_warehouse_id", "")
    if warehouse_id is not None:
        out["warehouseId"] = _text(warehouse_id)

    # ---- 店小秘/资质字段 ----
    id_fields = (
        ("brand_id", "brandId", "tiktok_brand_id"),
        ("manufacturer_id", "manufacturerId", "tiktok_manufacturer_id"),
        ("responsible_person_id", "responsiblePersonId", "tiktok_responsible_person_id"),
        ("product_unit", "productUnit", "tiktok_product_unit"),
    )
    for ov_key, key, cfg_key in id_fields:
        value = _override(ov, ov_key)
        if value is None and _is_blank(out.get(key)):
            value = _cfg_value(cfg, cfg_key, "")
        if value is not None:
            out[key] = _text(value)

    attributes = _override(ov, "product_attributes")
    if attributes is None and _is_blank(out.get("productAttributes")):
        attributes = _cfg_value(cfg, "tiktok_product_attributes", "")
    if attributes is not None:
        out["productAttributes"] = _json_field(attributes)

    certifications = _override(ov, "certifications")
    if certifications is not None:
        out["productCertifications"] = _json_field(certifications)

    # ---- 价格/库存/SKU：cfg 只在保存包完全没有价格/库存时兜底 ----
    price = _override(ov, "price")
    if price is None and payload_price(out) is None:
        price = _override({"price": _cfg_value(cfg, "tiktok_price", "")}, "price")
    stock = _override(ov, "stock")
    if stock is None and payload_stock(out) is None:
        stock = _override({"stock": _cfg_value(cfg, "tiktok_stock", "")}, "stock")
    seller_sku = _text(_override(ov, "seller_sku") or "")
    if price is not None or stock is not None or seller_sku:
        out.update(price_stock_overrides(out, price, stock, seller_sku))
    _ensure_seller_skus(out, seller_sku, cfg)


def _ensure_seller_skus(payload: dict, base_sku: str, cfg) -> None:
    """SKU 缺失时按 §5.11 自动生成（``基础SKU`` / ``基础SKU-序号``）。"""
    rows = payload.get("variationList")
    base = (base_sku or _text(payload.get("sellerSku")) or _text(payload.get("sku"))
            or _text(_cfg_value(cfg, "tiktok_sku", "")) or _auto_sku_base(payload))
    if not isinstance(rows, list) or not rows:
        if base and not _text(payload.get("sellerSku")):
            payload["sellerSku"] = base
        return
    unique_rows = [row for row in rows if isinstance(row, dict)]
    for index, row in enumerate(unique_rows):
        if _text(row.get("sellerSku")):
            continue
        row["sellerSku"] = base if len(unique_rows) == 1 else f"{base}-{index + 1}"


def _auto_sku_base(payload: dict) -> str:
    pid = _text(payload.get("id") or payload.get("idStr") or payload.get("productId"))
    return f"AURORA-{pid}" if pid else "AURORA-SKU"


def _row_price(row: dict) -> float | None:
    for key in ("originalPrice", "price", "salePrice"):
        value = _to_float(row.get(key))
        if value is not None:
            return value
    return None


def _row_stock(row: dict) -> int | None:
    for key in ("availableStock", "quantity"):
        value = _to_int(row.get(key), None)
        if value is not None:
            return value
    return None


def _variation_rows(payload: dict) -> list[dict]:
    rows = payload.get("variationList")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def payload_price(payload: dict) -> float | None:
    """保存包里的「单品价格」：variationList 行价优先，其次顶层 price/originalPrice。"""
    rows = _variation_rows(payload)
    if rows:
        return _row_price(rows[0])
    for key in ("price", "originalPrice", "standardPrice"):
        value = _to_float(payload.get(key))
        if value is not None:
            return value
    return None


def payload_stock(payload: dict) -> int | None:
    """保存包里的「单品库存」：variationList 行库存优先，其次顶层 quantity/availableStock。"""
    rows = _variation_rows(payload)
    if rows:
        return _row_stock(rows[0])
    for key in ("quantity", "availableStock"):
        value = _to_int(payload.get(key), None)
        if value is not None:
            return value
    return None


def _normalize_collections(out: dict) -> None:
    """把保存包里必须是数组/JSON 字符串的字段收敛成服务端可接受的形状。"""
    for key in ("keyProductFeatures", "searchTerms"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = _as_str_list(value)
        elif isinstance(value, (list, tuple)):
            out[key] = [str(x).strip() for x in value
                        if x is not None and not isinstance(x, (dict, list))
                        and str(x).strip()]
        elif value is None:
            out[key] = []
    site_ids = out.get("siteIds")
    if isinstance(site_ids, str):
        out["siteIds"] = _as_str_list(site_ids)
    elif isinstance(site_ids, tuple):
        out["siteIds"] = list(site_ids)
    elif site_ids is None:
        out["siteIds"] = []
    category_list = out.get("categoryList")
    if isinstance(category_list, str):
        out["categoryList"] = _parse_category_list(category_list)
    elif category_list is None:
        out["categoryList"] = []
    rows = out.get("variationList")
    if isinstance(rows, str):
        try:
            parsed = json.loads(rows) if rows.strip() else []
        except Exception:
            parsed = []
        out["variationList"] = parsed if isinstance(parsed, list) else []
    elif rows is None:
        out["variationList"] = []
    if not isinstance(out.get("productAttributes"), str):
        out["productAttributes"] = _json_field(out.get("productAttributes"))
    if not isinstance(out.get("productCertifications"), str):
        out["productCertifications"] = _json_field(out.get("productCertifications"))
    if out.get("productCertificationsRules") is None:
        out["productCertificationsRules"] = {}
    for key in ("preSaleFlag", "isCodOpen", "isNewCategory"):
        out[key] = _to_int(out.get(key), 0)


def _normalize_numbers(out: dict) -> None:
    """数值字段规范化（详情/overrides 都可能给 str）：重量/长宽高/价格/库存。"""
    for key, digits in (("packageWeight", 3), ("packageLength", 2),
                        ("packageWidth", 2), ("packageHeight", 2)):
        value = out.get(key)
        if _is_blank(value):
            continue
        number = _to_float(value)
        if number is not None:
            out[key] = round(number, digits)
    for key in ("price", "originalPrice"):
        if _is_blank(out.get(key)):
            continue
        number = _to_float(out.get(key))
        if number is not None:
            out[key] = round(number, 2)
    for key in ("quantity", "availableStock"):
        if _is_blank(out.get(key)):
            continue
        number = _to_int(out.get(key), None)
        if number is not None:
            out[key] = number
    rows = out.get("variationList")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("originalPrice", "price", "salePrice"):
                if _is_blank(row.get(key)):
                    continue
                number = _to_float(row.get(key))
                if number is not None:
                    row[key] = round(number, 2)
            for key in ("availableStock", "quantity"):
                if _is_blank(row.get(key)):
                    continue
                number = _to_int(row.get(key), None)
                if number is not None:
                    row[key] = number


def build_tiktok_payload(detail: dict, cfg=None, overrides: dict | None = None,
                         *, site: str = "", shop_id: str = "",
                         op: str = OP_SAVE) -> dict:
    """由详情（或列表行）+ overrides 构造保存包。

    步骤（docs §3.2）：
    1) ``extract_detail_product`` 深拷贝详情，保留全部未知 camelCase 键，
       缺失的 §1.5 顶层键补默认值（保序）；
    2) 同步 site/shopId/op；站点变化时按 §4.3 清空类目与仓库；
    3) 应用 §3.4 overrides（空串/None 不覆盖），价格/库存/尺寸兼容 str；
    4) 删除 ``variationBos``，规范化数字/数组字段。
    """
    ov = {key: value for key, value in (overrides or {}).items() if key in OVERRIDE_KEYS}
    product = extract_detail_product(detail)
    out: dict = dict(product)

    # 1) 补齐 §1.5 顶层键（未知键保留，顺序在透传键之后）
    for key in SAVE_KEYS:
        if key not in out:
            out[key] = copy.deepcopy(_DEFAULTS.get(key))

    # 2) 站点 / 店铺（arg > 详情 > cfg）
    new_site = normalize_site(
        _text(site) or _text(product.get("site"))
        or _text(_cfg_value(cfg, "tiktok_site", "")))
    if new_site:
        out = apply_site(out, new_site, shop_id=_text(shop_id))
    new_shop = (_text(shop_id) or _text(product.get("shopId"))
                or _text(_cfg_value(cfg, "tiktok_shop_id", "")))
    if new_shop:
        out["shopId"] = new_shop

    # 3) overrides
    _apply_overrides(out, ov, cfg)
    if ov.get("variant_edits"):
        edits = ov["variant_edits"]
        variants = copy.deepcopy(out.get("variationList") or [])
        seen = set()
        for row in variants:
            key = str(row.get("idStr") or row.get("id") or row.get("sellerSku") or "")
            if key not in edits:
                continue
            if key in seen:
                raise ValueError("TikTok 变种标识重复，已阻止不明确的修改")
            seen.add(key)
            for native in ("sellerSku", "originalPrice", "availableStock"):
                if native in edits[key]:
                    row[native] = edits[key][native]
        if seen != set(edits):
            raise ValueError("TikTok 变种已变更，请重新读取商品")
        out["variationList"] = variants

    # 4) 收尾：删 variationBos / 规范化 / op
    out.pop("variationBos", None)
    _normalize_collections(out)
    _normalize_numbers(out)
    out["op"] = _op_int(op)
    out["productType"] = _to_int(out.get("productType"), 1)
    if _is_blank(out.get("packageDimensionUnit")):
        out["packageDimensionUnit"] = "CENTIMETER"

    # 图片兜底：列表行只有 imgUrl、或只有 draftImgUrl 时补齐 mainImage
    if not ov.get("clear_images") and not _text(out.get("mainImage")):
        fallback = _text(out.get("imgUrl")) or join_pipe(split_pipe(out.get("draftImgUrl")))
        if fallback:
            out["mainImage"] = join_pipe(split_pipe(fallback))
    if not _text(out.get("draftImgUrl")) and _text(out.get("mainImage")):
        out["draftImgUrl"] = join_pipe(split_pipe(out.get("mainImage")))

    return out


# ---------------- 站点切换 ----------------
def _sync_site_ids(payload: dict, site: str) -> None:
    """siteIds 若是站点代码列表则同步为新站点；若是不透明 siteShopId 则不动。"""
    ids = payload.get("siteIds")
    if not isinstance(ids, (list, tuple)) or not ids:
        return
    codes = [normalize_site(x) for x in ids if isinstance(x, str)]
    if codes and all(code in SITE_META for code in codes):
        payload["siteIds"] = [site]


def apply_site(payload: dict, site: str, *, shop_id: str = "") -> dict:
    """把保存包切到新站点：同步 site/siteIds；站点变化时清空类目与仓库。

    - ``categoryId``/``categoryList`` 清空（类目属于「店铺×站点」，必须重选）；
    - ``warehouseId`` 清空（仓库按站点/店铺下发）；
    - ``shopId`` 仅在传入时更新；
    - 返回新 dict，不修改入参。
    """
    out = copy.deepcopy(dict(payload or {}))
    new_site = normalize_site(site)
    if not new_site:
        return out
    old_site = normalize_site(out.get("site"))
    out["site"] = new_site
    _sync_site_ids(out, new_site)
    if new_site != old_site:
        out["categoryId"] = ""
        out["categoryList"] = []
        out["isNewCategory"] = 0
        out["warehouseId"] = ""
    if _text(shop_id):
        out["shopId"] = _text(shop_id)
    return out


# ---------------- 价格/库存覆盖 ----------------
def price_stock_overrides(payload: dict, price, stock, sku: str = "") -> dict:
    """改价/改库存（amount 兼容 str/float，stock 兼容 str/int）。

    - 单品（variationList 只有 1 行）：直接改这一行 ``originalPrice``/``availableStock``；
    - 多变种：只有 ``sku`` 能唯一命中一行时才允许改，否则原样返回（避免误改全店）；
    - 没有 variationList 时写顶层 ``price``/``quantity``（单品保存包形状）；
    - 返回新 dict，不修改入参。
    """
    out = copy.deepcopy(dict(payload or {}))
    price_value = _to_float(price)
    price_value = round(price_value, 2) if price_value is not None else None
    stock_value = _to_int(stock, None)
    sku_text = _text(sku)

    rows = _variation_rows(out)
    target: list[dict] = []
    if sku_text:
        matched = [row for row in rows if _text(row.get("sellerSku")) == sku_text]
        if len(matched) == 1:
            target = matched
        elif len(rows) == 1:
            target = rows  # 单品：允许顺带改 SKU
        elif not rows:
            target = []
        else:
            return out  # 多变种且 SKU 不能唯一命中：拒绝批量改价
    elif len(rows) == 1:
        target = rows
    elif rows:
        return out  # 多变种：仅当唯一 SKU 时允许
    else:
        target = []

    for row in target:
        if price_value is not None:
            row["originalPrice"] = price_value
            for key in ("price", "salePrice"):
                if key in row:
                    row[key] = price_value
        if stock_value is not None:
            row["availableStock"] = stock_value
            if "quantity" in row:
                row["quantity"] = stock_value
        if sku_text:
            row["sellerSku"] = sku_text
            if "sku" in row:
                row["sku"] = sku_text

    # 顶层同步：没有 variationList 时写顶层（§3.2「单品：variationList[0]/顶层价格」）；
    # 只有一行时同步顶层已有的 price/quantity（列表行形状），多变种不动顶层。
    if not rows:
        if price_value is not None:
            out["price"] = price_value
        if stock_value is not None:
            out["quantity"] = stock_value
        if sku_text:
            out["sellerSku"] = sku_text
    elif len(rows) == 1:
        if price_value is not None and "price" in out:
            out["price"] = price_value
        if stock_value is not None and "quantity" in out:
            out["quantity"] = stock_value
    return out


# ---------------- 校验 ----------------
def _merge_limits(limits: dict | None) -> dict:
    """合并注入的站点限额（键名同 getLimitBySite，允许追加自定义键）。"""
    merged = dict(DEFAULT_LIMITS)
    if isinstance(limits, dict):
        for key, value in limits.items():
            if value is not None:
                merged[key] = value
    return merged


def _limit_number(limits: dict, key: str, default: float) -> float:
    value = _to_float(limits.get(key))
    return default if value is None else value


def _limit_int(limits: dict, key: str, default: int) -> int:
    value = _to_int(limits.get(key), None)
    return default if value is None else int(value)


def _payload_title(payload: dict) -> str:
    return _text(payload.get("productName") or payload.get("title"))


def _desc_tag_issues(description: str) -> list[str]:
    """描述里不允许的标签和无有效来源的图片。"""
    from .platform_media import html_images

    bad: list[str] = []
    for match in _TAG_NAME_RE.finditer(description):
        name = match.group(1).lower()
        if name not in (*_ALLOWED_DESC_TAGS, "img") and name not in bad:
            bad.append(name)
    if len(re.findall(r"<img\b", description, re.I)) != len(html_images(description)):
        bad.append("img（图片地址无效）")
    return bad


def validate_tiktok_payload(payload: dict, *, limits: dict | None = None,
                            site: str = "", is_global: bool = False,
                            for_publish: bool = False) -> list[str]:
    """保存前校验，返回中文「阻断项」列表（空列表=可保存）。规则见 docs §5。

    ``for_publish=True`` 时追加「仅发布前阻断」的检查（非 US 站点带五点/关键词、
    全球产品语言等），给流水线在发布动作前使用；默认 ``False`` 不阻断草稿保存。

    限额可注入（``getLimitBySite`` 原样传入即可），缺省 ``DEFAULT_LIMITS``（§1.6）。
    """
    p = payload if isinstance(payload, dict) else {}
    lim = _merge_limits(limits)
    issues: list[str] = []

    site_code = normalize_site(_text(site) or _text(p.get("site")))
    meta = prompt_site_meta(site_code) if site_code else {}
    lang = str(meta.get("lang") or "en")
    us_site = is_us_site(site_code) if site_code else False

    # 1) 店铺
    if not _text(p.get("shopId")):
        issues.append("未选择店铺（shopId 为空）：保存前需在店小秘「授权店铺」绑定并选择 TikTok 店铺")

    # 2) 站点
    if not site_code:
        issues.append("未选择站点（site 为空）")
    elif not meta.get("known"):
        issues.append(f"站点 {site_code} 不在支持的 24 个 TikTok 站点列表")

    # 3) 类目（叶子类目才能保存；站点不匹配的类目拒绝）
    category_id = _text(p.get("categoryId"))
    if not category_id or category_id.lower() in ("none", "null", "0"):
        issues.append("未选择叶子类目（categoryId 为空）")
    else:
        if p.get("isLeaf") is not None and _to_int(p.get("isLeaf"), 1) != 1:
            issues.append("当前类目不是叶子类目（isLeaf=0），需要选择最末级类目")
        category_site = normalize_site(_text(p.get("categorySite")))
        if category_site and site_code and category_site != site_code:
            issues.append(f"类目站点 {category_site} 与商品站点 {site_code} 不一致，请重新选择类目")

    # 4) 标题
    title = _payload_title(p)
    name_min = _limit_int(lim, "nameMin", TITLE_MIN)
    name_max = _limit_int(lim, "nameMax", TITLE_MAX)
    if not title:
        issues.append("标题为空")
    elif len(title) < name_min:
        issues.append(f"标题长度 {len(title)} 小于下限 {name_min} 字符")
    elif len(title) > name_max:
        issues.append(f"标题长度 {len(title)} 超过上限 {name_max} 字符")

    # 5) 产品描述
    description = _text(p.get("description"))
    desc_max = _limit_int(lim, "descMax", DESCRIPTION_MAX_CHARS)
    if not strip_html(description):
        issues.append("产品描述为空（TikTok 保存要求非空）")
    elif len(description) > desc_max:
        issues.append(f"产品描述长度 {len(description)} 超过上限 {desc_max} 字符")
    bad_tags = _desc_tag_issues(description) if description else []
    if bad_tags:
        issues.append("产品描述含不允许的 HTML 标签：" + "、".join(bad_tags[:8])
                      + "（支持 <p><ul><li><b><br> 及有效图片）")
    description_images = len(re.findall(r"<img\b", description, re.I))
    max_description_images = _limit_int(lim, "descImgMax", 30)
    if description_images > max_description_images:
        issues.append(f"详情图片最多 {max_description_images} 张")

    # 6) 主图（`|` 分隔，第 1 张为主图，1–9 张，URL 必须 http(s)）
    images = split_pipe(_text(p.get("mainImage")))
    img_min = _limit_int(lim, "imgCountMin", 1)
    img_max = _limit_int(lim, "imgCountMax", 9)
    if len(images) < img_min:
        issues.append(f"主图数量 {len(images)} 少于下限 {img_min}（至少需要 1 张主图）")
    elif len(images) > img_max:
        issues.append(f"主图数量 {len(images)} 超过上限 {img_max}")
    for index, url in enumerate(images, 1):
        if not re.match(r"^https?://", url, re.I):
            issues.append(f"第 {index} 张图片不是 http(s) 地址：{url[:80]}")

    # 7) 价格 / 8) 库存（variationList 行优先；没有变种则看顶层）
    price_min = _limit_number(lim, "priceMin", 0.01)
    price_max = _limit_number(lim, "priceMax", 180000000.0)
    stock_max = _limit_int(lim, "stockMax", 99999)
    rows = _variation_rows(p)

    def _check_price(value: float | None, label: str) -> None:
        if value is None:
            issues.append(f"{label}价格必填")
        elif value < price_min or value > price_max:
            issues.append(f"{label}价格 {value:g} 超出范围 {price_min:g}–{price_max:g}")

    def _check_stock(value: int | None, label: str) -> None:
        if value is None:
            issues.append(f"{label}库存必填（0–{stock_max}）")
        elif value < 0 or value > stock_max:
            issues.append(f"{label}库存 {value} 超出范围 0–{stock_max}")

    if rows:
        for index, row in enumerate(rows, 1):
            _check_price(_row_price(row), f"第 {index} 个 SKU ")
            _check_stock(_row_stock(row), f"第 {index} 个 SKU ")
    else:
        _check_price(payload_price(p), "")
        stock = payload_stock(p)
        if stock is not None and (stock < 0 or stock > stock_max):
            issues.append(f"库存 {stock} 超出范围 0–{stock_max}")

    # 9) 重量 / 尺寸单位
    weight = _to_float(p.get("packageWeight"))
    if weight is None or weight <= 0:
        issues.append("重量必须大于 0（packageWeight 为空或 ≤0，TikTok 物流必填）")
    if for_publish and not _text(p.get("warehouseId")):
        issues.append("未选择仓库（warehouseId 为空），TikTok 发布前必须选择发货仓库")
    unit_raw = _text(p.get("packageDimensionUnit"))
    if unit_raw and _normalize_unit(unit_raw) not in ("CENTIMETER", "INCH"):
        issues.append(f"尺寸单位 {unit_raw} 非法（仅支持 CENTIMETER / INCH）")
    for key, label in (("packageLength", "长度"), ("packageWidth", "宽度"),
                       ("packageHeight", "高度")):
        value = p.get(key)
        if _is_blank(value):
            continue
        number = _to_float(value)
        if number is None or number <= 0:
            issues.append(f"包裹{label}必须大于 0（{key}={value}）")

    # 10) 五点 / 关键词（仅 US/4Seller；其它站点仅发布前阻断）
    kf_max = _limit_int(lim, "keyFeaturesMax", KEY_FEATURES_MAX)
    kf_total_max = _limit_int(lim, "keyFeaturesTotalMax", KEY_FEATURES_TOTAL_MAX)
    st_max = _limit_int(lim, "searchTermsMax", SEARCH_TERMS_MAX)
    st_total_max = _limit_int(lim, "searchTermsTotalMax", SEARCH_TERMS_TOTAL_MAX)
    features = _as_str_list(p.get("keyProductFeatures"))
    terms = _as_str_list(p.get("searchTerms"))
    features_allowed = us_site and not is_global
    terms_allowed = us_site and not is_global
    if features:
        if features_allowed:
            if len(features) > kf_max:
                issues.append(f"五点描述最多 {kf_max} 条（当前 {len(features)}）")
            total = sum(len(item) for item in features)
            if total > kf_total_max:
                issues.append(f"五点描述总长度 {total} 超过上限 {kf_total_max}")
        elif for_publish:
            if is_global:
                issues.append("全球产品不支持五点描述（仅 US/4Seller），发布前请清空")
            else:
                issues.append(f"站点 {site_code or '?'} 不支持五点描述（仅 US/4Seller），发布前请清空")
    if terms:
        if terms_allowed:
            if len(terms) > st_max:
                issues.append(f"搜索关键词最多 {st_max} 个（当前 {len(terms)}）")
            total_terms = len(" ".join(terms))
            if total_terms > st_total_max:
                issues.append(f"搜索关键词总长度 {total_terms} 超过上限 {st_total_max}")
        elif for_publish:
            if is_global:
                issues.append("全球产品不支持搜索关键词（仅 US/4Seller），发布前请清空")
            else:
                issues.append(f"站点 {site_code or '?'} 不支持搜索关键词（仅 US/4Seller），发布前请清空")

    # 10b) 全球产品只允许英文（docs §2）
    if is_global:
        if lang != "en":
            issues.append(f"全球产品只允许英文文案（当前站点语言 {lang}）")
        for label, text in (("标题", title), ("产品描述", strip_html(description))):
            if has_chinese(text):
                issues.append(f"全球产品{label}含中文（只允许英文）")

    # 11) SKU
    if rows:
        for index, row in enumerate(rows, 1):
            if not _text(row.get("sellerSku")):
                issues.append(f"第 {index} 个 SKU 缺少 sellerSku（SKU 必填）")
    elif not _text(p.get("sellerSku") or p.get("sku")):
        issues.append("SKU 必填（sellerSku 为空）")

    # 去重（保持发现顺序）
    deduped: list[str] = []
    for issue in issues:
        if issue not in deduped:
            deduped.append(issue)
    return deduped


def _promo_hits(text: str) -> list[str]:
    """促销/绝对化/联系方式词（仅提示，不阻断）。"""
    s = str(text or "")
    hits: list[str] = []
    for match in _PROMO_RE.finditer(s):
        token = re.sub(r"\s+", " ", match.group(0).strip().lower())
        if token and token not in hits:
            hits.append(token)
    if _EMAIL_RE.search(s):
        hits.append("邮箱")
    if _URL_RE.search(s):
        hits.append("网址")
    if _PHONE_RE.search(s):
        hits.append("电话")
    return hits


def _images_of(payload: dict) -> list[str]:
    return split_pipe(_text(payload.get("mainImage")))


def build_warnings(payload: dict, *, site: str = "") -> list[str]:
    """非阻断提醒（docs §5「警告（不阻断）」）。"""
    p = payload if isinstance(payload, dict) else {}
    warns: list[str] = []
    site_code = normalize_site(_text(site) or _text(p.get("site")))
    meta = prompt_site_meta(site_code) if site_code else {}
    lang = str(meta.get("lang") or "en")
    us_site = is_us_site(site_code) if site_code else False

    title = _payload_title(p)
    if title:
        if len(title) < TITLE_MIN:
            warns.append(f"标题 {len(title)} 字符（TikTok 要求 ≥{TITLE_MIN}，草稿可存，发布前需补足）")
        if has_chinese(title) and lang != "zh":
            warns.append(f"标题含中文字符（站点语言 {lang}），建议用 AI 重新生成该站点语言标题")
        if re.search(r"</?[a-zA-Z]", title):
            warns.append("标题含 HTML 标签（TikTok 不允许）")
        hits = _promo_hits(title)
        if hits:
            warns.append("标题含疑似促销/联系方式词：" + "、".join(hits[:5]))
    else:
        warns.append("标题为空")

    description = _text(p.get("description"))
    if not description:
        warns.append("产品描述为空")
    else:
        plain = strip_html(description)
        if plain and len(plain) < 50:
            warns.append("产品描述过短（建议 3–5 段）")
        if not re.search(r"<ul\b|<li\b", description, re.I):
            warns.append("描述缺少参数列表（建议用 <ul><li> 列出规格参数）")
        if has_chinese(plain) and lang != "zh":
            warns.append(f"描述含中文字符（站点语言 {lang}），发布前需改为该站点语言")
        hits = _promo_hits(plain)
        if hits:
            warns.append("描述含疑似促销/联系方式词：" + "、".join(hits[:5]))

    images = _images_of(p)
    if len(images) < 3:
        warns.append(f"主图仅 {len(images)} 张（建议 1 张主图 + ≥2 张细节图）")
    if not _text(p.get("brandId")) and not _text(p.get("brandInfo")):
        warns.append("未填写品牌（brandId/brandInfo 为空，部分类目必填）")
    if not _text(p.get("productCertifications")):
        warns.append("未上传产品资质/认证（部分类目必填）")
    rules = p.get("productCertificationsRules")
    if isinstance(rules, (dict, list)) and rules and not _text(p.get("productCertifications")):
        warns.append("类目要求资质（productCertificationsRules 非空），但未上传认证")
    if not _text(p.get("warehouseId")):
        warns.append("未选择仓库（warehouseId 为空）")
    weight = _to_float(p.get("packageWeight"))
    if weight is None or weight <= 0:
        warns.append("未填写重量（物流必填）")

    rows = _variation_rows(p)
    if rows:
        stock = payload_stock(p)
        if stock is not None and stock <= 0:
            warns.append("库存为 0（草稿允许，发布前需补库存）")
        for index, row in enumerate(rows, 1):
            if not _text(row.get("sellerSku")):
                warns.append(f"第 {index} 个 SKU 缺少 sellerSku（保存前会自动生成）")
    else:
        stock = payload_stock(p)
        if stock is None:
            warns.append("库存为空（草稿允许，发布前需补库存）")
        elif stock <= 0:
            warns.append("库存为 0（草稿允许，发布前需补库存）")
    price = payload_price(p)
    if price is None or price <= 0:
        warns.append("价格为空或为 0（草稿允许，发布前需设价）")

    features = _as_str_list(p.get("keyProductFeatures"))
    terms = _as_str_list(p.get("searchTerms"))
    if features:
        if not us_site:
            warns.append("当前站点不支持五点描述（仅 US/4Seller），发布前请清空 keyProductFeatures")
        elif len(features) < KEY_FEATURES_MIN:
            warns.append(f"五点描述 {len(features)} 条（TikTok 要求 3–{KEY_FEATURES_MAX} 条）")
    if terms:
        if not us_site:
            warns.append("当前站点不支持搜索关键词（仅 US/4Seller），发布前请清空 searchTerms")
    return warns


# ================= 类目属性：只读加载 / 规范化 / 本地推导 / AI 兜底 =================
#
# 真实接口证据（2026-11-22 只读盘点 6 站点 × 24 叶子，见
# docs/tiktok-attributes-verification.json）：
# - ``POST /api/tiktokCategory/attributeList.json``（``type=3``）返回字段：
#   ``attrId``(str)、``attrName``(站点语言)、``transName``(中文)、``isMandatory``(0/1)、
#   ``isMultipleSelected``(0/1)、``isCustomized``(0/1)、``values``(JSON **字符串**)、
#   ``valueList``(同内容，已解析)、``transValues``(含中文值名)、
#   ``requirementConditions``(条件必填：依赖其它属性取值)；
# - **同一 attrId 在各站点复用**（如 100107=保修类型），但 ``attrName`` 随站点本地化，
#   ``transName`` 是稳定中文名 → 规则匹配用 transName + attrName 双通道；
# - 保存包里 ``productAttributes`` 是 JSON **字符串**，元素形状（前端
#   ``add-B_TV--jq.js`` 逆向，真实字段名）：
#   ``{"attributeId":"100157","attributeName":"Material",
#      "values":[{"valueId":"1000570","valueName":"Cotton"}]}``
#   自定义输入的值没有 ``valueId``（只有 ``valueName``）；品牌是特殊项
#   ``attributeId="-99999"``（由 brandId/brandInfo 维护），属性补全**跳过**。

ATTR_BRAND_ID = "-99999"
ATTR_SHAPE_ENUM = "enum"                   # 只有枚举值
ATTR_SHAPE_CUSTOM = "custom"               # 无枚举，只能自定义填写
ATTR_SHAPE_ENUM_CUSTOM = "enum_or_custom"  # 枚举 + 允许自定义填写

# 规则键 → 属性名匹配词（transName 中文 / attrName 英文双通道，大小写不敏感）
_ATTR_RULE_TOKENS: dict[str, tuple[str, ...]] = {
    "color": ("颜色", "color", "colour", "色彩", "主色"),
    "size": ("尺码", "尺寸", "size", "dimension"),
    "material": ("材质", "成分", "material", "composition", "fabric", "面料"),
    "warranty": ("保修", "质保", "warranty", "guarantee"),
    "model": ("型号", "model"),
    "style": ("风格", "款式", "style"),
    "pattern": ("图案", "花纹", "pattern", "print"),
    "origin": ("原产", "产地", "origin"),
    "brand": ("品牌", "brand", "trademark", "商标"),
    "weight": ("重量", "weight", "净重"),
    "capacity": ("容量", "capacity", "毫升"),
    "power": ("功率", "power", "watt", "瓦"),
    "voltage": ("电压", "voltage", "volt"),
    "plug": ("插头", "plug"),
    "battery": ("电池", "battery"),
    "age": ("年龄", "age"),
    "gender": ("性别", "gender"),
    "season": ("季节", "season"),
    "count": ("数量", "quantity", "每包", "件数"),
    "license": ("许可证", "license", "licence", "认证编号", "证书编号", "certificate"),
}

_ATTR_SOURCE_LABELS = {
    "title": "标题", "description": "描述", "features": "卖点/关键词",
    "attributes": "已有属性", "sku": "SKU", "brand": "品牌",
}

# 推导词表（英文 + 站点语言里可直接命中的常见写法）
_COLOR_WORDS = (
    "black", "white", "red", "blue", "green", "pink", "gray", "grey", "brown",
    "beige", "yellow", "purple", "orange", "gold", "golden", "silver", "navy",
    "khaki", "ivory", "transparent", "clear", "multicolor", "multi-color",
    "rose gold", "dark blue", "light blue", "wine red", "army green",
)
_MATERIAL_WORDS = (
    "cotton", "polyester", "leather", "faux leather", "pu leather", "genuine leather",
    "stainless steel", "steel", "aluminum", "aluminium", "wood", "wooden", "bamboo",
    "plastic", "abs", "ceramic", "glass", "tempered glass", "silicone", "wool",
    "linen", "silk", "nylon", "spandex", "denim", "canvas", "microfiber", "rubber",
    "brass", "copper", "titanium", "paper", "cardboard", "metal", "mesh", "velvet",
    "satin", "acrylic", "resin", "pu", "eva",
)
_STYLE_WORDS = (
    "casual", "sport", "sports", "vintage", "retro", "modern", "classic", "minimalist",
    "cute", "elegant", "luxury", "streetwear", "business", "formal", "bohemian",
    "korean", "japanese", "european", "american", "nordic", "industrial",
)
_PATTERN_WORDS = (
    "solid", "plain", "striped", "stripe", "plaid", "checkered", "floral", "flower",
    "polka dot", "leopard", "animal print", "camo", "camouflage", "geometric",
    "cartoon", "letter", "tie dye", "tie-dye", "patchwork", "sequin",
)
_SEASON_WORDS = ("spring", "summer", "autumn", "fall", "winter", "all season", "four seasons")
_GENDER_WORDS = ("men", "man", "male", "women", "woman", "female", "unisex",
                 "boys", "boy", "girls", "girl", "kids", "baby", "adult")
_AGE_WORDS = ("0-6 months", "6-12 months", "1-2 years", "2-4 years", "3-5 years",
              "kids", "children", "toddler", "baby", "adult", "teen", "newborn")
_ORIGIN_WORDS = (
    "china", "chinese", "united states", "usa", "us", "japan", "korea", "south korea",
    "germany", "vietnam", "thailand", "india", "turkey", "italy", "france",
    "united kingdom", "uk", "spain", "poland", "mexico", "brazil", "indonesia",
    "malaysia", "philippines", "singapore", "australia",
)
_PLUG_WORDS = ("us plug", "eu plug", "uk plug", "au plug", "type a", "type c",
               "type g", "type i", "type f", "type e", "usb", "usb-c", "usb c", "no plug")
_BATTERY_WORDS = ("lithium", "li-ion", "lithium ion", "alkaline", "nimh", "nicd",
                  "aaa", "aa", "cr2032", "18650", "polymer")

# 常见拼写差异（英文枚举名与文案写法不一致时兜底）
_SPELLING_ALIASES = {
    "grey": "gray", "gray": "grey", "colour": "color", "color": "colour",
    "aluminium": "aluminum", "aluminum": "aluminium",
    "licence": "license", "license": "licence",
}

_WARRANTY_RE = re.compile(
    r"(\d+)\s*(?:-|to\s*)?\s*(year|years|yr|yrs|month|months|mo|day|days|年|个月|天)\b",
    re.I)
_MODEL_LABEL_RE = re.compile(
    r"(?:model(?:\s*(?:no|number|#))?|型号|item\s*no|货号|sku)\s*[:：]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-_/]{1,24})", re.I)
_MODEL_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9\-]{2,19}\b")
_SIZE_CONTEXT_RE = re.compile(
    r"(?:size|尺码|尺寸|sizes)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9/,\-.\s]{0,30})", re.I)
_LICENSE_RE = re.compile(
    r"(?:licen[cs]e(?:\s*(?:no|number|#))?|许可证(?:号|编号)?|认证编号|证书编号|"
    r"certificate\s*(?:no|number)?)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9/\-.]{1,40})", re.I)
_POWER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:w|watt|watts|瓦)\b", re.I)
_VOLTAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:v|volt|volts|伏)\b", re.I)
_CAPACITY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(mah|ml|l|liter|litre|oz|gb|tb|加仑)\b", re.I)
_WEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|lb|lbs|oz|克|千克|公斤)\b", re.I)
_COUNT_RE = re.compile(r"(\d+)\s*(pcs|pc|pieces|piece|pack|packs|个|件|只|片)\b", re.I)

# 明显不是型号/值的词（避免把单位、认证当型号）
_MODEL_STOPWORDS = {
    "usb", "led", "ipx", "ipx7", "abs", "eva", "pu", "hd", "4k", "3d", "2d", "ac",
    "dc", "ce", "fda", "rohs", "pcs", "pc", "ml", "kg", "mm", "cm", "inch", "hz",
    "mah", "watt", "volt", "new", "hot", "sale", "2024", "2025", "2026",
}
# 纯「数值+单位」不是型号（500ml / 20w / 30cm / 100g）
_MEASURE_TOKEN_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:ml|l|g|kg|mg|cm|mm|m|w|v|mah|hz|khz|mhz|ghz|oz|lb|lbs|"
    r"pcs|pc|gb|tb|mb|inch|in|ft|h|min|s|%|x)$", re.I)


# ---------------- 属性规范化 ----------------
def _flag(value) -> bool:
    """0/1、"1"、True、"true" → bool。"""
    if isinstance(value, bool):
        return value
    try:
        return int(str(value).strip()) == 1
    except Exception:
        return str(value).strip().lower() in ("true", "yes", "y", "是")


def _json_list(raw) -> list:
    """``values`` / ``transValues`` / ``requirementConditions`` → list（容错 JSON 字符串）。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, dict):
        for key in ("list", "valueList", "values", "data"):
            inner = raw.get(key)
            if isinstance(inner, list):
                return list(inner)
        return [raw]
    text = str(raw).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _attr_value_rows(raw: dict) -> list[dict]:
    """属性枚举值：优先已解析的 ``valueList``，其次 ``values``(JSON 字符串)。"""
    source = raw.get("valueList")
    rows = _json_list(source) if isinstance(source, list) and source else _json_list(raw.get("values"))
    trans_map: dict[str, str] = {}
    for row in _json_list(raw.get("transValues")):
        if isinstance(row, dict):
            vid = _text(row.get("id") or row.get("valueId"))
            tname = _text(row.get("transName") or row.get("trans_name"))
            if vid and tname:
                trans_map[vid] = tname
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            vid = _text(row.get("id") or row.get("valueId") or row.get("value_id"))
            name = _text(row.get("name") or row.get("valueName") or row.get("value"))
            tname = _text(row.get("transName") or trans_map.get(vid))
        else:
            vid, name, tname = "", _text(row), ""
        if not name and not vid:
            continue
        key = vid or name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": vid, "name": name or vid, "transName": tname})
    return out


def normalize_category_attribute(raw: dict, *, site: str = "",
                                 category_id: str = "") -> dict:
    """单个类目属性 → 统一形状（幂等；已是统一形状时原样补字段）。

    返回 ``{attrId, attrName, transName, label, isMandatory, isMultipleSelected,
    isCustomized, values:[{id,name,transName}], valueShape, isConditional,
    site, categoryId}``，并带 UI 兼容别名 ``required``/``name``/``options``。
    """
    src = raw if isinstance(raw, dict) else {}
    attr_id = _text(src.get("attrId") or src.get("attributeId") or src.get("id"))
    if not attr_id:
        return {}
    attr_name = _text(src.get("attrName") or src.get("attributeName"))
    trans_name = _text(src.get("transName") or src.get("trans_name"))
    values = _attr_value_rows(src)
    is_customized = _flag(src.get("isCustomized") if "isCustomized" in src
                          else src.get("is_customized"))
    mandatory = _flag(src.get("isMandatory") if "isMandatory" in src
                      else src.get("isRequired", src.get("required")))
    multi = _flag(src.get("isMultipleSelected") if "isMultipleSelected" in src
                  else src.get("isMultiple"))
    conditions = _json_list(src.get("requirementConditions")
                            or src.get("requirement_conditions")
                            or src.get("conditions"))
    if values and is_customized:
        shape = ATTR_SHAPE_ENUM_CUSTOM
    elif values:
        shape = ATTR_SHAPE_ENUM
    else:
        shape = ATTR_SHAPE_CUSTOM
    label = trans_name or attr_name or attr_id
    return {
        "attrId": attr_id,
        "attrName": attr_name or label,
        "transName": trans_name,
        "label": label,
        "isMandatory": mandatory,
        "isMultipleSelected": multi,
        "isCustomized": is_customized,
        "values": values,
        "valueShape": shape,
        "isConditional": bool(conditions) or _flag(src.get("isConditional")),
        "conditions": [c for c in conditions if isinstance(c, dict)],
        "site": normalize_site(site or _text(src.get("site"))),
        "categoryId": _text(category_id or src.get("categoryId")),
        # UI 兼容别名（旧页面读 required/name/options）
        "required": mandatory,
        "name": label,
        "options": list(values),
    }


def _normalize_attr_list(attrs) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for raw in attrs or []:
        attr = normalize_category_attribute(raw) if isinstance(raw, dict) else {}
        attr_id = _text(attr.get("attrId"))
        if not attr_id or attr_id in seen:
            continue
        seen.add(attr_id)
        rows.append(attr)
    return rows


def load_category_attributes(site, category_id, client, *, shop_id: str = "-1",
                             cross_border: bool = True, attr_type: int = 3,
                             errors: list[str] | None = None) -> list[dict]:
    """读取并规范化「叶子类目属性」（只读，best-effort）。

    - 调用 ``client.category_attributes``（``type=3`` 才会返回完整属性）；
    - 站点/类目为空、未注入 client、客户端无该方法或接口异常 → 返回 ``[]``，
      不抛异常（不阻断预演）；失败原因写入 ``errors``（中文，供警告展示）；
    - 同一 ``attrId`` 只保留首次出现，保证顺序稳定。
    """
    site_code = normalize_site(site)
    cid = _text(category_id)
    if not site_code or not cid:
        if errors is not None:
            errors.append("属性加载缺少站点或类目（site/categoryId 为空）")
        return []
    fetch = getattr(client, "category_attributes", None) if client is not None else None
    if not callable(fetch):
        if errors is not None:
            errors.append("客户端未提供类目属性接口（category_attributes），已跳过属性补全")
        return []
    try:
        try:
            rows = fetch(site_code, cid, shop_id=_text(shop_id) or "-1",
                         cross_border=bool(cross_border), attr_type=int(attr_type))
        except TypeError:
            # 兼容只接受 (site, category_id) 的轻量替身
            rows = fetch(site_code, cid)
    except Exception as exc:  # noqa: BLE001
        if errors is not None:
            errors.append(f"类目属性接口调用失败（不阻断）：{exc}")
        return []
    if isinstance(rows, dict):
        rows = rows.get("list") or rows.get("attributeList") or []
    if isinstance(rows, str):  # 兼容返回 JSON 文本的轻量替身
        rows = _json_list(rows)
    if not isinstance(rows, (list, tuple)):
        if errors is not None:
            errors.append("类目属性接口返回格式异常（非列表），已跳过属性补全")
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        attr = normalize_category_attribute(raw, site=site_code, category_id=cid)
        attr_id = _text(attr.get("attrId"))
        if not attr_id or attr_id in seen:
            continue
        seen.add(attr_id)
        out.append(attr)
    return out


# ---------------- 商品文本与值匹配 ----------------
def _product_texts(product: dict) -> dict[str, str]:
    """商品可见文本（用于推导属性值）：标题/描述/卖点/SKU/品牌/已有属性。"""
    p = product if isinstance(product, dict) else {}
    texts: dict[str, str] = {}
    texts["title"] = _text(p.get("productName") or p.get("title") or p.get("subject"))
    texts["description"] = strip_html(_text(p.get("description")))
    features = _as_str_list(p.get("keyProductFeatures") or p.get("key_features"))
    terms = _as_str_list(p.get("searchTerms") or p.get("search_terms"))
    texts["features"] = " ".join(features + terms)
    sku_parts: list[str] = [_text(p.get("sellerSku") or p.get("sku"))]
    rows = p.get("variationList")
    if isinstance(rows, list):
        for row in rows[:20]:
            if not isinstance(row, dict):
                continue
            sku_parts.append(_text(row.get("sellerSku")))
            props = row.get("skuProperties")
            if isinstance(props, list):
                for prop in props:
                    if isinstance(prop, dict):
                        sku_parts.append(_text(prop.get("sku_property_name")
                                               or prop.get("attrName") or prop.get("name")))
    texts["sku"] = " ".join(x for x in sku_parts if x)
    texts["brand"] = _text(p.get("brandInfo") or p.get("brand") or p.get("brandId"))
    attr_parts: list[str] = []
    for item in _attribute_items(p.get("productAttributes")):
        attr_parts.append(_text(item.get("attributeName") or item.get("attrName")))
        for value in _item_values(item):
            attr_parts.append(value.get("valueName") or "")
    texts["attributes"] = " ".join(x for x in attr_parts if x)
    return texts


def _attribute_items(raw) -> list[dict]:
    """``productAttributes``（JSON 字符串/列表/字典）→ 元素列表（容错解析）。"""
    if isinstance(raw, (list, tuple)):
        data = list(raw)
    elif isinstance(raw, dict):
        data = [raw]
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except Exception:
            return []
        if isinstance(data, dict):
            data = [data]
    else:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _item_values(item: dict) -> list[dict]:
    """产品属性元素 → ``[{valueId, valueName}]``（兼容 values 列表/JSON/单值写法）。"""
    vals: list[dict] = []
    raw = item.get("values")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if isinstance(raw, list):
        for value in raw:
            if isinstance(value, dict):
                vid = _text(value.get("valueId") or value.get("id") or value.get("value_id"))
                vname = _text(value.get("valueName") or value.get("name")
                              or value.get("value"))
                if vid or vname:
                    vals.append({"valueId": vid, "valueName": vname or vid})
            elif value is not None and str(value).strip():
                vals.append({"valueId": "", "valueName": str(value).strip()})
    if not vals:
        single = _text(item.get("valueName") or item.get("value"))
        if single:
            vals.append({"valueId": _text(item.get("valueId") or item.get("value_id")),
                         "valueName": single})
    return vals


def _existing_attribute_map(product: dict) -> dict[str, list[dict]]:
    """已有 productAttributes → ``{attrId: [{valueId, valueName}]}``（只保留有值的）。"""
    out: dict[str, list[dict]] = {}
    raw = product.get("productAttributes") if isinstance(product, dict) else None
    for item in _attribute_items(raw):
        attr_id = _text(item.get("attributeId") or item.get("attrId") or item.get("id"))
        if not attr_id:
            continue
        vals = _item_values(item)
        if vals:
            out[attr_id] = vals
    return out


def _word_index(text: str, word: str) -> int:
    """整词定位（避免 "cotton" 命中 "cottonseed"、"us" 命中 "business"）。"""
    if not text or not word:
        return -1
    pattern = r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?![a-z0-9])"
    match = re.search(pattern, text.lower())
    return match.start() if match else -1


def _snippet(text: str, index: int, length: int, width: int = 32) -> str:
    start = max(0, index - width // 2)
    end = min(len(text), index + length + width // 2)
    return re.sub(r"\s+", " ", text[start:end]).strip()


def _find_phrase(texts: dict[str, str], words, *, all_hits: bool = False):
    """在标题→描述→卖点→属性→SKU→品牌顺序里找词；返回首个命中或全部命中。"""
    hits: list[dict] = []
    seen: set[str] = set()
    for source in ("title", "description", "features", "attributes", "sku", "brand"):
        text = texts.get(source) or ""
        if not text:
            continue
        for word in words:
            key = word.casefold()
            if key in seen:
                continue
            index = _word_index(text, word)
            if index < 0:
                continue
            seen.add(key)
            hits.append({"token": word, "source": source,
                         "evidence": _snippet(text, index, len(word))})
            if not all_hits:
                return hits
    return hits


def _find_regex(texts: dict[str, str], pattern: re.Pattern, builder) -> list[dict]:
    """按来源顺序跑正则，``builder(match)`` 返回候选 token 列表。"""
    out: list[dict] = []
    seen: set[str] = set()
    for source in ("title", "description", "features", "sku", "attributes"):
        text = texts.get(source) or ""
        if not text:
            continue
        for match in pattern.finditer(text):
            for token in builder(match):
                token = _text(token)
                if not token or token.casefold() in seen:
                    continue
                seen.add(token.casefold())
                out.append({"token": token, "source": source,
                            "evidence": _snippet(text, match.start(),
                                                 max(1, match.end() - match.start()))})
    return out


def _casefold(value) -> str:
    return str(value or "").strip().casefold()


def _compact(value) -> str:
    return re.sub(r"[\s\-_/]+", "", str(value or "")).casefold()


def _match_value(attr: dict, token) -> tuple[dict | None, str]:
    """枚举值匹配：先 id，再 name/transName 精确，再去空格缩写与包含（任务要求 id/name 匹配）。

    返回 ``(value, how)``，``how`` ∈ ``id/name/transName/compact/contains``；
    未命中返回 ``(None, "")``。短值（<3 字符，如尺码 M）只允许精确匹配，避免误配。
    """
    raw = _text(token)
    if not raw:
        return None, ""
    values = [v for v in (attr.get("values") or []) if isinstance(v, dict)]
    for value in values:  # 1) valueId 直接命中
        if _text(value.get("id")) and _text(value.get("id")) == raw:
            return value, "id"
    target = _casefold(raw)
    for value in values:  # 2) name / transName 精确
        if _casefold(value.get("name")) == target:
            return value, "name"
        if _casefold(value.get("transName")) == target:
            return value, "transName"
    alias = _SPELLING_ALIASES.get(target)
    if alias:
        for value in values:
            if _casefold(value.get("name")) == alias:
                return value, "name"
    if len(target) >= 3:  # 3) 去空格/连字符比对（"20W" ↔ "20 W"、"USB-C" ↔ "USB C"）
        compact_target = _compact(raw)
        for value in values:
            for name in (value.get("name"), value.get("transName")):
                if _compact(name) == compact_target:
                    return value, "compact"
        for value in values:  # 4) 包含（两边都 ≥3 字符，短枚举名不参与）
            for name in (value.get("name"), value.get("transName")):
                folded = _casefold(name)
                if len(folded) >= 3 and (target in folded or folded in target):
                    return value, "contains"
    return None, ""


def _rule_key(attr: dict) -> str:
    """属性 → 规则键（transName + attrName 双通道；兼容/适用类不参与推导）。"""
    names = (_casefold(attr.get("transName")) + " " + _casefold(attr.get("attrName"))).strip()
    if not names:
        return ""
    if any(token in names for token in ("兼容", "适用", "compatible", "for ")):
        return ""
    for key, tokens in _ATTR_RULE_TOKENS.items():
        for token in tokens:
            if token.casefold() in names:
                if key == "license" and ("url" in names or "链接" in names):
                    return ""  # 许可证链接不是编号
                return key
    return ""


def _is_boolean_attr(attr: dict) -> bool:
    """布尔型属性（含电池/是否可充电/带磁铁）：值只有 Yes/No，交人工确认更稳。"""
    label = (_text(attr.get("transName")) + " " + _text(attr.get("attrName"))).casefold()
    if not re.search(r"含|是否|带|with |is |has |include", label):
        return False
    names = {_casefold(v.get("name")) for v in (attr.get("values") or [])}
    return bool(names & {"yes", "no", "是", "否"})


def _derive_candidates(key: str, texts: dict[str, str],
                       product: dict) -> list[dict]:
    """规则键 → 候选值（按置信度排序；每项 ``{token, source, evidence}``）。"""
    if key == "color":
        return _find_phrase(texts, _COLOR_WORDS, all_hits=True)[:5]
    if key == "material":
        return _find_phrase(texts, _MATERIAL_WORDS, all_hits=True)[:5]
    if key == "style":
        return _find_phrase(texts, _STYLE_WORDS)[:1]
    if key == "pattern":
        return _find_phrase(texts, _PATTERN_WORDS)[:1]
    if key == "season":
        return _find_phrase(texts, _SEASON_WORDS)[:1]
    if key == "gender":
        return _find_phrase(texts, _GENDER_WORDS)[:2]
    if key == "age":
        return _find_phrase(texts, _AGE_WORDS)[:1]
    if key == "origin":
        return _find_phrase(texts, _ORIGIN_WORDS)[:1]
    if key == "plug":
        return _find_phrase(texts, _PLUG_WORDS)[:1]
    if key == "battery":
        return _find_phrase(texts, _BATTERY_WORDS)[:1]
    if key == "warranty":
        return _warranty_candidates(texts)
    if key == "model":
        return _model_candidates(texts, product)
    if key == "size":
        return _size_candidates(texts)
    if key == "license":
        return _license_candidates(texts)
    if key == "power":
        return _find_regex(texts, _POWER_RE,
                           lambda m: [f"{m.group(1)}W", f"{m.group(1)} W"])[:3]
    if key == "voltage":
        return _find_regex(texts, _VOLTAGE_RE,
                           lambda m: [f"{m.group(1)}V", f"{m.group(1)} V"])[:3]
    if key == "capacity":
        return _find_regex(texts, _CAPACITY_RE,
                           lambda m: [f"{m.group(1)}{m.group(2)}"])[:3]
    if key == "weight":
        return _find_regex(texts, _WEIGHT_RE,
                           lambda m: [f"{m.group(1)}{m.group(2)}"])[:3]
    if key == "count":
        return _find_regex(texts, _COUNT_RE,
                           lambda m: [f"{m.group(1)} {m.group(2)}"])[:3]
    if key == "brand":
        brand = _text(product.get("brandInfo") or product.get("brand"))
        if brand:
            index = 0
            return [{"token": brand, "source": "brand",
                     "evidence": _snippet(brand, index, len(brand))}]
        return []
    return []


def _warranty_candidates(texts: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for source in ("title", "description", "features", "attributes", "sku"):
        text = texts.get(source) or ""
        if not text:
            continue
        low = text.casefold()
        if any(word in low for word in ("lifetime", "终身", "永久")):
            token = "lifetime"
            if token not in seen:
                seen.add(token)
                index = min([i for i in (low.find("lifetime"), low.find("终身"),
                                         low.find("永久")) if i >= 0] or [0])
                out.append({"token": token, "source": source,
                            "evidence": _snippet(text, index, len(token))})
        for match in _WARRANTY_RE.finditer(text):
            number = match.group(1)
            unit = match.group(2).casefold()
            candidates: list[str] = []
            if unit in ("year", "years", "yr", "yrs", "年"):
                candidates = [f"{number} year", f"{number} years", f"{number}年"]
            elif unit in ("month", "months", "mo", "个月"):
                candidates = [f"{number} month", f"{number} months", f"{number}个月"]
                if number.isdigit() and int(number) % 12 == 0:
                    years = int(number) // 12
                    candidates += [f"{years} year", f"{years} years"]
            elif unit in ("day", "days", "天"):
                candidates = [f"{number} day", f"{number} days"]
            for token in candidates:
                key = token.casefold()
                if key in seen:
                    continue
                seen.add(key)
                out.append({"token": token, "source": source,
                            "evidence": _snippet(text, match.start(),
                                                 max(1, match.end() - match.start()))})
    return out


def _model_candidates(texts: dict[str, str], product: dict) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    def push(token: str, source: str, evidence: str) -> None:
        token = _text(token).strip(".,;:")
        if not token or token.casefold() in seen:
            return
        if token.casefold() in _MODEL_STOPWORDS or token.isdigit():
            return
        if _MEASURE_TOKEN_RE.match(token):
            return  # 500ml / 20w / 30cm 属于规格，不是型号
        if len(token) < 2 or len(token) > 24:
            return
        if not re.search(r"[A-Za-z]", token) or not re.search(r"\d", token):
            return
        seen.add(token.casefold())
        out.append({"token": token, "source": source, "evidence": evidence})

    for source in ("title", "description", "features", "sku"):
        text = texts.get(source) or ""
        if not text:
            continue
        for match in _MODEL_LABEL_RE.finditer(text):
            push(match.group(1), source,
                 _snippet(text, match.start(), max(1, match.end() - match.start())))
    sku = _text(product.get("sellerSku") or product.get("sku"))
    if sku:
        push(sku, "sku", _snippet(sku, 0, len(sku)))
    for source in ("title", "sku"):
        text = texts.get(source) or ""
        for match in _MODEL_TOKEN_RE.finditer(text):
            push(match.group(0), source, _snippet(text, match.start(), len(match.group(0))))
    return out[:5]


def _size_candidates(texts: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    size_words = {"xxs", "xs", "s", "m", "l", "xl", "xxl", "2xl", "3xl", "4xl", "5xl",
                  "one size", "free size", "queen", "king", "single", "double"}
    measure_re = re.compile(r"^\d+(?:\.\d+)?\s*(?:cm|inch|in|mm)$", re.I)
    for source in ("title", "description", "sku", "attributes"):
        text = texts.get(source) or ""
        if not text:
            continue
        for match in _SIZE_CONTEXT_RE.finditer(text):
            raw = match.group(1) or ""
            words = [w for w in re.split(r"[\s/,;|]+", raw) if w]
            tokens: list[str] = []
            for index, word in enumerate(words):
                if word.casefold() in size_words or measure_re.match(word):
                    tokens.append(word)
                if index + 1 < len(words):
                    bigram = f"{word} {words[index + 1]}"
                    if bigram.casefold() in size_words:
                        tokens.append(bigram)
            for token in tokens:
                token = _text(token).strip(" .")
                if not token or len(token) > 12:
                    continue
                key = token.casefold()
                if key in seen:
                    continue
                seen.add(key)
                out.append({"token": token, "source": source,
                            "evidence": _snippet(text, match.start(),
                                                 max(1, match.end() - match.start()))})
    return out[:5]


def _license_candidates(texts: dict[str, str]) -> list[dict]:
    """认证/许可证编号（如泰国 TIS license no.）：从「license no: xxx」里取值。"""
    out: list[dict] = []
    seen: set[str] = set()
    for source in ("title", "description", "features", "attributes", "sku"):
        text = texts.get(source) or ""
        if not text:
            continue
        for match in _LICENSE_RE.finditer(text):
            token = _text(match.group(1)).strip(" .,;:")
            key = token.casefold()
            if not token or key in seen:
                continue
            seen.add(key)
            out.append({"token": token, "source": source,
                        "evidence": _snippet(text, match.start(),
                                             max(1, match.end() - match.start()))})
    return out[:3]


def _candidate_values(attr: dict, texts: dict[str, str], limit: int = 3) -> list[dict]:
    """无法确定时给候选：先按「与文案相关」排序，再按原顺序。"""
    values = [v for v in (attr.get("values") or []) if isinstance(v, dict)]
    if not values:
        return []
    blob = " ".join(texts.values()).casefold()
    related: list[dict] = []
    others: list[dict] = []
    for value in values:
        name = _text(value.get("name"))
        tname = _text(value.get("transName"))
        hit = any(_word_index(blob, n) >= 0 for n in (name, tname) if n)
        (related if hit else others).append(value)
    out: list[dict] = []
    for value in (related + others)[:max(1, int(limit or 3))]:
        out.append({"valueId": _text(value.get("id")),
                    "name": _text(value.get("name")),
                    "transName": _text(value.get("transName"))})
    return out


# ---------------- 建议（本地规则 + 可选 AI 兜底） ----------------
def _suggestion(attr: dict, values: list[dict], *, source: str, confidence: str,
                reason: str, is_free_text: bool = False) -> dict:
    return {
        "attrId": attr.get("attrId"),
        "attrName": attr.get("attrName"),
        "transName": attr.get("transName"),
        "label": attr.get("label") or attr.get("attrName"),
        "isMandatory": bool(attr.get("isMandatory")),
        "isMultipleSelected": bool(attr.get("isMultipleSelected")),
        "isCustomized": bool(attr.get("isCustomized")),
        "values": values,
        "isFreeText": bool(is_free_text),
        "source": source,
        "confidence": confidence,
        "reason": reason,
    }


def _rule_suggestion(attr: dict, candidates: list[dict]) -> dict | None:
    """规则候选 → 建议；枚举按 id/name 匹配，自由文本属性直接采用首个候选。"""
    if not candidates:
        return None
    if _is_boolean_attr(attr):
        return None
    values = [v for v in (attr.get("values") or []) if isinstance(v, dict)]
    matched: list[dict] = []
    how = ""
    used: dict = {}
    for candidate in candidates:
        value, matched_how = _match_value(attr, candidate["token"])
        if not value:
            continue
        key = _text(value.get("id")) or _casefold(value.get("name"))
        if key in {(_text(v.get("id")) or _casefold(v.get("name"))) for v in matched}:
            continue
        matched.append(value)
        how = matched_how or how
        used = candidate
        if not attr.get("isMultipleSelected"):
            break
        if len(matched) >= 5:
            break
    if matched:
        parts = [f"{value.get('name')}" for value in matched]
        reason = (f"{_ATTR_SOURCE_LABELS.get(used['source'], used['source'])}"
                  f"「{used['evidence']}」匹配类目枚举「{'、'.join(parts)}」")
        confidence = "high" if how in ("id", "name") else "medium"
        return _suggestion(attr, [{"valueId": _text(v.get("id")),
                                   "valueName": _text(v.get("name"))} for v in matched],
                           source=used["source"], confidence=confidence, reason=reason)
    if not values:  # 自由填写属性：直接把推导出的文本作为值
        first = candidates[0]
        token = _text(first["token"])
        if token:
            reason = (f"{_ATTR_SOURCE_LABELS.get(first['source'], first['source'])}"
                      f"「{first['evidence']}」推导（该属性为自由填写，无枚举值）")
            return _suggestion(attr, [{"valueId": "", "valueName": token}],
                               source=first["source"], confidence="medium",
                               reason=reason, is_free_text=True)
    return None


def _ai_fill(product: dict, attrs: list[dict], site: str, cfg,
             ) -> tuple[dict[str, str], str]:
    """AI 兜底（**单次批量**调用）：返回 ``({attrId: value}, note)``，失败不抛。"""
    if not attrs or cfg is None:
        return {}, ""
    try:
        from ..api import ApiClient
        from . import model_of

        allowed = [a.get("attrId") for a in attrs]
        prompt = build_attributes_prompt(product, attrs, site, limit=len(attrs))
        if not prompt:
            return {}, ""
        client = ApiClient(_cfg_value(cfg, "api_base", ""), _cfg_value(cfg, "api_key", ""),
                           _cfg_value(cfg, "timeout", 180))
        text = client.chat(model_of(cfg, "text"),
                           [{"role": "user", "content": prompt}], max_tokens=900)
        data = parse_attributes_reply(text, allowed_ids=allowed)
        if not data:
            return {}, "AI 未返回可用属性值（已跳过，请人工补全）"
        return data, ""
    except Exception as exc:  # noqa: BLE001
        return {}, f"AI 属性补全失败（不阻断）：{exc}"[:200]


def _attribute_suggestion(attr: dict, existing: dict[str, list[dict]],
                          texts: dict[str, str], product: dict,
                          ) -> tuple[dict | None, dict | None]:
    """单个属性 → ``(suggestion, None)`` 或 ``(None, unresolved)``（互斥）。

    已有值优先（保留不覆盖）→ 本地规则 → 无法确定则给候选 + 中文原因。
    本函数由 :func:`suggest_attributes` 逐属性 try/except 调用。
    """
    attr_id = _text(attr.get("attrId"))
    have = existing.get(attr_id)
    if have:
        return _suggestion(attr, [dict(v) for v in have], source="existing",
                           confidence="high", reason="商品详情已有该属性值，保留不覆盖"), None
    key = _rule_key(attr)
    candidates = _derive_candidates(key, texts, product) if key else []
    suggestion = _rule_suggestion(attr, candidates) if candidates else None
    if suggestion:
        return suggestion, None
    entry = {
        "attrId": attr_id,
        "attrName": attr.get("attrName"),
        "transName": attr.get("transName"),
        "label": attr.get("label") or attr.get("attrName"),
        "isMandatory": bool(attr.get("isMandatory")),
        "isConditional": bool(attr.get("isConditional")),
        "isBoolean": _is_boolean_attr(attr),
        "isFreeText": not bool(attr.get("values")),
        "candidates": _candidate_values(attr, texts),
        "reason": "",
    }
    if entry["isBoolean"]:
        entry["reason"] = "布尔型属性（是/否），需人工确认，未自动推断"
    elif entry["isFreeText"]:
        entry["reason"] = ("类目未提供枚举值（自由填写），"
                           + ("文案中未找到可推导内容" if key else "无匹配规则"))
    elif key:
        entry["reason"] = f"文案未出现可匹配「{attr.get('label')}」的枚举值，请从候选人工确认"
    else:
        entry["reason"] = "本地无该属性的推导规则，请从候选人工确认"
    return None, entry


def suggest_attributes(product: dict, attrs, site: str = "", use_ai: bool = False,
                       cfg=None) -> dict:
    """为类目属性生成取值建议（本地规则优先，可选 AI 兜底）。

    规则：已有 ``productAttributes`` 的值一律保留（source=existing，不覆盖）；
    标题/描述/卖点/SKU/品牌里能匹配到类目枚举的按 id/name 命中；
    枚举无法命中的给「候选 + 中文原因」；自由填写属性用推导文本；
    ``use_ai=True`` 时对仍缺失的**必填**属性做一次批量 AI 兜底（逐项 try/except，
    AI 值必须落在类目枚举内，否则丢弃）。

    返回::

        {"site","categoryId","suggestions":[...],"unresolved":[...],
         "missing_mandatory":[label...],"coverage":{...},
         "ai_used":bool,"notes":[中文...]}
    """
    p = product if isinstance(product, dict) else {}
    site_code = normalize_site(site or _text(p.get("site")))
    attr_list = _normalize_attr_list(attrs)
    texts = _product_texts(p)
    existing = _existing_attribute_map(p)
    suggestions: list[dict] = []
    unresolved: list[dict] = []
    notes: list[str] = []

    pending_mandatory: list[dict] = []
    for attr in attr_list:
        attr_id = _text(attr.get("attrId"))
        if not attr_id or attr_id == ATTR_BRAND_ID:
            continue  # 品牌由 brandId/brandInfo 维护，不在属性补全范围
        # 逐属性 try/except：单个属性数据异常不得中断整批补全
        try:
            suggestion, entry = _attribute_suggestion(attr, existing, texts, p)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"属性「{attr.get('label') or attr_id}」解析失败（不阻断）："
                         f"{str(exc)[:120]}")
            entry = {
                "attrId": attr_id,
                "attrName": attr.get("attrName"),
                "transName": attr.get("transName"),
                "label": attr.get("label") or attr.get("attrName"),
                "isMandatory": bool(attr.get("isMandatory")),
                "isConditional": bool(attr.get("isConditional")),
                "isBoolean": False,
                "isFreeText": not bool(attr.get("values")),
                "candidates": [],
                "reason": f"属性数据异常，已跳过本地推断（{str(exc)[:80]}）",
            }
            suggestion = None
        if suggestion:
            suggestions.append(suggestion)
        if entry:
            unresolved.append(entry)
            if entry.get("isMandatory") and not entry.get("isConditional"):
                pending_mandatory.append(attr)

    ai_used = False
    if use_ai and pending_mandatory:
        by_id = {a.get("attrId"): a for a in attr_list}
        data, note = _ai_fill(p, pending_mandatory, site_code, cfg)
        if note:
            notes.append(note)
        filled_ids: set[str] = set()
        for attr_id, value in (data or {}).items():
            attr = by_id.get(attr_id)
            if attr is None:
                continue
            try:  # 逐字段 try/except：单个 AI 值异常不影响其它属性
                matched, how = _match_value(attr, value)
                if attr.get("values") and not matched:
                    notes.append(f"AI 对「{attr.get('label')}」给出的值「{value}」"
                                 "不在类目枚举中，已丢弃")
                    continue
                values = ([{"valueId": _text(matched.get("id")),
                            "valueName": _text(matched.get("name"))}]
                          if matched else [{"valueId": "", "valueName": _text(value)}])
                suggestions.append(_suggestion(
                    attr, values, source="ai", confidence="low",
                    reason=f"AI 兜底推断（值：{value}），保存前请核对",
                    is_free_text=not bool(attr.get("values"))))
                filled_ids.add(attr_id)
                ai_used = True
            except Exception as exc:  # noqa: BLE001
                notes.append(f"AI 值应用失败（属性 {attr_id}，不阻断）：{str(exc)[:120]}")
        if filled_ids:
            unresolved = [u for u in unresolved if u["attrId"] not in filled_ids]

    missing_mandatory = [
        (u.get("label") or u.get("attrId"))
        for u in unresolved if u.get("isMandatory") and not u.get("isConditional")
    ]
    mandatory = [a for a in attr_list if a.get("isMandatory") and a.get("attrId") != ATTR_BRAND_ID]
    mandatory_ids = {_text(a.get("attrId")) for a in mandatory}
    mandatory_filled = len([s for s in suggestions if _text(s.get("attrId")) in mandatory_ids
                            and s.get("values")])
    mandatory_total = len(mandatory_ids)
    eligible = [a for a in attr_list if _text(a.get("attrId")) != ATTR_BRAND_ID]
    coverage = {
        "attr_total": len(eligible),
        "suggested": len(suggestions),
        "suggested_ratio": round(len(suggestions) / len(eligible), 3) if eligible else 0.0,
        "mandatory_total": mandatory_total,
        "mandatory_filled": mandatory_filled,
        "mandatory_ratio": (round(mandatory_filled / mandatory_total, 3)
                            if mandatory_total else 1.0),
        "unresolved": len(unresolved),
    }
    return {
        "site": site_code,
        "categoryId": _text(attr_list[0].get("categoryId")) if attr_list else "",
        "suggestions": suggestions,
        "unresolved": unresolved,
        "missing_mandatory": missing_mandatory,
        "coverage": coverage,
        "ai_used": ai_used,
        "notes": notes,
    }


# ---------------- 写回 productAttributes（只补空值） ----------------
def attribute_items(suggestions) -> list[dict]:
    """建议 → ``productAttributes`` 元素（真实前端形状 attributeId/attributeName/values）。"""
    rows = suggestions.get("suggestions") if isinstance(suggestions, dict) else suggestions
    items: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        attr_id = _text(row.get("attrId"))
        if not attr_id or attr_id == ATTR_BRAND_ID:
            continue
        values = [v for v in (row.get("values") or []) if isinstance(v, dict)]
        out_values: list[dict] = []
        for value in values:
            vid = _text(value.get("valueId"))
            vname = _text(value.get("valueName"))
            if not vname and not vid:
                continue
            out_values.append({"valueId": vid, "valueName": vname or vid})
        if not out_values:
            continue
        items.append({
            "attributeId": attr_id,
            "attributeName": _text(row.get("attrName") or row.get("label")) or attr_id,
            "values": out_values,
        })
    return items


def merge_product_attributes(existing, suggestions, *, overwrite: bool = False) -> list[dict]:
    """合并建议到已有属性元素：默认**只补空值，不覆盖用户已填的值**。

    - 已有元素（含品牌 ``-99999``、未知键）原样保留；
    - 某属性已有非空 ``values`` → 跳过（``overwrite=True`` 才替换）；
    - 新属性追加在末尾；同 ``attributeId`` 只保留一份。
    """
    items = _attribute_items(existing)
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for item in items:
        attr_id = _text(item.get("attributeId") or item.get("attrId") or item.get("id"))
        if not attr_id:
            order.append("")
            by_id[f"__anon_{len(order)}"] = item
            continue
        if attr_id not in by_id:
            order.append(attr_id)
        by_id[attr_id] = item

    for new_item in attribute_items(suggestions):
        attr_id = new_item["attributeId"]
        current = by_id.get(attr_id)
        if current is None:
            order.append(attr_id)
            by_id[attr_id] = new_item
            continue
        if _item_values(current) and not overwrite:
            continue  # 用户已填，保留
        merged = {k: v for k, v in current.items() if k not in ("values", "valueName", "value")}
        merged.update(new_item)
        by_id[attr_id] = merged

    out: list[dict] = []
    for key in order:
        item = by_id.get(key)
        if item is not None:
            out.append(item)
    return out


def apply_attribute_suggestions(payload: dict, suggestions, *,
                                overwrite: bool = False) -> dict:
    """把建议写回保存包的 ``productAttributes``（JSON 字符串），返回新 dict。

    只补空值（不覆盖用户值）；无建议时原样返回，保证 productAttributes 透传不被破坏。
    """
    out = copy.deepcopy(dict(payload or {}))
    rows = suggestions.get("suggestions") if isinstance(suggestions, dict) else suggestions
    if not rows:
        return out
    merged = merge_product_attributes(out.get("productAttributes"), suggestions,
                                      overwrite=overwrite)
    if merged:
        out["productAttributes"] = json.dumps(merged, ensure_ascii=False)
    return out


def missing_mandatory_attributes(payload: dict, attrs) -> list[dict]:
    """保存包里仍缺值的**必填**属性（条件必填单独标记，交给调用方只做提醒）。"""
    filled = _existing_attribute_map({"productAttributes": (payload or {}).get("productAttributes")})
    out: list[dict] = []
    for attr in _normalize_attr_list(attrs):
        attr_id = _text(attr.get("attrId"))
        if not attr_id or attr_id == ATTR_BRAND_ID or not attr.get("isMandatory"):
            continue
        if filled.get(attr_id):
            continue
        out.append({
            "attrId": attr_id,
            "label": attr.get("label") or attr.get("attrName"),
            "isConditional": bool(attr.get("isConditional")),
            "isFreeText": not bool(attr.get("values")),
        })
    return out


__all__ = [
    "SAVE_KEYS", "DEFAULT_LIMITS", "OVERRIDE_KEYS", "SITE_META",
    "OP_SAVE", "OP_PUBLISH", "OP_MOVE_PENDING", "OP_SCHEDULE",
    "split_pipe", "join_pipe", "extract_detail_product", "build_tiktok_payload",
    "validate_tiktok_payload", "build_warnings", "apply_site",
    "price_stock_overrides", "payload_price", "payload_stock",
    "ATTR_BRAND_ID", "ATTR_SHAPE_CUSTOM", "ATTR_SHAPE_ENUM", "ATTR_SHAPE_ENUM_CUSTOM",
    "normalize_category_attribute", "load_category_attributes",
    "suggest_attributes", "attribute_items", "merge_product_attributes",
    "apply_attribute_suggestions", "missing_mandatory_attributes",
]

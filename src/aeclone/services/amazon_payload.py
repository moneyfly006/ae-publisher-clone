"""Amazon 保存包（amazonSave.txt）构造与保存前校验。

事实来源：2026-11-22 真实抓包（``.tmp-probe/amazonSave_payload.json`` /
``save_capture2.json`` 中 ZIP 内的 ``amazonSave.txt``）与 ``editSp`` 详情。

关键约定
--------
- 保存包只有 ``SAVE_KEYS`` 这 39 个键，顺序与真实抓包一致；
- 五点描述 ``bulletPoint`` 用 ``±`` 连接；``description`` 是 HTML 字符串；
- ``productAttr`` 是 JSON **字符串**（读取后原样回写，切站点时深度重写
  ``marketplace_id`` / ``marketplaceId``）；
- 数值：``standardPrice`` / ``saleSalePrice`` ≤0 时发 ``null``；
  ``quantity`` / ``isVariation`` / ``gpsrSafetyAttestation`` / ``skuTitle`` 转 int
  （真实写回读验证过 int 可被服务端接受；editSp 里它们本身也是数字）；
- ``overrides`` 只认 docs/AMAZON_DESIGN.md §3.2.0 的 snake_case 键（空串=不覆盖）。
"""
from __future__ import annotations

import html as _html
import json
import re

from .amazon_prompts import (
    BULLET_MAX_CHARS, BULLET_MAX_ITEMS, BULLET_SUGGEST_CHARS,
    DESCRIPTION_MAX_BYTES, SEARCH_TERMS_MAX_BYTES, SEARCH_TERMS_MAX_CHARS,
    SITE_META, TITLE_MAX, has_chinese, is_chinese_site, remove_emoji,
    site_meta as prompt_site_meta, strip_html,
)

# 真实保存包 39 键，顺序与抓包一致（不得增删/重排）
SAVE_KEYS: list[str] = [
    "id", "shopId", "marketPlaceId", "currency", "title", "titleDifferentiation",
    "categoryId", "categoryBrowsePath", "audience", "isVariation", "brandSettledType",
    "fullCid", "sourceUrl", "standardProductType", "standardProductId", "sku", "brand",
    "manufacturer", "productAttr", "standardPrice", "saleSalePrice", "saleStartDateStr",
    "saleEndDateStr", "quantity", "conditionType", "conditionValue", "mainImage",
    "extraImagesStr", "draftImgUrl", "variationTheme", "skuTitle", "description",
    "bulletPoint", "gpsrManufacturerReference", "dsaResponsiblePartyAddress",
    "gpsrSafetyAttestation", "switchFulfillmentTo", "merchantShippingGroup", "searchTerms",
]

# overrides 唯一键名（docs §3.2.0 + task-8 属性补齐）；其它键一律忽略
OVERRIDE_KEYS = (
    "title", "bullets", "description", "search_terms", "brand", "manufacturer", "sku",
    "standard_price", "sale_price", "quantity", "category_id", "category_path",
    "marketplace_id", "currency", "fulfillment", "handling_time", "shipping_template",
    "attributes", "required_attributes",
)

# 发布（op=2）实测必填的通用属性（不同类目还会有额外项，见 errMsg/schema）
REQUIRED_COMMON = ("model_number", "part_number", "model_name",
                   "manufacturer_contact_information")

# 缺失必填属性自动补齐开关（配置键；config.py 的 DEFAULTS 由 backend 维护，
# 这里未写入时按默认 True：缺失就补、可在设置里关掉）
AUTOFILL_CFG_KEY = "amazon_autofill_attrs"

OP_DEFAULT = "1"

_MARKET_KEYS = ("marketplace_id", "marketplaceId", "marketplaceID", "marketPlaceId", "marketplace")
_CN_RE = re.compile(r"[\u4e00-\u9fff]")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)
_BAD_DESC_TAG_RE = re.compile(r"<\s*(script|style|iframe)\b", re.I)
_SCRIPT_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>[\s\S]*?</\1>", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_PHONE_RE = re.compile(r"(?<![\d.])[+]?\d[\d\s\-()]{7,}\d(?![\d.])")
# 促销 / 绝对化 / 医疗词（可按 site_meta["promo_whitelist"] 放行）
_PROMO_RE = re.compile(
    r"(?:\bbest\b|\bcheapest\b|\blowest\s+price\b|\bfree\s+shipping\b|\bhot\s+sale\b|"
    r"\bdiscount\b|\bsale\b|\bpromotion\b|\bcashback\b|\bguarantee(?:d)?\b|"
    r"100\s*%|\bno\.?\s*1\b|\bnumber\s+one\b|\bmiracle\b|\bcure\b|\bfda\s+approved\b)",
    re.I,
)
PROMO_WHITELIST: tuple[str, ...] = ()


# ---------------- 基础工具 ----------------
def split_bullets(bullet_point: str) -> list[str]:
    """五点描述字符串 → 列表（``±`` 分隔，strip，去空）。"""
    if not bullet_point:
        return []
    return [b.strip() for b in str(bullet_point).split("±") if b and b.strip()]


def join_bullets(bullets: list[str]) -> str:
    """列表 → 五点描述字符串（``±`` 连接，去空）。"""
    if not bullets:
        return ""
    if isinstance(bullets, str):
        bullets = [bullets]
    return "±".join(str(b).strip() for b in bullets if b and str(b).strip())


def _to_float(value):
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        f = float(str(value).strip())
    except Exception:
        return None
    if f != f:  # NaN
        return None
    return f


def _price(value):
    """价格：None/空/≤0 → None；>0 → 两位小数。"""
    f = _to_float(value)
    if f is None or f <= 0:
        return None
    return round(f, 2)


def _to_int(value, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return default
        f = float(str(value).strip())
        if f != f:
            return default
        return int(f)
    except Exception:
        return default


def _text(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _override(ov: dict, key: str, default=None):
    """取 overrides 值；空串/空白视作「不覆盖」。"""
    if not isinstance(ov, dict) or key not in ov:
        return default
    value = ov.get(key)
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return value


def _has_cfg(cfg, key: str) -> bool:
    value = _cfg_value(cfg, key, None)
    if value is None:
        return False
    return not (isinstance(value, str) and not value.strip())


def _cfg_value(cfg, key: str, default=None):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _as_html_description(value) -> str:
    """description 原样保留 HTML；纯文本按段落包 <p>（UI 约定「纯文本自动包 <p>」）。"""
    s = "" if value is None else str(value)
    if not s.strip():
        return ""
    if _HTML_TAG_RE.search(s):
        return s
    paras = [p.strip() for p in re.split(r"\r?\n+", s) if p.strip()]
    return "".join(f"<p>{_html.escape(p)}</p>" for p in paras)


def _site_lang(meta: dict | None) -> str:
    if not meta:
        return "en"
    return str(meta.get("lang") or meta.get("language") or "en").lower()


def _rewrite_marketplace(node, marketplace_id: str):
    """深度遍历 JSON，重写所有 marketplace_id / marketplaceId 的值为新站点。"""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key in _MARKET_KEYS and isinstance(value, str):
                out[key] = marketplace_id
            else:
                out[key] = _rewrite_marketplace(value, marketplace_id)
        return out
    if isinstance(node, list):
        return [_rewrite_marketplace(v, marketplace_id) for v in node]
    return node


def _product_attr_payload(raw, marketplace_id: str = "") -> str:
    """productAttr：字符串/字典 → JSON 字符串；切站点时重写 marketplace 字段。"""
    if raw is None:
        return ""
    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ""
        try:
            data = json.loads(text)
        except Exception:
            return raw if not marketplace_id else raw
    elif not isinstance(raw, (dict, list)):
        return str(raw)
    if marketplace_id:
        data = _rewrite_marketplace(data, marketplace_id)
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return raw if isinstance(raw, str) else ""


def parse_product_attr(raw) -> dict:
    """productAttr（JSON 字符串/字典）→ dict；非法输入返回 {}。"""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def language_tag_for(marketplace_id: str) -> str:
    """marketPlaceId → Amazon language_tag（如 A2EUQ1WTGCTBG2 → en_CA）。"""
    mp = str(marketplace_id or "").strip()
    meta = SITE_META.get(mp) or {}
    code = str(meta.get("code") or "").strip()
    lang = str(meta.get("lang") or "en").strip()
    if code:
        return f"{lang}_{code}"
    return "en_US"


def _attr_present_value(val) -> bool:
    if val is None:
        return False
    if isinstance(val, list):
        if not val:
            return False
        for first in val:
            if isinstance(first, dict):
                if "value" in first:
                    inner = first.get("value")
                    if inner is None:
                        return False
                    if isinstance(inner, str) and not inner.strip():
                        return False
                    return True
                if first:
                    # 形如 item_dimensions 的原始 item（length/width/height，无 value 键）
                    return True
            elif str(first).strip():
                return True
        return False
    if isinstance(val, str):
        return bool(val.strip())
    return True


def attribute_present(attr_data, key: str) -> bool:
    """属性 dict 里该 key 是否有非空值（布尔 false 也算已填写）。"""
    if not isinstance(attr_data, dict) or key not in attr_data:
        return False
    return _attr_present_value(attr_data.get(key))


def missing_common_attributes(raw) -> list[str]:
    """检查 productAttr 里缺失的 REQUIRED_COMMON（发布必填）。"""
    data = parse_product_attr(raw)
    return [key for key in REQUIRED_COMMON if not attribute_present(data, key)]


# ---------------- 缺失必填属性自动补齐（保存包阶段） ----------------
def autofill_enabled(cfg) -> bool:
    """``cfg.amazon_autofill_attrs``（默认 True；False/0/"0"/"false"/"off" 关闭）。"""
    value = _cfg_value(cfg, AUTOFILL_CFG_KEY, True)
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "off", "no", "")
    return bool(value)


def required_attribute_names(cfg=None, overrides: dict | None = None) -> list[str]:
    """本次要补齐的属性名：overrides["required_attributes"] > cfg 记录 > REQUIRED_COMMON。"""
    raw = (overrides or {}).get("required_attributes") \
        if isinstance(overrides, dict) else None
    if not raw:
        raw = _cfg_value(cfg, "amazon_last_category_required", "") or ""
    if isinstance(raw, str):
        parts = re.split(r"[,;，；\s]+", raw)
    else:
        try:
            parts = list(raw)
        except TypeError:
            parts = []
    out: list[str] = []
    for item in parts:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out or list(REQUIRED_COMMON)


def attribute_value_text(value, limit: int = 60) -> str:
    """属性值 → 单行摘要（用于 warnings/notes 展示补了什么）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return ""
        first = value[0]
        if isinstance(first, dict):
            inner = first.get("value", first)
            return attribute_value_text(inner, limit)
        return attribute_value_text(first, limit)
    if isinstance(value, dict):
        inner = value.get("value")
        if isinstance(inner, dict) and "amount" in inner:
            currency = str(inner.get("currency") or "").strip()
            amount = inner.get("amount")
            return f"{amount} {currency}".strip()
        if inner is not None:
            return attribute_value_text(inner, limit)
        return str(value)[:limit]
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] if len(text) > limit else text


def format_attribute_summary(filled: dict, limit: int = 6) -> str:
    """{"model_number": "HW20-28", ...} → ``model_number=HW20-28、part_number=SKU``。"""
    parts: list[str] = []
    for key, value in (filled or {}).items():
        text = attribute_value_text(value)
        parts.append(f"{key}={text}" if text else str(key))
        if len(parts) >= limit:
            break
    return "、".join(parts)


def _attr_json_ok(raw) -> bool:
    """productAttr 是否是「可安全解析/重写」的 JSON（空值视为可写）。"""
    if raw is None:
        return True
    if isinstance(raw, (dict, list)):
        return True
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return True
        try:
            json.loads(text)
            return True
        except Exception:
            return False
    return False


def _payload_autofill(product: dict, cfg, overrides: dict | None,
                      marketplace_id: str) -> tuple[dict, list[str]]:
    """确定性补齐缺失的发布必填属性（只补缺失，不覆盖已有）。

    具体映射在 ``amazon_pipeline._autofill_attributes_with_notes``（SKU/标题/品牌等
    确定性推导，不发请求、不写平台）；这里延迟 import 避免 amazon_payload ↔
    amazon_pipeline 的模块级循环依赖。补齐失败/关闭开关一律返回空，不阻断保存。
    """
    if not autofill_enabled(cfg):
        return {}, []
    try:
        from .amazon_pipeline import _autofill_attributes_with_notes
    except Exception:  # pragma: no cover - 理论不可达（延迟 import 防御）
        return {}, []
    try:
        return _autofill_attributes_with_notes(
            product or {}, cfg, marketplace_id, overrides,
            required=required_attribute_names(cfg, overrides))
    except Exception:  # noqa: BLE001 - 属性补齐绝不阻断保存包构造
        return {}, []


def format_attribute_value(value, marketplace_id: str = "",
                           language_tag: str = "") -> list[dict]:
    """把 Python 值转成 productAttr 实测格式。

    - 布尔：``[{"value": false, "marketplace_id": mp}]``
    - 字符串：``[{"value": "x", "language_tag": "en_CA", "marketplace_id": mp}]``
    - dict（如 list_price {currency, amount}）：``[{"value": {...}, "marketplace_id": mp}]``
    """
    mp = str(marketplace_id or "").strip()
    if value is None:
        return []
    if isinstance(value, list):
        items = [dict(x) for x in value if isinstance(x, dict)]
        return items
    if isinstance(value, bool):
        item: dict = {"value": value}
        if mp:
            item["marketplace_id"] = mp
        return [item]
    if isinstance(value, dict):
        if not value:
            return []
        item = {"value": dict(value)}
        if mp:
            item["marketplace_id"] = mp
        return [item]
    text = str(value).strip()
    if not text:
        return []
    item = {"value": text}
    tag = str(language_tag or "").strip() or language_tag_for(mp)
    if tag:
        item["language_tag"] = tag
    if mp:
        item["marketplace_id"] = mp
    return [item]


def merge_attributes(attr_data, attributes, marketplace_id: str = "",
                     language_tag: str = "") -> dict:
    """把 ``overrides["attributes"]`` 合并进 productAttr dict（override 优先）。"""
    out = dict(attr_data or {})
    if not isinstance(attributes, dict):
        return out
    for key, value in attributes.items():
        name = str(key or "").strip()
        if not name:
            continue
        formatted = format_attribute_value(value, marketplace_id, language_tag)
        if formatted:
            out[name] = formatted
    return out


def _images(product: dict, main=None) -> tuple[str, str, str]:
    """返回 (mainImage, extraImagesStr, draftImgUrl)。"""
    p = product or {}
    main_image = _text(main) if main is not None else _text(p.get("mainImage"))
    if not main_image:
        img_url = _text(p.get("imgUrl"))
        if img_url:
            main_image = img_url.split("|")[0].strip()
    extra_list: list[str] = []
    raw_list = p.get("extraImages")
    if isinstance(raw_list, list):
        extra_list = [str(x).strip() for x in raw_list if str(x).strip()]
    extra_str = _text(p.get("extraImagesStr"))
    if not extra_str and extra_list:
        extra_str = ";".join(extra_list)
    if not extra_list and extra_str:
        extra_list = [x.strip() for x in extra_str.split(";") if x.strip()]
    draft = _text(p.get("draftImgUrl"))
    if not draft and extra_list:
        draft = "|".join(extra_list)
    return main_image, extra_str, draft


# ---------------- 保存包构造 ----------------
def build_amazon_payload(product: dict, cfg=None, overrides: dict | None = None,
                         *, currency: str = "",
                         notes: list | None = None) -> dict:
    """由 editSp 的 product（或列表行）+ overrides 构造 39 键保存包。

    - 只取 ``SAVE_KEYS``（保序），其它字段一律丢弃；
    - overrides 只认 snake_case（docs §3.2.0），空串=不覆盖；
    - 站点切换：同步 currency，并深度重写 productAttr 里的 marketplace 字段；
    - **缺失的发布必填属性自动补齐进 productAttr**（只补缺失、不覆盖已有；
      ``cfg.amazon_autofill_attrs=False`` 可关闭，默认开）；
    - ``notes``（可选）收集「补了什么/哪些补不了」的说明，供 prepare_save/UI 展示；
    - 价格 ≤0 → None；quantity/isVariation/skuTitle → int。
    """
    p = dict(product or {})
    ov = {k: v for k, v in (overrides or {}).items() if k in OVERRIDE_KEYS}

    out: dict = {key: None for key in SAVE_KEYS}

    # 基础标识
    out["id"] = _text(p.get("id") or p.get("productId") or p.get("idStr"))
    out["shopId"] = _text(p.get("shopId") or _cfg_value(cfg, "amazon_shop_id", "") or "")
    out["titleDifferentiation"] = p.get("titleDifferentiation")
    out["audience"] = _text(p.get("audience"), "all") or "all"
    out["fullCid"] = _text(p.get("fullCid"))
    out["sourceUrl"] = _text(p.get("sourceUrl") or p.get("source_url"))
    out["standardProductType"] = p.get("standardProductType")
    out["standardProductId"] = p.get("standardProductId")
    out["conditionType"] = p.get("conditionType")
    out["conditionValue"] = _text(p.get("conditionValue"))
    out["variationTheme"] = _text(p.get("variationTheme"))
    out["gpsrManufacturerReference"] = _text(p.get("gpsrManufacturerReference"))
    out["dsaResponsiblePartyAddress"] = _text(p.get("dsaResponsiblePartyAddress"))
    out["brandSettledType"] = _text(p.get("brandSettledType"), "external_product_id") \
        or "external_product_id"
    out["isVariation"] = _to_int(p.get("isVariation"), 0)
    out["skuTitle"] = _to_int(p.get("skuTitle"), 0)
    out["gpsrSafetyAttestation"] = _to_int(p.get("gpsrSafetyAttestation"), 0)

    # 站点 / 币种
    new_mp = _text(_override(ov, "marketplace_id", p.get("marketPlaceId")))
    old_mp = _text(p.get("marketPlaceId"))
    out["marketPlaceId"] = new_mp
    meta = prompt_site_meta(new_mp) if new_mp else {}
    cur = (_text(currency) or _text(_override(ov, "currency"))
           or _text(p.get("currency")) or _text(p.get("standardPriceCurrency")))
    if new_mp and new_mp != old_mp and not _text(_override(ov, "currency")):
        cur = _text(meta.get("currency")) or cur
    out["currency"] = cur

    # 文案
    title = _override(ov, "title", p.get("title"))
    out["title"] = _text(title)
    out["bulletPoint"] = join_bullets(_override(ov, "bullets", p.get("bulletPoint")))
    desc = _override(ov, "description", p.get("description"))
    out["description"] = _as_html_description(desc)
    terms = _override(ov, "search_terms", p.get("searchTerms"))
    out["searchTerms"] = _text(terms)

    # 类目
    cid = _override(ov, "category_id", p.get("categoryId"))
    out["categoryId"] = _text(cid)
    path = _override(ov, "category_path", p.get("categoryBrowsePath"))
    out["categoryBrowsePath"] = _text(path)

    # 品牌 / 制造商 / SKU（商品值 > cfg 兜底；overrides 优先）
    brand = _override(ov, "brand", p.get("brand"))
    if not _text(brand) and _has_cfg(cfg, "amazon_brand"):
        brand = _cfg_value(cfg, "amazon_brand")
    out["brand"] = _text(brand)
    manufacturer = _override(ov, "manufacturer", p.get("manufacturer"))
    if not _text(manufacturer) and _has_cfg(cfg, "amazon_manufacturer"):
        manufacturer = _cfg_value(cfg, "amazon_manufacturer")
    out["manufacturer"] = _text(manufacturer)
    sku = _override(ov, "sku", p.get("sku"))
    if not _text(sku) and _has_cfg(cfg, "amazon_sku"):
        sku = _cfg_value(cfg, "amazon_sku")
    out["sku"] = _text(sku)

    # 价格 / 库存
    # 价格：overrides > 商品原值 > cfg 兜底（cfg 只在商品没价时生效，避免静默改价）
    std_price = _override(ov, "standard_price", None)
    if std_price is None:
        std_price = p.get("standardPrice")
    if _to_float(std_price) in (None, 0.0) and _has_cfg(cfg, "amazon_price"):
        std_price = _cfg_value(cfg, "amazon_price")
    out["standardPrice"] = _price(std_price)
    sale_price = _override(ov, "sale_price", None)
    if sale_price is None:
        sale_price = p.get("saleSalePrice")
    if _to_float(sale_price) in (None, 0.0) and _has_cfg(cfg, "amazon_sale_price"):
        sale_price = _cfg_value(cfg, "amazon_sale_price")
    out["saleSalePrice"] = _price(sale_price)
    out["saleStartDateStr"] = p.get("saleStartDateStr")
    out["saleEndDateStr"] = p.get("saleEndDateStr")
    qty = _override(ov, "quantity", None)
    if qty is None:
        qty = p.get("quantity")
    if qty is None:
        qty = _cfg_value(cfg, "amazon_quantity", 0)
    out["quantity"] = _to_int(qty, 0)

    # 图片
    main_image, extra_str, draft = _images(p)
    out["mainImage"] = main_image
    out["extraImagesStr"] = extra_str
    out["draftImgUrl"] = draft

    # 物流
    fulfillment = _text(_override(ov, "fulfillment", p.get("switchFulfillmentTo")))
    if not fulfillment:
        fulfillment = _text(_cfg_value(cfg, "amazon_fulfillment", ""), "MFN") or "MFN"
    out["switchFulfillmentTo"] = fulfillment
    shipping = _text(_override(ov, "shipping_template", p.get("merchantShippingGroup")))
    if not shipping:
        shipping = _text(_cfg_value(cfg, "amazon_shipping_template", ""))
    out["merchantShippingGroup"] = shipping

    # 产品属性：切站点时深度重写 marketplace 字段；overrides["attributes"] 合并/覆盖；
    # 缺失的发布必填属性自动补齐（确定性映射，只补缺失，不覆盖已有）。
    raw_attr = p.get("productAttr")
    rewrite_mp = new_mp if new_mp and new_mp != old_mp else ""
    attrs_ov = _override(ov, "attributes", None)
    has_attrs_ov = isinstance(attrs_ov, dict) and bool(attrs_ov)
    attr_data = parse_product_attr(raw_attr)
    filled: dict = {}
    attr_notes: list[str] = []
    if _attr_json_ok(raw_attr):
        filled, attr_notes = _payload_autofill(p, cfg, ov, new_mp)
    if has_attrs_ov or rewrite_mp or filled:
        if rewrite_mp:
            attr_data = _rewrite_marketplace(attr_data, rewrite_mp)
        if has_attrs_ov:
            attr_data = merge_attributes(attr_data, attrs_ov, new_mp,
                                         language_tag_for(new_mp))
        if filled:
            attr_data = merge_attributes(attr_data, filled, new_mp,
                                         language_tag_for(new_mp))
        out["productAttr"] = json.dumps(attr_data, ensure_ascii=False)
    else:
        # 无需改写时保持原样回写（真实写 #1 的 productAttr 往返一致）
        out["productAttr"] = _product_attr_payload(raw_attr, "")

    if notes is not None:
        if filled:
            notes.append("自动补齐发布必填属性：" + format_attribute_summary(filled))
        notes.extend(attr_notes)

    return out


def apply_site(payload: dict, marketplace_id: str, currency: str,
               site_meta: dict) -> dict:
    """把保存包切到新站点：marketPlaceId/currency + productAttr 深度重写。"""
    out = dict(payload or {})
    mp = str(marketplace_id or "").strip()
    if not mp:
        return out
    meta = site_meta or prompt_site_meta(mp)
    out["marketPlaceId"] = mp
    cur = str(currency or "").strip() or str((meta or {}).get("currency") or "").strip()
    if cur:
        out["currency"] = cur
    attr = out.get("productAttr")
    if attr:
        out["productAttr"] = _product_attr_payload(attr, mp)
    return out


# ---------------- 校验 ----------------
def _field_label(key: str) -> str:
    return {
        "title": "标题", "bulletPoint": "五点描述", "description": "产品描述",
        "searchTerms": "Search Terms",
    }.get(key, key)


def _promo_hits(text: str, whitelist: tuple[str, ...] = ()) -> list[str]:
    """返回命中的促销/绝对化/联系方式词（去重、保持出现顺序）。"""
    s = str(text or "")
    hits: list[str] = []
    for m in _PROMO_RE.finditer(s):
        token = re.sub(r"\s+", " ", m.group(0).strip().lower())
        if token and token not in hits:
            hits.append(token)
    if _EMAIL_RE.search(s):
        hits.append("邮箱")
    if _URL_RE.search(s):
        hits.append("网址")
    if _PHONE_RE.search(s):
        hits.append("电话")
    allow = {str(w).strip().lower() for w in whitelist if str(w).strip()}
    return [h for h in hits if h.lower() not in allow]


def _scan_copy_fields(payload: dict) -> list[tuple[str, str]]:
    """(字段名, 纯文本) —— 用于中文/促销词/联系方式检查。"""
    fields: list[tuple[str, str]] = []
    fields.append(("title", str(payload.get("title") or "")))
    for i, b in enumerate(split_bullets(payload.get("bulletPoint") or ""), 1):
        fields.append((f"bullet{ i }", b))
    fields.append(("description", strip_html(payload.get("description") or "")))
    terms = str(payload.get("searchTerms") or "")
    if terms:
        fields.append(("searchTerms", terms))
    return fields


def validate_amazon_payload(payload: dict, *, product: dict | None = None,
                            site_meta: dict | None = None) -> list[str]:
    """返回「阻断项」列表；空列表=可保存。规则见 docs §6（schema 硬上限）。"""
    p = payload or {}
    issues: list[str] = []

    mp = str(p.get("marketPlaceId") or "").strip()
    meta = site_meta or {}
    lang = _site_lang(meta) if meta else "en"
    if meta and meta.get("marketplace_id") and str(meta.get("marketplace_id")) != mp:
        # 调用方给的站点元数据与保存包不一致：以保存包为准重新取
        meta = prompt_site_meta(mp)
        lang = _site_lang(meta)
    elif not meta:
        meta = prompt_site_meta(mp) if mp else {}
        lang = _site_lang(meta)

    # 1) 标题
    title = str(p.get("title") or "").strip()
    if not title:
        issues.append("标题为空")
    elif len(title) > TITLE_MAX:
        issues.append(f"标题超过 {TITLE_MAX} 字符（{len(title)}）")

    # 1b) Amazon 标题同一单词最多出现 2 次（实测平台 code:100470 直接拒绝）
    if title:
        try:
            import re as _re
            from collections import Counter as _Counter
            words = _re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", title.lower())
            repeated = [w for w, n in _Counter(words).items() if n > 2]
            if repeated:
                issues.append("标题同一单词出现超过 2 次（Amazon 会拒绝 code:100470）："
                              + "、".join(repeated[:5]))
        except Exception:
            pass

    # 2) 非中文站点文案含中文
    if not lang.startswith("zh"):
        for key, text in _scan_copy_fields(p):
            if has_chinese(text):
                label = _field_label("bulletPoint") if key.startswith("bullet") else _field_label(key)
                issues.append(f"{label}含中文字符（站点语言 {lang}）")
                break

    # 3) 五点描述（schema: maxItems=10 / maxLength=700）
    bullets = split_bullets(p.get("bulletPoint") or "")
    if len(bullets) > BULLET_MAX_ITEMS:
        issues.append(f"卖点数量超过 {BULLET_MAX_ITEMS} 条（{len(bullets)}）")
    if 0 < len(bullets) < 3:
        issues.append(f"卖点不足 3 条（{len(bullets)} 条，Amazon 建议 5 条）")
    for i, b in enumerate(bullets, 1):
        if len(b) > BULLET_MAX_CHARS:
            issues.append(f"卖点 {i} 超过 {BULLET_MAX_CHARS} 字符（{len(b)}）")
        if "±" in b or "●" in b or _HTML_TAG_RE.search(b) or _EMOJI_RE.search(b):
            issues.append(f"卖点 {i} 含 ±/●/HTML/emoji 等非法字符")

    # 4) 产品描述（schema: required / maxUtf8ByteLength=20000）
    desc = str(p.get("description") or "")
    if not strip_html(desc).strip():
        issues.append("产品描述为空（Amazon 必填）")
    if len(desc.encode("utf-8")) > DESCRIPTION_MAX_BYTES:
        issues.append(f"产品描述超过 {DESCRIPTION_MAX_BYTES} 字节"
                      f"（{len(desc.encode('utf-8'))}）")
    if _BAD_DESC_TAG_RE.search(desc):
        issues.append("产品描述含 <script>/<style>/<iframe> 等危险标签")

    # 5) Search Terms（schema: maxLength=500 / maxUtf8ByteLength=2000）
    terms = str(p.get("searchTerms") or "")
    if terms:
        if len(terms) > SEARCH_TERMS_MAX_CHARS:
            issues.append(f"Search Terms 超过 {SEARCH_TERMS_MAX_CHARS} 字符（{len(terms)}）")
        if len(terms.encode("utf-8")) > SEARCH_TERMS_MAX_BYTES:
            issues.append(f"Search Terms 超过 {SEARCH_TERMS_MAX_BYTES} 字节"
                          f"（{len(terms.encode('utf-8'))}）")

    # 6) 站点
    if not mp:
        issues.append("marketPlaceId 为空")
    else:
        known = mp in SITE_META or (
            str((meta or {}).get("marketplace_id") or "") == mp
            and bool(str((meta or {}).get("code") or "").strip()))
        if not known:
            issues.append(f"站点 {mp} 不在支持的 Amazon 站点列表")

    # 7) 类目
    cid = str(p.get("categoryId") or "").strip()
    if not cid or cid.lower() in ("none", "null") or not cid.isdigit():
        issues.append(f"类目无效：{cid or '空'}")
    path = str(p.get("categoryBrowsePath") or "").strip()
    if cid and path and path.rsplit("/", 1)[-1] != cid:
        issues.append("categoryBrowsePath 末段与 categoryId 不一致")

    # 8) SKU / 品牌 / 制造商
    if not str(p.get("sku") or "").strip():
        issues.append("SKU 为空")
    if not str(p.get("brand") or "").strip():
        issues.append("品牌为空")
    if not str(p.get("manufacturer") or "").strip():
        issues.append("制造商为空")

    # 9) 主图
    main_image = str(p.get("mainImage") or "").strip()
    if not main_image or not re.match(r"^https?://", main_image, re.I):
        issues.append("主图为空或不是 http(s) 图片地址")

    # 10) 价格
    std = p.get("standardPrice")
    sale = p.get("saleSalePrice")
    if std is not None:
        std_f = _to_float(std)
        if std_f is None or std_f <= 0:
            issues.append("标准价必须大于 0（或留空）")
    if sale is not None:
        sale_f = _to_float(sale)
        if sale_f is None or sale_f <= 0:
            issues.append("促销价必须大于 0（或留空）")
        elif std is not None:
            std_f = _to_float(std)
            if std_f is not None and sale_f >= std_f:
                issues.append("促销价必须小于标准价")

    # 11) 促销词 / 联系方式 / 绝对化词
    whitelist = tuple(PROMO_WHITELIST) + tuple(
        str(w) for w in ((site_meta or {}).get("promo_whitelist") or []))
    for key, text in _scan_copy_fields(p):
        hits = _promo_hits(text, whitelist)
        if hits:
            label = _field_label("bulletPoint") if key.startswith("bullet") else _field_label(key)
            issues.append(f"{label}含促销/联系方式/绝对化词：{', '.join(hits[:5])}")

    return issues


def build_warnings(payload: dict, *, product: dict | None = None) -> list[str]:
    """非阻断提醒（docs §6 提醒区）。"""
    p = payload or {}
    warns: list[str] = []
    title = str(p.get("title") or "").strip()
    if title and (len(title) < 30 or len(title) > 150):
        warns.append(f"标题长度 {len(title)} 字符（建议 30~150）")

    bullets = split_bullets(p.get("bulletPoint") or "")
    if len(bullets) < 5 or len(bullets) > 5:
        warns.append(f"卖点 {len(bullets)} 条（Amazon 建议 5 条，硬上限 {BULLET_MAX_ITEMS}）")
    for i, b in enumerate(bullets, 1):
        if len(b) > BULLET_SUGGEST_CHARS:
            warns.append(f"卖点 {i} 长度 {len(b)} 字符（建议 ≤{BULLET_SUGGEST_CHARS}）")

    terms = str(p.get("searchTerms") or "")
    if len(terms.encode("utf-8")) > 250:
        warns.append(f"Search Terms {len(terms.encode('utf-8'))} 字节（建议 ≤250）")

    desc = str(p.get("description") or "")
    plain_desc = strip_html(desc)
    if desc and len(plain_desc) > 2000:
        warns.append(f"产品描述 {len(plain_desc)} 字符（建议 ≤2000）")

    if not str(p.get("fullCid") or "").strip():
        warns.append("fullCid 为空（可能是采集箱旧数据）")
    category_path = str(p.get("categoryBrowsePath") or "")
    if not category_path or "未分类" in category_path:
        warns.append("商品类目为「未分类」或为空，保存前建议重新选择类目")

    if not str(p.get("extraImagesStr") or "").strip():
        warns.append("没有附加图（只有主图）")
    if _to_int(p.get("quantity"), 0) <= 0:
        warns.append("库存为 0（草稿允许，发布前需补库存）")
    if p.get("standardPrice") is None:
        warns.append("标准价为 0/空（草稿允许，发布前需设价）")
    if str(p.get("brand") or "").strip().lower() == "generic":
        warns.append("品牌为 Generic（Amazon 可能要求品牌备案）")

    attr = p.get("productAttr")
    if not attr:
        warns.append("productAttr 为空（类目属性会丢失）")
    else:
        data = attr
        if isinstance(attr, str):
            try:
                data = json.loads(attr)
            except Exception:
                data = attr
        if data in ({}, [], "", None):
            warns.append("productAttr 为空对象 {}（类目属性会丢失）")

    missing_required = missing_common_attributes(attr)
    if missing_required:
        warns.append(
            "发布必填属性缺失：" + "、".join(missing_required)
            + "（自动补齐默认按 SKU/标题/品牌/模板推导；仍缺的需在 productAttr "
              "手动填写，否则发布会失败）")

    if product:
        old_mp = str(product.get("marketPlaceId") or "").strip()
        new_mp = str(p.get("marketPlaceId") or "").strip()
        if old_mp and new_mp and old_mp != new_mp:
            warns.append(f"站点已从 {old_mp} 切换到 {new_mp}，类目需为新站点重新选择")

    return warns


__all__ = [
    "SAVE_KEYS", "OVERRIDE_KEYS", "OP_DEFAULT", "PROMO_WHITELIST", "REQUIRED_COMMON",
    "AUTOFILL_CFG_KEY",
    "TITLE_MAX", "BULLET_MAX_ITEMS", "BULLET_MAX_CHARS", "BULLET_SUGGEST_CHARS",
    "DESCRIPTION_MAX_BYTES", "SEARCH_TERMS_MAX_CHARS", "SEARCH_TERMS_MAX_BYTES",
    "is_chinese_site", "remove_emoji",
    "split_bullets", "join_bullets", "build_amazon_payload", "validate_amazon_payload",
    "build_warnings", "apply_site",
    "parse_product_attr", "language_tag_for", "format_attribute_value",
    "merge_attributes", "attribute_present", "missing_common_attributes",
    "autofill_enabled", "required_attribute_names", "attribute_value_text",
    "format_attribute_summary",
]

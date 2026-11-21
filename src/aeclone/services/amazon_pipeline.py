"""Amazon 刊登流水线（服务层，不含 UI）。

一条链路：读 editSp 详情 → 站点/币种 → 类目校验 →（可选）AI 文案 →
组装 39 键保存包 → 必填/合规校验 → 违禁词提示 → 保存。

生产用户流程
------------
UI 的「保存/发布」应调用 ``services.amazon_auto.auto_publish``（真实发布 + 失败自动修复重发）。
本模块的 ``dry_run`` 参数仅供测试/内部校验使用；``AURORA_NO_SAVE`` / ``AURORA_TEST``
仅是 CI 安全护栏（命中时拒绝真实写），生产（无 env）没有预演分支/文案。

安全语义
--------
- ``dry_run=True``（测试内部）绝不调用 ``save_product`` / ``save_to_amazon``；
- 真实保存必须通过 ``core.guard.require(cfg)``，并统一走
  ``services.amazon_publish.save_to_amazon``（唯一写收口）；
- 任何异常都转成 ``ok=False`` + 失败步骤，不静默吞掉；
- AI 失败只记 notes/warnings，不阻断后续校验（保留手填内容）。
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from ..integrations.amazon import parse_required_attrs
from .amazon_payload import (
    BULLET_MAX_ITEMS, DESCRIPTION_MAX_BYTES, REQUIRED_COMMON,
    SEARCH_TERMS_MAX_CHARS, TITLE_MAX, attribute_present, attribute_value_text,
    autofill_enabled, build_amazon_payload, build_warnings,
    format_attribute_summary, join_bullets, parse_product_attr,
    split_bullets, validate_amazon_payload,
)
from .amazon_publish import (
    OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, make_client,
)
from .amazon_prompts import (
    BULLET_DEFAULT_COUNT, build_attributes_prompt, build_bullets_prompt,
    build_description_prompt, build_search_terms_prompt, build_sku_prompt,
    build_title_prompt, clean_bullet, clean_for_site, clean_title,
    parse_attribute_suggestion, parse_bullets, parse_search_terms, parse_sku,
    sanitize_description, site_meta as prompt_site_meta, strip_html,
)

ENV_NO_SAVE = "AURORA_NO_SAVE"
ENV_TEST = "AURORA_TEST"

OP_LABELS = {
    OP_SAVE: "保存草稿",
    OP_MOVE_PENDING: "保存并移入待发布",
    OP_PUBLISH: "保存并发布",
}

AI_FIELDS = ("title", "bullets", "description", "search_terms")
# sku 只支持单字段生成（不参与「AI 一键生成全部」的 4 次调用）
AI_EXTRA_FIELDS = ("sku",)
AI_LABELS = {"title": "标题", "bullets": "五点描述", "description": "产品描述",
             "search_terms": "Search Terms", "sku": "SKU"}


@dataclass
class AmazonStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class AmazonRunResult:
    """一次 Amazon 刊登预演/保存的结果（UI 直接读这些字段渲染）。"""

    product_id: str = ""
    ok: bool = False
    error: str = ""
    steps: list[AmazonStep] = field(default_factory=list)
    preview: bool = False
    saved: bool = False
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    description: str = ""
    search_terms: str = ""
    site: str = ""
    site_code: str = ""
    currency: str = ""
    category_id: str = ""
    op: str = OP_SAVE
    banned_words: list[str] = field(default_factory=list)
    save_result: dict = field(default_factory=dict)
    ai_used: bool = False
    # 发布结果核实（op=2）
    publish_state: str = ""
    publish_error: str = ""
    publish_asin: str = ""
    publish_detail: str = ""
    required_attrs: list[str] = field(default_factory=list)
    attribute_notes: list[str] = field(default_factory=list)

    @property
    def incomplete_steps(self) -> list[AmazonStep]:
        return [s for s in self.steps if not s.ok]

    @property
    def completion_label(self) -> str:
        if self.preview:
            return "预演完成（未保存商品）" if self.ok else "预演失败（未保存商品）"
        if self.publish_state == "online" and self.saved:
            return "保存并发布成功"
        if self.publish_state == "publishFail" and self.publish_error:
            return "已保存，发布失败"
        if self.publish_state == "publishFail" and "审核" in self.publish_detail:
            return "已保存，平台审核中"
        if self.publish_state in ("waitPublish", "publishing"):
            return "已保存，发布处理中"
        if not self.ok:
            return "保存失败"
        return "保存成功" if self.saved else "未保存"

    def log(self) -> str:
        return "\n".join(f"{'✓' if s.ok else '✗'} {s.name}: {s.detail}" for s in self.steps)


def forced_preview() -> bool:
    """AURORA_NO_SAVE / AURORA_TEST 存在时真实写必须降级为预演。"""
    return bool(os.environ.get(ENV_NO_SAVE) or os.environ.get(ENV_TEST))


# ---------------- 内部工具 ----------------
def _cfg_get(cfg, key: str, default=None):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _step(res: AmazonRunResult, name: str, ok: bool, detail: str = "") -> None:
    res.steps.append(AmazonStep(name, ok, detail))


def _fail(res: AmazonRunResult, name: str, error: str) -> AmazonRunResult:
    res.ok = False
    res.error = error
    _step(res, name, False, error)
    return res


def _short(text, limit: int = 160) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s[:limit] if len(s) > limit else s


def _product_of(detail: dict) -> dict:
    """editSp 返回 data → product（兼容直接给 product 的桩）。"""
    if not isinstance(detail, dict):
        return {}
    product = detail.get("product")
    if isinstance(product, dict) and product:
        return dict(product)
    return dict(detail)


def _site_code(meta: dict) -> str:
    return str((meta or {}).get("code") or "")


def _fill_result(res: AmazonRunResult, payload: dict) -> None:
    """把保存包里的关键字段回填到结果对象。"""
    if not isinstance(payload, dict):
        return
    res.title = str(payload.get("title") or "")
    res.bullets = split_bullets(payload.get("bulletPoint") or "")
    res.description = str(payload.get("description") or "")
    res.search_terms = str(payload.get("searchTerms") or "")
    res.site = str(payload.get("marketPlaceId") or "") or res.site
    res.currency = str(payload.get("currency") or "") or res.currency
    res.category_id = str(payload.get("categoryId") or "") or res.category_id
    meta = prompt_site_meta(res.site) if res.site else {}
    if not res.site_code:
        res.site_code = _site_code(meta)


def _merge_overrides(overrides: dict | None) -> dict:
    """只保留 §3.2.0 约定的 overrides 键（空串视作未传）。"""
    from .amazon_payload import OVERRIDE_KEYS
    src = overrides or {}
    if not isinstance(src, dict):
        return {}
    return {k: v for k, v in src.items() if k in OVERRIDE_KEYS}


def _current_text_fields(product: dict, ov: dict) -> dict:
    """取当前生效的文案字段（overrides 优先，空则不覆盖商品原值）。"""
    title = str(ov.get("title") or product.get("title") or "")
    bullets = ov.get("bullets")
    if not bullets:
        bullets = split_bullets(product.get("bulletPoint") or "")
    if isinstance(bullets, str):
        bullets = split_bullets(bullets)
    desc = str(ov.get("description") or product.get("description") or "")
    terms = str(ov.get("search_terms") or product.get("searchTerms") or "")
    return {"title": title, "bullets": list(bullets or []),
            "description": desc, "search_terms": terms}


def _product_for_ai(product: dict, ov: dict, detail: dict | None = None) -> dict:
    """给 AI 的上下文：商品事实 + 当前表单值 + 站点等。"""
    p = dict(product or {})
    text = _current_text_fields(p, ov)
    p["title"] = text["title"]
    p["bulletPoint"] = join_bullets(text["bullets"])
    p["description"] = text["description"]
    p["searchTerms"] = text["search_terms"]
    for key in ("brand", "manufacturer", "sku"):
        if ov.get(key):
            p[key] = ov[key]
    if detail:
        p.setdefault("site", detail.get("site"))
        p.setdefault("productType", detail.get("productType"))
    return p


# ---------------- 必填属性自动补齐 / 发布结果核实 ----------------
_MODEL_RE = re.compile(r"\b[A-Z]{2,6}\d{2,6}(?:[-/][A-Z0-9]{1,8})*\b")
_MODEL_LABEL_RE = re.compile(
    r"\bmodel(?:\s*(?:no\.?|number|#))?\s*[:：]\s*([A-Za-z0-9][A-Za-z0-9_\-/\.]{1,40})",
    re.I)
_REVIEW_HINTS = ("100521", "审查", "审核", "under review", "pending review", "in review")
_ORIGIN_RE = re.compile(r"\b(china|chinese|cn|prc)\b", re.I)


def _extract_model(text) -> str:
    """从标题/SKU 提取型号（如 ``HW20-28``），最长 40 字符。"""
    s = str(text or "")
    match = _MODEL_LABEL_RE.search(s)
    if match:
        return match.group(1).strip(" .-_")[:40]
    match = _MODEL_RE.search(s)
    if match:
        return match.group(0).strip()[:40]
    return ""


def _origin_code(text) -> str:
    """标题/描述出现 China/CN/Chinese/PRC → ``CN``（Amazon country_of_origin 枚举）。"""
    return "CN" if _ORIGIN_RE.search(str(text or "")) else ""


def _first_text(source: dict, keys) -> str:
    """按优先级取第一个非空字符串字段。"""
    for key in keys:
        value = (source or {}).get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


# 常见「与保存包字段同名」的必填属性：errMsg 偶尔会把这些报成缺失属性
# （实际是 payload 字段），补齐时直接复用当前文案/品牌，避免再向用户要一遍。
_PAYLOAD_ATTRIBUTE_FIELDS = {
    "item_name": "title",
    "product_description": "description",
    "bullet_point": "bulletPoint",
    "generic_keyword": "searchTerms",
    "brand": "brand",
    "manufacturer": "manufacturer",
}
# 商品数据里可能直接带的型号字段（标题/SKU 都提不出来时的兜底）
_PRODUCT_MODEL_KEYS = ("modelNumber", "model_number", "modelName", "model",
                       "partNumber", "part_number")

# AI 属性兜底：价格/尺寸/图片/识别码/物流等结构敏感属性不能由 AI 编造 → 直接跳过
AI_ATTRIBUTE_SKIP = (
    "list_price", "item_dimensions", "item_depth_width_height",
    "item_package_dimensions", "item_package_weight", "item_weight",
    "fulfillment_availability", "main_product_image_locator",
    "other_product_image_locator", "merchant_shipping_group",
    "gpsr_safety_attestation", "externally_assigned_product_identifier",
    "variation_theme", "supplier_declared_dg_hz_regulation",
)
AI_ATTRIBUTE_MAX_FIELDS = 6


# 发布必填属性的确定性映射（错误驱动第二轮；无 template 也能补）
DETERMINISTIC_BOOLEAN_DEFAULTS = ("contains_liquid_contents", "batteries_required",
                                  "is_expiration_dated_product", "is_assembly_required")
_ASSEMBLY_RE = re.compile(r"\bassembly\b|\bassemble\b|组装|拼装", re.I)
# Amazon power_source_type 枚举（按标题/描述关键词，取最早出现的词）
POWER_SOURCE_ENUM_MAP = (
    ("hydraulic", "hydraulic_powered"),
    ("diesel", "diesel_powered"),
    ("gasoline", "gasoline_powered"),
    ("petrol", "gasoline_powered"),
    ("battery", "Battery Powered"),
    ("lithium", "Battery Powered"),
    ("electric", "electric"),
)
POWER_SOURCE_FALLBACK = "diesel_powered"
_UNIT_TO_INCHES = {
    "mm": 1.0 / 25.4, "millimeter": 1.0 / 25.4, "millimeters": 1.0 / 25.4,
    "毫米": 1.0 / 25.4,
    "cm": 10.0 / 25.4, "centimeter": 10.0 / 25.4, "centimeters": 10.0 / 25.4,
    "厘米": 10.0 / 25.4,
    "m": 1000.0 / 25.4, "meter": 1000.0 / 25.4, "meters": 1000.0 / 25.4, "米": 1000.0 / 25.4,
    "in": 1.0, "inch": 1.0, "inches": 1.0, "英寸": 1.0,
    "ft": 12.0, "feet": 12.0, "foot": 12.0, "英尺": 12.0,
}
_DIM_TEXT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[*xX×]\s*(\d+(?:\.\d+)?)\s*[*xX×]\s*"
    r"(\d+(?:\.\d+)?)\s*"
    r"(mm|cm|inches|inch|in|ft|feet|foot|m|毫米|厘米|米|英寸|英尺)?"
    r"(?![A-Za-z])",
    re.I)
_DIM_UNIT_SEARCH_RE = re.compile(
    r"^\s*(mm|cm|inches|inch|in|ft|feet|foot|m|毫米|厘米|米|英寸|英尺)(?![A-Za-z])",
    re.I)


def _power_source_enum(text) -> tuple[str, str]:
    """返回 (枚举值, 依据说明)；无关键词时返回 ("", "")。"""
    low = str(text or "").lower()
    best = None
    for keyword, enum_value in POWER_SOURCE_ENUM_MAP:
        idx = low.find(keyword)
        if idx >= 0 and (best is None or idx < best[0]):
            best = (idx, enum_value, keyword)
    if best:
        return best[1], f"关键词 {best[2]}"
    return "", ""


def _norm_unit(unit) -> str:
    u = str(unit or "").strip().lower()
    return u if u in _UNIT_TO_INCHES else ("mm" if not u else u)


def _to_inches(value, unit) -> str:
    try:
        num = float(str(value).strip())
    except Exception:
        return ""
    factor = _UNIT_TO_INCHES.get(_norm_unit(unit), 1.0 / 25.4)
    inches = num * factor
    return str(int(round(inches))) if inches > 0 else ""


def _raw_dimension_items(raw, marketplace_id: str = ""):
    """把 productAttr item_dimensions / 模板 dimensions 转成 Amazon 原始 item 列表。"""
    mp = str(marketplace_id or "").strip()
    if isinstance(raw, list):
        items = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            if not all(k in entry for k in ("length", "width", "height")):
                continue
            item = dict(entry)
            item.pop("value", None)
            if mp:
                item["marketplace_id"] = mp
            items.append(item)
        return items or None
    if isinstance(raw, dict):
        if "value" in raw and isinstance(raw.get("value"), dict):
            return _raw_dimension_items([raw.get("value")], marketplace_id)
        item = {}
        for key in ("length", "width", "height"):
            if key not in raw:
                continue
            val = raw.get(key)
            if isinstance(val, dict):
                item[key] = {"value": str(val.get("value") or "").strip(),
                             "unit": _norm_unit(val.get("unit"))}
            elif val not in (None, ""):
                item[key] = {"value": str(val).strip(),
                             "unit": _norm_unit(raw.get(f"{key}_unit"))}
        if len(item) == 3:
            if mp:
                item["marketplace_id"] = mp
            return [item]
    return None


def _dims_from_product(product: dict, marketplace_id: str = ""):
    for key in ("itemDimensions", "packageDimensions", "package_dimensions"):
        items = _raw_dimension_items((product or {}).get(key), marketplace_id)
        if items:
            return items
    return None


def _dims_from_text(text: str, marketplace_id: str = ""):
    """标题/描述里的 ``6200*1960*2800mm`` / ``6200x1960x2800`` → inches 结构。"""
    match = _DIM_TEXT_RE.search(str(text or ""))
    if not match:
        return None
    unit = match.group(4)
    if not unit:
        tail = str(text or "")[match.end():match.end() + 8]
        tail_match = _DIM_UNIT_SEARCH_RE.match(tail)
        unit = tail_match.group(1) if tail_match else "mm"   # 默认按毫米（常见采集数据）
    values = [_to_inches(match.group(i), unit) for i in (1, 2, 3)]
    if not all(values):
        return None
    item = {
        "length": {"value": values[0], "unit": "inches"},
        "width": {"value": values[1], "unit": "inches"},
        "height": {"value": values[2], "unit": "inches"},
    }
    mp = str(marketplace_id or "").strip()
    if mp:
        item["marketplace_id"] = mp
    return [item]


def _dimension_value(key: str, product: dict, existing: dict, text: str,
                     template: dict, marketplace_id: str):
    """item_dimensions / item_depth_width_height 的取值（返回 (items|None, note)）。"""
    attr_dims = _raw_dimension_items((existing or {}).get("item_dimensions"),
                                     marketplace_id)
    prod_dims = _dims_from_product(product, marketplace_id)
    text_dims = _dims_from_text(text, marketplace_id)
    tpl_dims = _raw_dimension_items((template or {}).get(key), marketplace_id)
    if key == "item_dimensions":
        candidates = [("productAttr.item_dimensions", attr_dims),
                      ("product.itemDimensions", prod_dims),
                      ("标题/描述尺寸", text_dims),
                      ("同类模板", tpl_dims)]
    else:
        candidates = [("product.itemDimensions", prod_dims),
                      ("productAttr.item_dimensions", attr_dims),
                      ("标题/描述尺寸", text_dims),
                      ("同类模板", tpl_dims)]
    for source, items in candidates:
        if items:
            return items, f"{key}：来源 {source}"
    return None, f"{key}：无法自动补齐（无 item_dimensions/尺寸文本），需用户填写"


def _template_value(raw):
    """productAttr 里某属性的原始值 → 首个 item 的 value（便于套模板）。"""
    value = raw
    if isinstance(value, list):
        if not value:
            return None
        first = value[0]
        return first.get("value") if isinstance(first, dict) else first
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _normalize_required(required) -> list[str]:
    if required is None:
        return list(REQUIRED_COMMON)
    if isinstance(required, str):
        parts = re.split(r"[,;，；\s]+", required)
    else:
        try:
            parts = list(required)
        except TypeError:
            parts = []
    out: list[str] = []
    for item in parts:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out or list(REQUIRED_COMMON)


def _required_from_overrides(ov: dict, cfg) -> list[str]:
    raw = ov.get("required_attributes") if isinstance(ov, dict) else None
    if not raw:
        raw = _cfg_get(cfg, "amazon_last_category_required", "") or ""
    return _normalize_required(raw if raw else None)


def _existing_attributes(product: dict, overrides: dict) -> dict:
    data = parse_product_attr((product or {}).get("productAttr"))
    attrs = (overrides or {}).get("attributes")
    if isinstance(attrs, dict):
        for key, value in attrs.items():
            data[str(key)] = value
    return data


def _list_price_value(product: dict, overrides: dict, cfg, marketplace_id: str,
                      template_value=None):
    """list_price：以模板结构为准，amount=库存标准价，currency=站点币种。"""
    raw_price = (overrides or {}).get("standard_price")
    if raw_price in (None, ""):
        raw_price = (product or {}).get("standardPrice")
    if raw_price in (None, "") and _cfg_get(cfg, "amazon_price", ""):
        raw_price = _cfg_get(cfg, "amazon_price")
    try:
        amount = float(str(raw_price).strip())
    except Exception:
        return None
    if amount <= 0:
        return None
    meta = prompt_site_meta(marketplace_id) if marketplace_id else {}
    currency = (str((overrides or {}).get("currency") or "").strip()
                or str((product or {}).get("standardPriceCurrency") or "").strip()
                or str(meta.get("currency") or "").strip())
    price_str = f"{amount:.2f}"
    mp = str(marketplace_id or "").strip()
    # 以模板结构为准：模板若为嵌套 {currency,amount} 就更新 amount；若是顶层 item 就填 value
    if isinstance(template_value, list) and template_value and isinstance(template_value[0], dict):
        item = dict(template_value[0])
        inner = item.get("value")
        if isinstance(inner, dict):
            inner = dict(inner)
            inner["currency"] = currency or inner.get("currency") or ""
            inner["amount"] = round(amount, 2)
            item["value"] = inner
        else:
            item["value"] = price_str
            if "currency" in item or not currency:
                item["currency"] = currency or item.get("currency") or ""
        if mp:
            item["marketplace_id"] = mp
        return [item]
    # 实测格式：[{"currency": 币种, "value": "价格", "marketplace_id": mp}]
    item = {"currency": currency or "", "value": price_str}
    if mp:
        item["marketplace_id"] = mp
    return [item]


def _autofill_context(product: dict, cfg, overrides: dict) -> dict:
    """收集确定性补齐需要的商品事实（文案/品牌/SKU/型号/产地）。"""
    p = dict(product or {})
    ov = dict(overrides or {}) if isinstance(overrides, dict) else {}
    title = str(ov.get("title") or p.get("title") or "")
    description = str(ov.get("description") or p.get("description") or "")
    bullets = ov.get("bullets")
    if not bullets:
        bullets = split_bullets(p.get("bulletPoint") or "")
    if isinstance(bullets, str):
        bullets = split_bullets(bullets)
    search_terms = str(ov.get("search_terms") or p.get("searchTerms") or "")
    sku = str(ov.get("sku") or p.get("sku")
              or _cfg_get(cfg, "amazon_sku", "") or "").strip()
    brand = str(ov.get("brand") or p.get("brand")
                or _cfg_get(cfg, "amazon_brand", "") or "").strip()
    manufacturer = str(ov.get("manufacturer") or p.get("manufacturer")
                       or _cfg_get(cfg, "amazon_manufacturer", "") or "").strip()
    # 型号来源：标题型号 → 商品自带型号字段 → SKU（不编造）
    model = _extract_model(title) or _extract_model(sku)
    if not model:
        model = _first_text(p, _PRODUCT_MODEL_KEYS)
    desc_text = strip_html(description)
    origin = _origin_code(f"{title} {desc_text}") or _origin_code(sku)
    contact_cfg = str(_cfg_get(cfg, "amazon_manufacturer_contact", "") or "").strip()
    contact = contact_cfg or f"{manufacturer or brand or 'Generic'}, {origin or 'CN'}"
    # 与保存包字段同名（key 是保存包的 camelCase 字段，见 _PAYLOAD_ATTRIBUTE_FIELDS）
    payload_values = {
        "title": title,
        "description": description,
        "bulletPoint": join_bullets(list(bullets or [])),
        "searchTerms": search_terms,
        "brand": brand,
        "manufacturer": manufacturer,
    }
    return {
        "product": p, "overrides": ov, "title": title, "description": description,
        "desc_text": desc_text, "bullets": list(bullets or []),
        "search_terms": search_terms, "sku": sku, "brand": brand,
        "manufacturer": manufacturer, "model": model, "origin": origin,
        "contact": contact, "payload_values": payload_values,
        "text_ctx": f"{title} {desc_text}",
    }


def _autofill_attributes_with_notes(product: dict, cfg, marketplace_id: str = "",
                                    overrides: dict | None = None,
                                    required=None, template: dict | None = None):
    """内部实现：返回 (要补的属性 dict, 无法补齐的 notes)。"""
    p = dict(product or {})
    ov = dict(overrides or {}) if isinstance(overrides, dict) else {}
    existing = _existing_attributes(p, ov)
    required_list = _normalize_required(required) if required is not None \
        else _normalize_required(ov.get("required_attributes")) \
        if ov.get("required_attributes") else list(REQUIRED_COMMON)
    template = template if isinstance(template, dict) else {}
    ctx = _autofill_context(p, cfg, ov)
    title = ctx["title"]
    desc_text = ctx["desc_text"]
    sku = ctx["sku"]
    model = ctx["model"]
    origin = ctx["origin"]
    contact = ctx["contact"]
    payload_values = ctx["payload_values"]
    text_ctx = ctx["text_ctx"]

    filled: dict = {}
    notes: list[str] = []
    for key in required_list:
        if attribute_present(existing, key):
            continue
        tpl = _template_value(template.get(key))
        value = None
        if key in DETERMINISTIC_BOOLEAN_DEFAULTS:
            if key == "is_assembly_required":
                value = bool(_ASSEMBLY_RE.search(text_ctx))
            else:
                value = False
        elif key == "model_number":
            value = (str(model or sku)[:40] if (model or sku) else None)
        elif key == "model_name":
            # 型号名称不含类型/品牌/颜色：优先型号，其次 SKU（≤100 字符）
            value = (str(model or sku)[:100] if (model or sku) else None)
        elif key == "part_number":
            value = (sku or model or _first_text(p, ("partNumber", "part_number"))
                     or None)
        elif key == "country_of_origin":
            if origin:
                value = origin
            elif isinstance(tpl, str) and tpl.strip():
                value = tpl
            else:
                value = "CN"
                notes.append("country_of_origin：标题/描述未找到产地依据，使用默认 CN")
        elif key == "manufacturer_contact_information":
            value = contact
        elif key == "list_price":
            value = _list_price_value(p, ov, cfg, marketplace_id, template.get(key))
        elif key == "power_source_type":
            enum_value, source = _power_source_enum(text_ctx)
            if isinstance(tpl, str) and tpl.strip():
                value = tpl
            elif enum_value:
                value = enum_value
            else:
                value = POWER_SOURCE_FALLBACK
                notes.append(f"power_source_type：无明确依据，使用安全默认 "
                             f"{POWER_SOURCE_FALLBACK}")
        elif key in ("item_dimensions", "item_depth_width_height"):
            value, dim_note = _dimension_value(
                key, p, existing, text_ctx, template, marketplace_id)
            if value is None and dim_note:
                notes.append(dim_note)
        elif key == "supplier_declared_dg_hz_regulation":
            if isinstance(tpl, str) and tpl.strip():
                value = tpl
            else:
                value = "not_applicable"
        elif key in _PAYLOAD_ATTRIBUTE_FIELDS:
            # 与保存包同名的属性：直接复用当前文案/品牌值（保真，不编造）
            raw_value = payload_values.get(_PAYLOAD_ATTRIBUTE_FIELDS[key])
            if key == "product_description":
                raw_value = strip_html(raw_value) or raw_value
            value = str(raw_value or "").strip() or None
        elif key == "item_package_quantity":
            value = 1
            notes.append("item_package_quantity：按单件包装默认 1，套装商品请手工修改")
        elif key == "externally_assigned_product_identifier":
            value = _first_text(p, ("standardProductId", "standard_product_id",
                                    "upc", "ean", "gtin")) or None
            if not value:
                notes.append("externally_assigned_product_identifier："
                             "商品没有 UPC/EAN/GTIN，需用户填写")
        elif key == "color":
            value = _first_text(p, ("color", "colour", "colorName")) or None
            if not value:
                notes.append("color：商品数据无颜色，需用户填写")
        elif key == "variation_theme":
            value = _first_text(p, ("variationTheme", "variation_theme")) or None
            if not value:
                notes.append("variation_theme：非变种商品可留空/需按平台变种设置")
        else:
            value = tpl
        if value in (None, "", [], {}):
            if not any(n.startswith(f"{key}：") for n in notes):
                notes.append(f"{key}：无法自动补齐，需用户填写")
            continue
        if isinstance(value, str):
            value = value.strip()
            if not value:
                if not any(n.startswith(f"{key}：") for n in notes):
                    notes.append(f"{key}：无法自动补齐，需用户填写")
                continue
        filled[key] = value
        existing[key] = value
    return filled, notes


def autofill_attributes(product: dict, cfg, marketplace_id: str = "",
                        overrides: dict | None = None, required=None,
                        template: dict | None = None) -> dict:
    """自动补齐缺失的发布必填属性（只补缺失项；调用方可用 overrides 完全覆盖）。

    - ``required``：本次要补的属性名（来自上次发布失败 errMsg / schema），
      未显式传入时读 ``overrides["required_attributes"]``，默认 REQUIRED_COMMON；
    - ``template``：同类目在线商品的 productAttr，用来套 enum/结构（如 list_price/dims）。

    确定性映射（错误驱动第二轮，无 template 也能补）：
    - 布尔：contains_liquid_contents / batteries_required / is_expiration_dated_product=False，
      is_assembly_required=标题/描述含 assembly/组装；
    - supplier_declared_dg_hz_regulation="not_applicable"（有模板以模板为准）；
    - power_source_type：hydraulic_powered / diesel_powered / gasoline_powered /
      Battery Powered / electric（取最早关键词；无依据 → diesel_powered 并在 notes 注明）；
    - country_of_origin="CN"（文案有 China/CN 依据优先，否则默认 CN 并注明）；
    - list_price=[{currency, value, marketplace_id}]（amount=库存标准价）；
    - item_dimensions / item_depth_width_height：productAttr.item_dimensions →
      product.itemDimensions/packageDimensions → 标题/描述尺寸正则（换算 inches）→ 模板；
    - model_number/model_name/part_number：标题型号 → 商品型号字段 → SKU（不编造）；
    - manufacturer_contact_information：cfg.amazon_manufacturer_contact 或
      「制造商/品牌, 产地(默认 CN)」；
    - item_name/product_description/bullet_point/generic_keyword/brand/manufacturer：
      与保存包字段同名，直接复用当前文案/品牌；
    - item_package_quantity 默认 1；color / variation_theme / 识别码只从商品真实字段取，
      没有则 notes 提示用户填写；
    - 其它未知属性：有模板用模板，否则 notes 提示「无法自动补齐，需用户填写」。

    返回 {属性名: 原始值}，由 ``build_amazon_payload`` 格式化成 productAttr JSON。
    """
    filled, _notes = _autofill_attributes_with_notes(
        product, cfg, marketplace_id, overrides, required=required, template=template)
    return filled


def _missing_attr_names(attr_data, required_list) -> list[str]:
    """required_list 里 productAttr 仍然缺失的属性名（顺序保持）。"""
    data = attr_data if isinstance(attr_data, dict) else parse_product_attr(attr_data)
    return [key for key in (required_list or []) if not attribute_present(data, key)]


def ai_suggest_attributes(product: dict, cfg, marketplace_id: str = "",
                          missing: list[str] | None = None) -> tuple[dict, list[str]]:
    """AI 兜底：对确定性映射补不了的属性逐个生成建议值（不写平台、不自动保存）。

    - 每个属性单独一次 AI 调用并独立 try/except：某字段失败不影响其它字段；
    - ``AI_ATTRIBUTE_SKIP`` 里的价格/尺寸/图片/识别码/物流等结构敏感属性直接跳过，
      避免 AI 编造（只提示「需用户填写」）；
    - 返回 ``({属性名: 建议值}, notes)``。建议值不会自动写平台：
      ``run_amazon_product(use_ai=True)`` 只在仍有缺失时把建议并入
      ``overrides["attributes"]``（保存前仍走本地校验与 confirm_write 护栏），
      UI 也可只展示建议、由用户确认后再填。
    """
    keys: list[str] = []
    for item in (missing or []):
        name = str(item or "").strip()
        if name and name not in keys:
            keys.append(name)
    if not keys:
        return {}, ["AI 属性建议：没有需要建议的属性"]
    mp = _marketplace_for(product or {}, cfg, marketplace_id)
    notes: list[str] = []
    out: dict = {}
    skipped = [key for key in keys if key in AI_ATTRIBUTE_SKIP]
    targets = [key for key in keys if key not in AI_ATTRIBUTE_SKIP]
    for key in skipped:
        notes.append(f"{key}：价格/尺寸/图片/识别码等结构属性不由 AI 生成，需用户填写")
    if len(targets) > AI_ATTRIBUTE_MAX_FIELDS:
        notes.append(f"AI 属性建议：一次最多建议 {AI_ATTRIBUTE_MAX_FIELDS} 项，"
                     f"其余 {len(targets) - AI_ATTRIBUTE_MAX_FIELDS} 项需用户填写")
        targets = targets[:AI_ATTRIBUTE_MAX_FIELDS]
    if not targets:
        return {}, notes
    try:
        client = _ai_client(cfg)
        model = _ai_model(cfg)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"AI 属性建议失败：{_short(exc)}")
        return {}, notes
    for key in targets:
        try:
            prompt = build_attributes_prompt(product or {}, cfg, mp, key)
            text = client.chat(model, [{"role": "user", "content": prompt}],
                               max_tokens=180)
            value = parse_attribute_suggestion(key, text)
            if value in (None, "", [], {}):
                notes.append(f"{key}：AI 无法从原文确定，需用户填写")
                continue
            out[key] = value
            notes.append(f"{key}：AI 建议 {_short(attribute_value_text(value), 80)}")
        except Exception as exc:  # noqa: BLE001 - 逐字段隔离
            notes.append(f"{key}：AI 建议失败：{_short(exc, 120)}")
    return out, notes


def _classify_publish(raw: dict) -> dict:
    """把 publish_state 原始结果分类成 (status, ok, detail)。"""
    raw = raw if isinstance(raw, dict) else {}
    state = str(raw.get("state") or "unknown").strip()
    low = state.lower()
    err = str(raw.get("err_msg") or "")
    asin = str(raw.get("asin") or "")
    if low == "online":
        return {"status": "online", "ok": True,
                "detail": "发布成功" + (f" · ASIN {asin}" if asin else "")}
    if low in ("publishing", "waitpublish"):
        return {"status": "processing", "ok": True,
                "detail": f"已提交，平台处理中（{state}）"}
    if low == "publishfail":
        if any(h in err or h.lower() in err.lower() for h in _REVIEW_HINTS):
            return {"status": "reviewing", "ok": True,
                    "detail": "已提交，平台审核中（非软件失败）"}
        req = parse_required_attrs(err)
        detail = "发布失败：" + (_short(err, 160) or "平台校验不通过")
        if req:
            detail += " · 缺失必填属性：" + ", ".join(req)
        return {"status": "failed", "ok": False, "detail": detail}
    if low == "draft":
        return {"status": "not_submitted", "ok": False,
                "detail": "未提交成功（商品仍在采集箱）"}
    return {"status": "unknown", "ok": False, "detail": "未能确认发布状态"}


def verify_publish(product_id: str, cfg, *, client=None, wait_seconds=0,
                   interval=10) -> dict:
    """轮询核实发布结果；``wait_seconds=0`` 只查一次。

    返回 ``{"state","status","ok","detail","err_msg","asin","title",
    "required_attrs","counts","checks","waited"}``；status ∈
    ``online|processing|reviewing|failed|not_submitted|unknown``。
    """
    pid = str(product_id or "").strip()
    if not pid:
        return {"state": "unknown", "status": "unknown", "ok": False,
                "detail": "缺少 product_id", "err_msg": "", "asin": "", "title": "",
                "required_attrs": [], "counts": {}, "checks": 0, "waited": 0.0}
    c = client if client is not None else make_client(cfg)
    try:
        wait = max(0.0, float(wait_seconds or 0))
    except Exception:
        wait = 0.0
    try:
        step = max(1.0, float(interval or 10))
    except Exception:
        step = 10.0
    started = time.time()
    deadline = started + wait
    raw: dict = {}
    verdict: dict = {"status": "unknown", "ok": False, "detail": "未能确认发布状态"}
    checks = 0
    while True:
        try:
            result = c.publish_state(pid)
            raw = result if isinstance(result, dict) else {"state": "unknown"}
        except Exception as exc:  # noqa: BLE001
            raw = {"state": "unknown", "err_msg": f"读取发布状态失败：{_short(exc, 120)}",
                   "counts": {}, "error": str(exc)[:200]}
        checks += 1
        verdict = _classify_publish(raw)
        status = verdict["status"]
        if status in ("online", "failed", "reviewing"):
            break
        now = time.time()
        if now >= deadline:
            break
        time.sleep(min(step, max(0.0, deadline - now)))
    err_msg = str(raw.get("err_msg") or "")
    out = {
        "state": str(raw.get("state") or "unknown"),
        "status": verdict["status"],
        "ok": bool(verdict["ok"]),
        "detail": verdict["detail"],
        "err_msg": err_msg,
        "asin": str(raw.get("asin") or ""),
        "title": str(raw.get("title") or ""),
        "required_attrs": parse_required_attrs(err_msg),
        "counts": raw.get("counts") or {},
        "checks": checks,
        "waited": round(time.time() - started, 1),
    }
    if raw.get("error"):
        out["error"] = str(raw.get("error"))[:300]
    return out


def required_attributes_from_schema(category_id: str, product_type: str,
                                    site_code: str, *, cfg=None,
                                    client=None) -> list[str]:
    """best-effort：从 getAttrJsUrl schema JS 提取 ``required:!0`` 属性名。

    失败/缺 client 一律返回 []（不阻断保存/发布）。
    """
    try:
        c = client
        if c is None and cfg is not None:
            c = make_client(cfg)
        if c is None:
            return []
        return list(c.required_attributes_from_schema(
            str(category_id or ""), str(product_type or ""),
            str(site_code or "")) or [])
    except Exception:
        return []


# ---------------- AI 生成 ----------------
def _ai_client(cfg):
    from ..api import ApiClient
    return ApiClient(_cfg_get(cfg, "api_base", ""), _cfg_get(cfg, "api_key", ""),
                     _cfg_get(cfg, "timeout", 180))


def _ai_model(cfg) -> str:
    from . import model_of
    return model_of(cfg, "text")


def _marketplace_for(product: dict, cfg, marketplace_id: str = "") -> str:
    mp = str(marketplace_id or "").strip()
    if not mp:
        mp = str(_cfg_get(cfg, "amazon_site", "") or "").strip()
    if not mp:
        mp = str((product or {}).get("marketPlaceId") or "").strip()
    return mp


def _bullets_count(cfg, count: int = 0) -> int:
    try:
        n = int(count or _cfg_get(cfg, "amazon_bullet_count", BULLET_DEFAULT_COUNT)
                or BULLET_DEFAULT_COUNT)
    except Exception:
        n = BULLET_DEFAULT_COUNT
    return max(1, min(n, BULLET_MAX_ITEMS))


def ai_generate_field(product: dict, cfg, field: str,
                      marketplace_id: str = "", count: int = 5) -> tuple[object, list[str]]:
    """生成单个字段：title / bullets / description / search_terms / sku（均为字符串或列表）。"""
    name = str(field or "").strip().lower()
    allowed = AI_FIELDS + AI_EXTRA_FIELDS
    if name not in allowed:
        return None, [f"未知字段：{field}（可选 {'/'.join(allowed)}）"]
    mp = _marketplace_for(product or {}, cfg, marketplace_id)
    meta = prompt_site_meta(mp) if mp else {}
    want = _bullets_count(cfg, count)
    notes: list[str] = []
    if not mp:
        notes.append("未读取到站点，按英语处理")
    builders = {
        "title": lambda: build_title_prompt(product, cfg, mp),
        "bullets": lambda: build_bullets_prompt(product, cfg, mp, want),
        "description": lambda: build_description_prompt(product, cfg, mp),
        "search_terms": lambda: build_search_terms_prompt(product, cfg, mp),
        "sku": lambda: build_sku_prompt(product, cfg, mp),
    }
    max_tokens = {"title": 320, "bullets": 1800, "description": 2200,
                  "search_terms": 220, "sku": 120}
    try:
        client = _ai_client(cfg)
        text = client.chat(_ai_model(cfg), [
            {"role": "user", "content": builders[name]()},
        ], max_tokens=max_tokens[name])
    except Exception as exc:  # noqa: BLE001
        return _empty_ai_value(name), [f"{AI_LABELS[name]}生成失败：{_short(exc)}"]

    text = str(text or "").strip()
    if not text:
        return _empty_ai_value(name), [f"{AI_LABELS[name]}生成失败：模型返回空内容"]

    if name == "title":
        value = clean_title(clean_for_site(text, mp), TITLE_MAX)
        if not value:
            return "", [f"{AI_LABELS[name]}生成失败：清洗后为空"]
        if len(value) >= TITLE_MAX:
            notes.append(f"标题已按 {TITLE_MAX} 字符词边界截断")
        notes.append(f"已生成（{len(value)} 字符）")
        return value, notes

    if name == "bullets":
        parsed = parse_bullets(text, want)
        cleaned = [clean_bullet(clean_for_site(b, mp)) for b in parsed]
        cleaned = [b for b in cleaned if b][:BULLET_MAX_ITEMS]
        if len(cleaned) < want:
            notes.append(f"AI 只解析出 {len(cleaned)} 条（要求 {want} 条），请人工补齐")
        notes.append(f"已生成 {len(cleaned)} 条")
        return cleaned, notes

    if name == "description":
        value = sanitize_description(clean_for_site(text, mp), DESCRIPTION_MAX_BYTES)
        if not value:
            return "", [f"{AI_LABELS[name]}生成失败：清洗后为空"]
        notes.append(f"已生成（{len(value.encode('utf-8'))} 字节 HTML）")
        return value, notes

    if name == "sku":
        value = parse_sku(clean_for_site(text, mp))
        if not value:
            return "", [f"{AI_LABELS[name]}生成失败：清洗后为空"]
        notes.append(f"已生成（{len(value)} 字符）")
        return value, notes

    # search_terms
    value = parse_search_terms(clean_for_site(text, mp))
    if len(value) > SEARCH_TERMS_MAX_CHARS:
        value = value[:SEARCH_TERMS_MAX_CHARS]
    notes.append(f"已生成（{len(value)} 字符 / {len(value.encode('utf-8'))} 字节）")
    return value, notes


def _empty_ai_value(name: str):
    if name == "bullets":
        return []
    return ""


def ai_generate_all(product: dict, cfg, marketplace_id: str = "",
                    count: int = 5) -> tuple[dict, list[str]]:
    """一次生成 4 个字段；每字段独立 try/except，失败只进 notes。"""
    out: dict = {"title": "", "bullets": [], "description": "", "search_terms": ""}
    notes: list[str] = []
    for name in AI_FIELDS:
        try:
            value, field_notes = ai_generate_field(product, cfg, name, marketplace_id, count)
            out[name] = value
            notes.extend(f"{AI_LABELS[name]}：{n}" for n in field_notes)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{AI_LABELS[name]}：生成异常：{_short(exc)}")
    return out, notes


# ---------------- 只读准备 ----------------
def prepare_save(product_id: str, cfg, overrides: dict | None = None,
                 client=None) -> tuple[dict, dict, list[str], list[str]]:
    """只读：读详情 → 补齐缺失必填属性 → 组装保存包 → 校验。

    返回 (detail, payload, issues, warnings)；不调用任何写接口。
    缺失的发布必填属性会按 SKU/标题/品牌确定性补齐进 ``payload.productAttr``
    （只补缺失、不覆盖已有；``cfg.amazon_autofill_attrs=False`` 可关闭），
    补了什么/哪些补不了都写进 warnings。
    """
    pid = str(product_id or "").strip()
    if not pid:
        raise ValueError("缺少 product_id")
    c = client if client is not None else make_client(cfg)
    detail = c.product_detail(pid)
    if not detail:
        raise RuntimeError(f"读不到商品 {pid}（可能已删除/不属于当前店铺）")
    product = _product_of(detail)
    ov = _merge_overrides(overrides)
    mp = str(ov.get("marketplace_id") or product.get("marketPlaceId") or "")
    shop_id = str(product.get("shopId") or _cfg_get(cfg, "amazon_shop_id", "") or "")
    currency = str(ov.get("currency") or "").strip()
    if not currency and mp and shop_id:
        try:
            currency = c.currency(shop_id, mp) or ""
        except Exception:
            currency = ""
    if not currency:
        currency = str(product.get("standardPriceCurrency") or "")

    # 缺失的发布必填属性自动补齐（只补缺失、不覆盖已有；默认开，可配置关）
    required_list = _required_from_overrides(ov, cfg)
    filled: dict = {}
    attr_notes: list[str] = []
    if autofill_enabled(cfg):
        filled, attr_notes = _autofill_attributes_with_notes(
            product, cfg, mp, ov, required=required_list)
        if filled:
            attrs = dict(ov.get("attributes") or {})
            for key, value in filled.items():
                attrs.setdefault(key, value)
            ov["attributes"] = attrs

    build_notes: list[str] = []
    payload = build_amazon_payload(product, cfg, ov, currency=currency,
                                   notes=build_notes)
    meta = prompt_site_meta(str(payload.get("marketPlaceId") or mp))
    issues = validate_amazon_payload(payload, product=product, site_meta=meta)
    warnings = build_warnings(payload, product=product)
    if filled:
        warnings.insert(0, "已自动补齐发布必填属性："
                        + format_attribute_summary(filled))
    warnings.extend(n for n in build_notes if n not in warnings)
    warnings.extend("发布属性提示：" + n for n in attr_notes)
    still_missing = _missing_attr_names(payload.get("productAttr"), required_list)
    if still_missing:
        warnings.append("发布必填属性仍缺失：" + "、".join(still_missing)
                        + "（保存可继续；发布前建议手工填写）")
    return detail, payload, issues, warnings


# ---------------- 主流程 ----------------
def run_amazon_product(product_id: str, cfg, overrides: dict | None = None,
                       *, dry_run: bool = True, use_ai: bool = False,
                       op: str = OP_SAVE, client=None,
                       confirm_write: bool = False) -> AmazonRunResult:
    """读详情 → 站点/币种 → 类目 →（AI）→ 保存包 → 校验 → 违禁词 → 预演/保存。

    默认 ``dry_run=True``：只预演，绝不调用保存接口。
    真实保存需同时满足：``dry_run=False`` + **显式 ``confirm_write=True``**（防脚本误写）
    + 非 ``forced_preview()`` + ``core.guard.require`` 通过，才走
    ``amazon_publish.save_to_amazon``（唯一写收口，并复用步骤①的 client，不再新建客户端）。
    """
    pid = str(product_id or "").strip()
    op = str(op or OP_SAVE)
    res = AmazonRunResult(product_id=pid, op=op, preview=bool(dry_run))
    try:
        if not pid:
            return _fail(res, "参数校验", "缺少 product_id")
        if op not in OP_LABELS:
            return _fail(res, "参数校验", f"未知操作 op={op}（可选 1/2/3）")
        # 防脚本误写：dry_run=False 必须显式 confirm_write=True 才允许进入真实保存分支
        if not dry_run and not confirm_write:
            msg = ("真实保存需要显式 confirm_write=True（防脚本误写）；"
                   "预演请用 dry_run=True")
            res.ok = False
            res.saved = False
            res.preview = False
            res.error = msg
            _step(res, "写入确认", False, msg)
            return res

        # ① 连接 + 读详情
        try:
            c = client if client is not None else make_client(cfg)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "连接 Amazon", f"创建客户端失败：{_short(exc)}")
        _step(res, "连接 Amazon", True, "Cookie 可用，客户端已创建")
        try:
            detail = c.product_detail(pid)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "读取商品详情", f"读取商品 {pid} 失败：{_short(exc)}")
        product = _product_of(detail)
        if not product:
            return _fail(res, "读取商品详情", f"读不到商品 {pid}（接口返回空）")
        _step(res, "读取商品详情", True,
              f"{_short(product.get('title'), 60)} · 类目 {product.get('categoryId') or '未分类'}")

        # ② 站点 + 币种
        ov = _merge_overrides(overrides)
        mp = str(ov.get("marketplace_id") or product.get("marketPlaceId") or "")
        meta = prompt_site_meta(mp) if mp else {}
        res.site = mp
        res.site_code = _site_code(meta)
        shop_id = str(product.get("shopId") or _cfg_get(cfg, "amazon_shop_id", "") or "")
        currency = str(ov.get("currency") or "").strip()
        if not currency and mp and shop_id:
            try:
                currency = c.currency(shop_id, mp) or ""
            except Exception:
                currency = ""
        res.currency = currency or str(product.get("standardPriceCurrency") or "")
        if mp:
            _step(res, "站点与币种", True,
                  f"站点 {res.site_code or mp} · {meta.get('country') or ''} · "
                  f"{res.currency or '未知币种'} · {meta.get('lang_name') or ''}")
        else:
            _step(res, "站点与币种", False, "商品缺少 marketPlaceId，无法确定站点")
            res.issues.append("marketPlaceId 为空")

        # ③ 类目校验（显式路径时远程校验；否则沿用商品类目）
        category_path = str(ov.get("category_path") or "").strip()
        site_code = res.site_code
        if category_path and site_code:
            try:
                node = c.category_by_path(site_code, category_path)
            except Exception as exc:  # noqa: BLE001
                msg = f"类目路径不属于站点 {site_code} 或校验失败：{_short(exc)}"
                res.issues.append(msg)
                _step(res, "类目校验", False, msg)
            else:
                if node:
                    name = str(node.get("chineseName") or node.get("categoryName") or "")
                    _step(res, "类目校验", True,
                          f"{category_path} → {name or node.get('categoryId') or '已确认'}")
                else:
                    msg = f"类目路径不存在：{category_path}"
                    res.issues.append(msg)
                    _step(res, "类目校验", False, msg)
        else:
            cid = str(ov.get("category_id") or product.get("categoryId") or "").strip()
            _step(res, "类目校验", True,
                  f"沿用商品类目 {cid}" if cid else "未选择类目（保存前校验会拦截）")

        # ④ AI 生成（use_ai；overrides 已有值不覆盖）
        text = _current_text_fields(product, ov)
        ai_notes: list[str] = []
        if use_ai:
            missing = [f for f in AI_FIELDS if not text.get(f)]
            if missing:
                ai_product = _product_for_ai(product, ov, detail)
                generated, ai_notes = ai_generate_all(
                    ai_product, cfg, mp, _bullets_count(cfg, 0))
                res.ai_used = True
                ok_fields = 0
                for name in missing:
                    value = generated.get(name)
                    if name == "bullets":
                        if value:
                            ov["bullets"] = list(value)
                            ok_fields += 1
                    elif value:
                        ov[name] = value
                        ok_fields += 1
            else:
                _step(res, "AI 文案", True, "四项文案均已填写，跳过 AI 生成")
        else:
            _step(res, "AI 文案", True, "未启用 AI，使用当前文案/商品原文")
        if ai_notes:
            failed = [n for n in ai_notes if "失败" in n or "异常" in n]
            for note in ai_notes[:8]:
                res.warnings.append("AI：" + note)
            _step(res, "AI 文案", True if not failed else False,
                  ("；".join(_short(n, 80) for n in ai_notes[:4]))
                  + (f"（{len(failed)} 项失败，已保留手填内容）" if failed else ""))

        # ④b 缺失发布必填属性自动补齐（op=1/3/2 都生效；op=2 额外套同类在线商品模板）
        required_list = _required_from_overrides(ov, cfg)
        if not autofill_enabled(cfg):
            missing_now = _missing_attr_names(
                _existing_attributes(product, ov), required_list)
            detail = "已关闭（amazon_autofill_attrs=False）"
            if missing_now:
                detail += "；需人工：" + "、".join(missing_now)
                res.warnings.append("发布必填属性未自动补齐（开关已关闭）："
                                    + "、".join(missing_now))
            _step(res, "必填属性补齐", True, detail)
        else:
            try:
                category_id = str(ov.get("category_id") or product.get("categoryId") or "")
                template: dict = {}
                if op == OP_PUBLISH and category_id and mp:
                    try:
                        template = c.attribute_template(category_id, mp, shop_id) or {}
                    except Exception:
                        template = {}
                filled, attr_notes = _autofill_attributes_with_notes(
                    product, cfg, mp, ov, required=required_list, template=template)
                res.attribute_notes.extend(attr_notes)
                if filled:
                    attrs = dict(ov.get("attributes") or {})
                    for key, value in filled.items():
                        attrs.setdefault(key, value)
                    ov["attributes"] = attrs
                    detail = "补齐：" + format_attribute_summary(filled, limit=8)
                    res.warnings.append("已自动补齐发布必填属性："
                                        + format_attribute_summary(filled, limit=8))
                else:
                    detail = "必填属性已齐全，无需补齐"
                missing_after = _missing_attr_names(
                    _existing_attributes(product, ov), required_list)
                # 确定性映射仍补不了 → AI 兜底建议（仅 use_ai；逐字段隔离失败，不写平台）
                if missing_after and use_ai:
                    suggested, ai_attr_notes = ai_suggest_attributes(
                        product, cfg, mp, missing_after)
                    res.attribute_notes.extend(ai_attr_notes)
                    res.warnings.extend("AI 属性建议：" + n for n in ai_attr_notes)
                    if suggested:
                        attrs = dict(ov.get("attributes") or {})
                        for key, value in suggested.items():
                            attrs.setdefault(key, value)
                        ov["attributes"] = attrs
                        res.ai_used = True
                        detail += "；AI 建议：" + format_attribute_summary(
                            suggested, limit=8)
                    missing_after = _missing_attr_names(
                        _existing_attributes(product, ov), required_list)
                if attr_notes:
                    res.warnings.extend("发布属性提示：" + n for n in attr_notes)
                if missing_after:
                    detail += "；需人工：" + "、".join(missing_after[:6])
                    res.warnings.append("发布必填属性仍缺失：" + "、".join(missing_after)
                                        + "（保存可继续；发布前建议手工填写）")
                _step(res, "必填属性补齐", True, detail)
            except Exception as exc:  # noqa: BLE001
                msg = f"必填属性补齐异常（不阻断）：{_short(exc, 160)}"
                res.warnings.append(msg)
                _step(res, "必填属性补齐", False, msg)

        # ⑤ 组装保存包
        build_notes: list[str] = []
        try:
            payload = build_amazon_payload(product, cfg, ov, currency=res.currency,
                                           notes=build_notes)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "组装保存包", f"build_amazon_payload 失败：{_short(exc)}")
        if build_notes:
            res.warnings.extend(n for n in build_notes if n not in res.warnings)
        _fill_result(res, payload)
        res.op = op

        # ⑥ 必填/合规校验
        try:
            issues = validate_amazon_payload(payload, product=product,
                                             site_meta=prompt_site_meta(res.site))
            warnings = build_warnings(payload, product=product)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "保存前校验", f"校验异常：{_short(exc)}")
        res.issues.extend(issues)
        # AI 生成的卖点不足 3 条：AI 输出必须 5 条，解析不足要阻断提示
        if use_ai and ov.get("bullets"):
            n_bullets = len([b for b in split_bullets(payload.get("bulletPoint") or "") if b])
            if 0 < n_bullets < 3:
                res.issues.append(f"AI 生成的卖点不足 3 条（{n_bullets} 条），请补齐后再保存")
        res.warnings.extend(warnings)
        if res.issues:
            detail_msg = f"{len(res.issues)} 项未通过：" + "；".join(res.issues[:6])
            _step(res, "保存前校验", False, detail_msg)
            res.ok = False
            res.error = "保存前校验未通过：" + "；".join(res.issues)
            res.preview = bool(dry_run) or forced_preview()
            return res
        _step(res, "保存前校验", True, "必填项/合规项全部通过")

        # ⑦ 违禁词（只读，仅提示）
        try:
            banned = c.check_banned_words(
                title=res.title, description=res.description,
                bullet_point=payload.get("bulletPoint") or "",
                search_terms=res.search_terms)
            if banned:
                res.banned_words = list(banned)
                warn = "违禁词命中：" + "、".join(banned[:8]) + "（建议替换，不阻断）"
                res.warnings.append(warn)
                _step(res, "违禁词检查", True, warn)
            else:
                _step(res, "违禁词检查", True, "未命中")
        except Exception as exc:  # noqa: BLE001
            res.warnings.append(f"违禁词检查失败（不影响预演/保存）：{_short(exc)}")
            _step(res, "违禁词检查", True, "跳过：" + _short(exc))

        # ⑧ 预演
        save_detail = f"op={op}（{OP_LABELS[op]}）· 载荷 {len(payload)} 个字段"
        if dry_run:
            res.preview = True
            res.ok = True
            _step(res, "预演", True, f"{save_detail}；dry-run 未调用保存接口")
            _step(res, "完成", True, "预演完成（未保存商品）")
            return res

        # 真实保存前的双保险：dry_run=False 但命中验收环境 → 明确「拒绝真实保存」
        if forced_preview():
            res.preview = True
            res.saved = False
            res.ok = False
            res.error = ("当前是预演/验收模式（AURORA_NO_SAVE/AURORA_TEST），"
                         "已拒绝真实保存")
            res.warnings.append("检测到 AURORA_NO_SAVE/AURORA_TEST：已拒绝真实保存")
            _step(res, "安全护栏", False, res.error)
            return res

        try:
            from ..core.guard import require as _require_license
            _require_license(cfg)
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "授权校验", f"授权校验未通过：{_short(exc)}")
        _step(res, "授权校验", True, "通过")

        # 唯一写收口：save_to_amazon（内部还会再校验一次 forced_preview/guard/issues）
        try:
            from .amazon_publish import save_to_amazon
            # 复用步骤①创建的 client：注入 fake 时绝不新建真实客户端（防脚本误写事故）
            saved = save_to_amazon(pid, cfg, overrides=ov, op=op, client=c)
        except ValueError as exc:
            return _fail(res, "保存到 Amazon", f"保存前校验未通过：{_short(exc, 300)}")
        except Exception as exc:  # noqa: BLE001
            return _fail(res, "保存到 Amazon", f"保存失败：{_short(exc, 300)}")
        res.save_result = saved.get("result") if isinstance(saved, dict) else {}
        if isinstance(res.save_result, dict):
            code = res.save_result.get("code")
            if code not in (0, "0"):
                msg = str(res.save_result.get("msg") or "")
                return _fail(res, "保存到 Amazon",
                             f"保存失败：{msg or f'code={code}'}")
        res.saved = True
        res.ok = True
        msg = ""
        if isinstance(res.save_result, dict):
            data = res.save_result.get("data")
            if isinstance(data, dict):
                msg = str(data.get("msg") or "")
            msg = msg or str(res.save_result.get("msg") or "")
        _step(res, "保存到 Amazon", True,
              f"{OP_LABELS[op]}成功" + (f"：{_short(msg, 110)}" if msg else ""))

        # op=2：保存成功后轮询核实发布结果（亚马逊类目校验通过后才算发布成功）
        if op == OP_PUBLISH:
            try:
                wait = _cfg_get(cfg, "amazon_publish_wait", 60)
                try:
                    wait = int(wait if wait is not None else 60)
                except Exception:
                    wait = 60
                wait = max(0, min(wait, 600))
                verified = verify_publish(pid, cfg, client=c, wait_seconds=wait)
                res.publish_state = str(verified.get("state") or "")
                res.publish_asin = str(verified.get("asin") or "")
                res.publish_detail = str(verified.get("detail") or "")
                res.required_attrs = list(verified.get("required_attrs") or [])
                status = str(verified.get("status") or "unknown")
                if status == "failed":
                    res.publish_error = str(verified.get("err_msg") or "")
                    res.ok = False
                    res.error = "发布失败：" + (res.publish_error or res.publish_detail)
                    if res.required_attrs:
                        res.error += "；缺失必填属性：" + ", ".join(res.required_attrs)
                        note = "缺失必填属性：" + ", ".join(res.required_attrs)
                        if note not in res.attribute_notes:
                            res.attribute_notes.append(note)
                        res.warnings.append(note + "（可修复后一键重发）")
                elif status in ("not_submitted", "unknown"):
                    res.ok = False
                    res.error = "发布结果核实失败：" + res.publish_detail
                elif status == "reviewing":
                    res.warnings.append("平台审核中（非软件失败）：" + res.publish_detail)
                elif status == "processing":
                    res.warnings.append("已提交，平台处理中（可稍后再核实）")
                _step(res, "发布结果核实",
                      status in ("online", "processing", "reviewing"),
                      res.publish_detail
                      + (f" · 缺失属性：{', '.join(res.required_attrs)}"
                         if res.required_attrs else ""))
            except Exception as exc:  # noqa: BLE001
                msg = f"发布结果核实异常（保存已成功）：{_short(exc, 160)}"
                res.warnings.append(msg)
                _step(res, "发布结果核实", False, msg)

        _step(res, "完成", True, "商品已保存/提交")
        return res
    except Exception as exc:  # noqa: BLE001
        return _fail(res, "流水线异常", f"{type(exc).__name__}: {_short(exc, 200)}")


__all__ = [
    "ENV_NO_SAVE", "ENV_TEST", "OP_SAVE", "OP_MOVE_PENDING", "OP_PUBLISH",
    "OP_LABELS", "AI_FIELDS", "AI_LABELS",
    "AI_ATTRIBUTE_SKIP", "AI_ATTRIBUTE_MAX_FIELDS",
    "AmazonStep", "AmazonRunResult", "forced_preview", "prepare_save",
    "run_amazon_product", "ai_generate_field", "ai_generate_all",
    "autofill_attributes", "ai_suggest_attributes", "verify_publish",
    "required_attributes_from_schema",
]

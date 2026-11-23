"""Explicit per-field AI generation for batch automation, preserving unselected data."""
from copy import deepcopy
import json
import re

from .progress import emit
from .platform_presets import ai_fields


SKU_PROMPT = "根据商品真实标题与原SKU生成简短ASCII编码前缀，仅包含英文字母、数字、横线或下划线，最多20字符。不编造型号或认证。只输出编码前缀，不要解释。系统会加商品ID以避免批量重复。"
ATTRIBUTE_PROMPT = "根据当前商品原资料选择类目属性。只输出JSON对象：属性名到{value:原文值,source:原文中逐字出现的证据短语}。没有直接证据的属性不要输出。不得猜测认证、产地、危险品、品牌、重量、尺寸、识别码、价格或库存。已有值不要改。枚举值必须来自候选列表。"


def generate_copy(platform, product, cfg, overrides):
    ov = deepcopy(overrides)
    prefix = platform + "_collect_"
    selected = json.loads(getattr(cfg, prefix + "ai_fields", "[]") or "[]")
    if not isinstance(selected, list) or any(k not in ai_fields(platform) for k in selected):
        raise ValueError("文案处理字段无效，请重新选择")
    rewrite = getattr(cfg, prefix + "ai_mode", "missing") == "rewrite"
    fields = {"alibaba": {"title": ("subject", "subject"), "description": ("textDesc", "text_desc"), "keywords": ("keywords", "keywords")},
              "amazon": {"title": ("title", "title"), "bullets": ("bulletPoint", "bullets"), "description": ("description", "description"), "search_terms": ("searchTerms", "search_terms")},
              "tiktok": {"title": ("productName", "title"), "description": ("description", "description"), "key_features": ("keyProductFeatures", "key_features"), "search_terms": ("searchTerms", "search_terms")},
              "shopee": {"title": ("name" if getattr(cfg, "shopee_mode", "global") == "site" else "itemName", "name" if getattr(cfg, "shopee_mode", "global") == "site" else "itemName"), "description": ("description", "description")}}[platform]
    if platform == "tiktok":
        from .tiktok_prompts import is_us_site
        if not is_us_site(product.get("site") or getattr(cfg, "tiktok_site", "")):
            selected = [k for k in selected if k in ("title", "description")]
    pid = str(product.get("idStr") or product.get("id") or "")
    for kind in selected:
        native, key = fields[kind]
        if platform == "shopee" and kind == "description" and ov.get("descriptionType", product.get("descriptionType")) == "extended":
            native = key = "descriptionInfoStr"
        existing = ov.get(key, product.get(native))
        if key in ov:
            continue
        if existing and not rewrite:
            # AI 开关已开，但“补齐缺失”模式下已有文案不会调用 AI；必须明确告知，
            # 否则日志会看起来像 AI 开关没生效。
            emit(platform + " · AI 文案", "跳过",
                 f"{kind} 已有内容；当前为补齐缺失模式，不调用 AI（如需重写请切换“重写所选字段”）",
                 "WARN", product=pid)
            continue
        emit(platform + " · AI 文案", "开始", kind + ("重写" if rewrite else "补齐"), product=pid)
        from .collect_budget import paid
        def generate():
            return _generate_field(platform, product, cfg, kind)
        value, notes = paid(cfg, platform, "text:" + kind, product, {}, generate)
        if platform == "shopee" and kind == "description" and key == "descriptionInfoStr":
            import html
            value = "<p>" + html.escape(value).replace("\n", "<br>") + "</p>"
        if kind == "description" and (platform == "tiktok" or key == "descriptionInfoStr"):
            from .platform_media import keep_description_images
            value = keep_description_images(value, existing)
        if not value:
            raise ValueError(f"{kind} AI生成失败，原内容保留，本件未提交：" + "；".join(notes))
        if kind == "bullets" and (not isinstance(value, list) or len(value) != 5):
            raise ValueError("AI五点必须一次生成完整5条，当前输出不完整，本件未提交")
        ov[key] = value
        emit(platform + " · AI 文案", "完成", kind, product=pid)
    return ov


def _generate_field(platform, product, cfg, kind):
        if platform == "amazon":
            from .amazon_pipeline import ai_generate_field
            value, notes = ai_generate_field(product, cfg, kind, product.get("marketPlaceId") or "", count=5)
        elif platform == "tiktok":
            from .tiktok_pipeline import ai_generate_field
            value, notes = ai_generate_field(product, cfg, kind, product.get("site") or cfg.tiktok_site, count=5)
        elif platform == "shopee":
            from .shopee import generate_copy as generate
            value, notes = generate(product, cfg, kind, site=cfg.shopee_site or "global"), []
        else:
            from ..config import AppConfig
            from .text_pipeline import TextPipeline
            data = cfg.as_dict()
            data["detail_prompt"] = cfg.alibaba_detail_prompt
            pipeline = TextPipeline(AppConfig(data))
            if kind == "title":
                result = pipeline.make_alibaba_title(str(product.get("subject") or ""), prompt=cfg.alibaba_title_prompt)
            elif kind == "description":
                result = pipeline.make_detail(str(product.get("subject") or ""), {}, source_text=product.get("textDesc") or "")
            else:
                result = pipeline.make_keywords(str(product.get("subject") or ""), str(product.get("textDesc") or ""))
            value, notes = (result.value if result.ok else ""), [getattr(result, "error", "")]
        if not value:
            raise ValueError(f"{kind} AI生成失败：" + "；".join(notes))
        if kind == "bullets" and (not isinstance(value, list) or len(value) != 5):
            raise ValueError("AI五点必须一次生成完整5条，当前输出不完整，本件未提交")
        return value, notes


def generate_sku(platform, product, cfg, overrides):
    """Generate a missing parent code only; existing variant identities remain intact."""
    alibaba_skus = None
    if platform == "alibaba":
        raw = product.get("productSku") or "{}"
        alibaba_skus = json.loads(raw) if isinstance(raw, str) else deepcopy(raw)
        rows = alibaba_skus.get("special_skus") or [] if isinstance(alibaba_skus, dict) else []
        if not rows or not any(isinstance(row, dict) and not (row.get("sku_code") or row.get("code")) for row in rows):
            return
    native, key = {"amazon": ("sku", "sku"), "tiktok": ("sellerSku", "seller_sku"),
                   "shopee": ("itemSku", "itemSku"), "alibaba": ("", "product_sku")}.get(platform, ("", ""))
    if not key or product.get(native) or overrides.get(key):
        return
    if platform == "tiktok" and any(row.get("sellerSku") for row in product.get("variationList", []) if isinstance(row, dict)):
        return  # A generated parent code must not rename an existing child SKU.
    code = generate_sku_code(platform, product, cfg, prefix_only=platform == "alibaba")
    pid = str(product.get("idStr") or product.get("id") or "")
    if platform == "alibaba":
        for index, row in enumerate(rows, 1):
            if isinstance(row, dict) and not (row.get("sku_code") or row.get("code")):
                row["sku_code"] = _format_sku(code, pid, index)
        overrides[key] = alibaba_skus
    else:
        overrides[key] = code
    emit(platform + " · AI SKU", "完成", "已补齐所选 SKU 编码", product=pid)


def _format_sku(prefix, pid, index=None):
    product = re.sub(r"[^A-Za-z0-9_-]", "", pid)[-18:]
    if not product:
        raise ValueError("缺少有效商品 ID")
    return f"{prefix[:16]}-{product}-{index}" if index is not None else f"{prefix[:20]}-{product}"


def generate_sku_code(platform, product, cfg, *, index=None, prefix_only=False):
    """Use the same SKU naming rule for batch completion and selected item fields."""
    from ..api import ApiClient
    from . import model_of
    from .collect_budget import paid
    prompt = str(getattr(cfg, platform + "_collect_sku_prompt", "") or SKU_PROMPT)
    api = ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)
    title = next((str(product[k]) for k in ("title", "subject", "productName", "itemName", "name") if product.get(k)), "")
    facts = title + ("\n当前规格：" + str(product["selected_variant"]) if product.get("selected_variant") else "")
    result = paid(cfg, platform, "sku", product, prompt, lambda: api.chat(
        model_of(cfg, "text"), [{"role": "system", "content": prompt}, {"role": "user", "content": facts}], max_tokens=100))
    code = re.sub(r"[^A-Za-z0-9_-]", "", str(result or "").strip())[:20]
    pid = str(product.get("idStr") or product.get("id") or "")
    if not code or not pid:
        raise ValueError("AI SKU输出无效")
    return code if prefix_only else _format_sku(code, pid, index)


def _attribute_matches(row, name, definition_id):
    return (str(row.get("attribute_name") or row.get("name") or row.get("attributeName") or "").casefold() == name.casefold()
            or bool(definition_id and str(row.get("attributeId") or row.get("attribute_id") or "") == definition_id))


def _attribute_has_value(row):
    for key in ("value_name", "valueName", "original_value_name", "value", "value_id", "valueId"):
        value = row.get(key)
        if value not in (None, [], {}) and str(value).strip() not in ("", "-1"):
            return True
    return any(_attribute_has_value(value) for key in ("values", "attribute_value_list")
               for value in (row.get(key) or []) if isinstance(value, dict))


def suggest_attributes(platform, product, cfg, client, overrides=None):
    """One bounded AI call, only accept verbatim product evidence and legal enums."""
    from ..api import ApiClient
    from . import model_of
    from .amazon_payload import parse_product_attr, attribute_present
    from .shopee import json_value
    ov = overrides or {}
    category = ov.get("categoryId" if platform == "shopee" else "category_id", product.get("categoryId"))
    changed_category = str(category or "") != str(product.get("categoryId") or "")
    if not category:
        return {}
    if platform == "alibaba":
        definitions = client.required_attributes(category)
        existing = [] if changed_category else json_value(product.get("attributes"), [])
    elif platform == "amazon":
        from .amazon_pipeline import required_attributes_from_schema
        from .amazon_prompts import site_meta
        names = required_attributes_from_schema(str(category), "" if changed_category else str(product.get("productType") or ""),
            site_meta(product.get("marketPlaceId") or "").get("code", ""), client=client)
        existing = {} if changed_category else parse_product_attr(product.get("productAttr"))
        definitions = [{"name": name} for name in names if not attribute_present(existing, name)]
    elif platform == "tiktok":
        definitions = client.category_attributes(str(product.get("site") or cfg.tiktok_site), str(category))
        existing = [] if changed_category else json_value(product.get("productAttributes"), [])
    else:
        definitions = client.attributes(category, product.get("shopId") or cfg.shopee_shop_id)
        existing = [] if changed_category else json_value(product.get("attributes"), [])
    if isinstance(definitions, dict):
        definitions = definitions.get("attributeList") or definitions.get("attributes") or []
    if not isinstance(definitions, list):
        return {}
    source = json.dumps({k: v for k, v in product.items() if k in
        ("subject", "title", "productName", "itemName", "name", "textDesc", "description", "descriptionInfoStr", "attributes", "productAttributes", "productAttr")}, ensure_ascii=False)
    explicit = ov.get("product_attributes" if platform == "tiktok" else "attributes", {} if platform in ("alibaba", "amazon") else [])
    if isinstance(explicit, str):
        explicit = json.loads(explicit)
    targets = []
    for definition in definitions:
        name = str(definition.get("nameEn") or definition.get("attributeName") or definition.get("name") or "")
        if not name or any(word in name.lower() for word in
                ("certif", "origin", "brand", "weight", "dimension", "hazard", "identifier", "price", "stock", "battery", "contact", "voltage")):
            continue
        definition_id = str(definition.get("attributeId") or definition.get("attribute_id") or definition.get("attrId") or definition.get("id") or "")
        if isinstance(explicit, dict) and any(str(key).casefold() == name.casefold() for key in explicit):
            continue
        if isinstance(explicit, list) and any(_attribute_matches(row, name, definition_id) for row in explicit if isinstance(row, dict)):
            continue
        if isinstance(existing, list) and any(_attribute_matches(row, name, definition_id) and _attribute_has_value(row)
                                             for row in existing if isinstance(row, dict)):
            continue
        targets.append(definition)
    targets = targets[:6]
    if not targets:
        return {}
    api = ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)
    prompt = str(getattr(cfg, platform + "_collect_attribute_prompt", "") or ATTRIBUTE_PROMPT)
    from .collect_budget import paid
    raw = paid(cfg, platform, "attributes", product, targets, lambda: api.chat(model_of(cfg, "text"), [{"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps({"product": source[:12000], "attributes": targets}, ensure_ascii=False)}], max_tokens=1200))
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip())
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("AI属性返回结构无效，本件未提交")
    accepted = {}
    for definition in targets:
        name = str(definition.get("nameEn") or definition.get("attributeName") or definition.get("name") or "")
        suggestion = data.get(name)
        if not isinstance(suggestion, dict):
            continue
        value, evidence = str(suggestion.get("value") or "").strip(), str(suggestion.get("source") or "").strip()
        if not value or not evidence or evidence.casefold() not in source.casefold() or value.casefold() not in evidence.casefold():
            continue
        candidates = definition.get("values") or definition.get("attributeValueList") or definition.get("valueList") or []
        if candidates and not any(value.casefold() == str(row.get("name_en") or row.get("valueName") or row.get("name") or row.get("originalValueName") or "").casefold() for row in candidates if isinstance(row, dict)):
            continue
        accepted[name] = dict(value=value, definition=definition)
    return accepted


def apply_attributes(platform, product, cfg, client, overrides):
    from .shopee import json_value
    accepted = suggest_attributes(platform, product, cfg, client, overrides)
    if not accepted:
        return
    if platform in ("amazon", "alibaba"):
        explicit = overrides.setdefault("attributes", {})
        for name, suggestion in accepted.items():
            explicit.setdefault(name, suggestion["value"])
    else:
        key = "product_attributes" if platform == "tiktok" else "attributes"
        original_key = "productAttributes" if platform == "tiktok" else "attributes"
        category = overrides.get("categoryId" if platform == "shopee" else "category_id", product.get("categoryId"))
        changed_category = str(category or "") != str(product.get("categoryId") or "")
        current = json_value(overrides.get(key, [] if changed_category else product.get(original_key)), [])
        for name, suggestion in accepted.items():
            definition, value = suggestion["definition"], suggestion["value"]
            aid = definition.get("attributeId") or definition.get("attribute_id") or definition.get("id")
            candidates = definition.get("attributeValueList") or definition.get("values") or definition.get("valueList") or []
            match = next((row for row in candidates if isinstance(row, dict) and value.casefold() == str(
                row.get("name_en") or row.get("valueName") or row.get("name") or row.get("originalValueName") or "").casefold()), None)
            vid = (match or {}).get("valueId") or (match or {}).get("value_id") or (match or {}).get("id")
            # Unknown native IDs are left for explicit editing, never synthesized.
            if not aid or not vid:
                continue
            if platform == "tiktok":
                entry = dict(attributeId=str(aid), attributeName=name, values=[dict(valueId=str(vid), valueName=value)])
            else:
                entry = dict(attribute_id=aid, attribute_value_list=[dict(value_id=vid, original_value_name=value)])
            position = next((index for index, row in enumerate(current) if isinstance(row, dict)
                             and _attribute_matches(row, name, str(aid))), None)
            if position is None:
                current.append(entry)
            else:
                current[position] = dict(current[position], **entry)
        overrides[key] = current
    emit(platform + " · AI 属性", "完成", "仅采用带原文证据且匹配平台候选的建议", product=str(product.get("idStr") or product.get("id") or ""))


def prepare(platform, pid, cfg, client, overrides):
    prefix = platform + "_collect_"
    enabled = any(bool(getattr(cfg, prefix + suffix, False)) for suffix in ("ai", "ai_sku", "ai_attributes"))
    if not enabled:
        return overrides
    detail = client.product_detail(pid)
    from .collect_runtime import verified_product
    product = verified_product(detail, pid)
    ov = deepcopy(overrides)
    if bool(getattr(cfg, prefix + "ai", False)):
        ov = generate_copy(platform, product, cfg, ov)
    if bool(getattr(cfg, prefix + "ai_sku", False)):
        generate_sku(platform, product, cfg, ov)
    if bool(getattr(cfg, prefix + "ai_attributes", False)):
        apply_attributes(platform, product, cfg, client, ov)
    return ov

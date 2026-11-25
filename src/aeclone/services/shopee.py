"""Shopee 编辑、校验与保存。保留原始字段和变种身份；不调用速卖通管线。"""
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import os
import re
from urllib.parse import urlparse

from ..integrations.shopee import make_client
from .progress import emit

DEFAULT_LIMITS = dict(nameMin=15, nameMax=180, descMin=20, descMax=5000,
    imgCountMin=1, imgCountMax=9, priceMin=0.01, priceMax=100000, stockMin=0, stockMax=500000)
LANGUAGES = {"global": "英语", "SG": "英语", "MY": "英语", "PH": "英语", "TW": "繁体中文",
    "TH": "泰语", "VN": "越南语", "ID": "印尼语", "BR": "葡萄牙语", "MX": "西班牙语"}
DEFAULT_PROMPTS = {
    "title": (
        "你是 Shopee 跨境电商资深 listing 运营专家。请根据商品原始信息，"
        "为 {language} 站点写一个可直接上架的商品标题。\n"
        "硬性要求：\n"
        "1) 使用 {language}（站点语言），非中文站点禁止出现任何中文字符；\n"
        "2) 长度 ≤ 120 字符（Shopee 建议限额，超过按词边界截断）；\n"
        "3) 结构：核心产品词 + 品牌/材质 + 关键规格/型号 + 核心卖点或适用场景；"
        "关键词自然融入，不堆砌，不重复；\n"
        "4) 保留原文真实型号与规格（尺寸/材质/功率/数量/颜色），"
        "不得编造认证、参数、产地；\n"
        "5) 禁止 emoji、HTML 标签、引号、联系方式（邮箱/电话/网址/WhatsApp）、"
        "促销词（best/No.1/free shipping/cheapest/guarantee 等）与竞品品牌；\n"
        "6) 只输出一行标题，不要解释、不要编号、不要 Markdown。"
    ),
    "description": (
        "你是 Shopee 商品文案专家。请为 {language} 站点商品写产品描述。\n"
        "硬性要求：\n"
        "1) 使用 {language}（站点语言）；非中文站点禁止出现中文字符；\n"
        "2) 输出纯文本（不含 HTML 标签）；\n"
        "3) 内容结构：\n"
        "   · 开头 1~2 句：产品核心卖点 + 适用场景\n"
        "   · 主要参数列表（只写原文有的数据）\n"
        "   · 使用方法/安装说明（如有）\n"
        "   · 包装内容 / 售后说明\n"
        "4) 语言自然流畅，不堆砌关键词，不写绝对化表述；\n"
        "5) 禁止联系方式、网址、二维码、促销返现、医疗功效声称、编造认证与参数；\n"
        "6) 只输出描述正文，不要解释或标题。"
    ),
}


def json_value(value, default):
    if isinstance(value, type(default)):
        return deepcopy(value)
    try:
        parsed = json.loads(value or "null")
        return parsed if isinstance(parsed, type(default)) else deepcopy(default)
    except (ValueError, TypeError):
        return deepcopy(default)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def variation_rows(product, mode="global"):
    for key in (("variations", "variationsStr") if mode == "site" else ("variationList", "variationListStr")):
        rows = json_value(product.get(key), [])
        if rows:
            return rows
    return []


def images(product, mode="global"):
    delimiter = ";" if mode == "site" else "|"
    return [x.strip() for x in str(product.get("mainImage") or "").split(delimiter) if x.strip()]


def build_payload(product, overrides=None, *, mode="global", op="1"):
    if not isinstance(product, dict) or not (product.get("idStr") or product.get("id")):
        raise ValueError("请先载入已有的 Shopee 商品")
    out = deepcopy(product)
    ov = deepcopy(overrides or {})
    # ID 一律使用服务端字符串，避免大整数经浮点数转换后损坏。
    out["id"] = str(product.get("idStr") or product["id"])
    out["op"] = str(op)
    allowed = {"shopId", "categoryId", "itemName", "name", "itemSku", "description",
        "descriptionInfoStr", "descriptionType", "originalPrice", "price", "normalStock", "stock",
        "weight", "packageLength", "packageWidth", "packageHeight", "condition", "daysToShip",
        "isPreOrder", "sourceUrl", "attributes", "brandInfo", "logisticsInfo", "logistics",
        "videoUrl", "videoThumbnail", "videoUploadId", "dxmVideoId", "sizeChart", "fullCid"}
    out.update({k: v for k, v in ov.items() if k in allowed})
    if "images" in ov:
        urls = list(ov["images"])
        out["mainImage"] = (";" if mode == "site" else "|").join(urls)
        out["extraImages" if mode == "site" else "draftImgUrl"] = "|".join(urls)
    if mode == "global" and "normalStock" in ov:
        # 单仓使用编辑器约定的 CNZ；多仓必须逐仓编辑，不能改总量而覆盖分配。
        stock = number(ov["normalStock"])
        sellers = json_value(out.get("sellerStock"), [])
        if len(sellers) > 1 and stock != sum(number(x.get("stock")) or 0 for x in sellers):
            raise ValueError("多仓商品请逐仓修改库存，不能直接覆盖总库存")
        if len(sellers) <= 1 and stock is not None:
            out["sellerStock"] = json.dumps([dict(location_id=sellers[0].get("location_id", "CNZ") if sellers else "CNZ", stock=stock)])
    rows = variation_rows(out, mode)
    edits = ov.get("variant_edits") or {}
    if edits:
        seen = set()
        for row in rows:
            key = str(row.get("idStr") or row.get("id") or row.get("modelIdStr") or row.get("modelId") or "")
            if key in edits:
                if key in seen:
                    raise ValueError("变种 ID 重复，已阻止不明确的修改")
                seen.add(key)
                permitted = {"modelSku", "variationSku", "originalPrice", "price", "normalStock", "stock"}
                from .platform_media import SKU_IMAGE_KEYS, is_image_url
                permitted.update(k for k in SKU_IMAGE_KEYS if k in row and is_image_url(edits[key].get(k)))
                row.update({k: v for k, v in edits[key].items() if k in permitted})
                if mode == "global" and "normalStock" in edits[key]:
                    stocks = json_value(row.get("sellerStock"), [])
                    quantity = number(edits[key]["normalStock"])
                    if len(stocks) > 1 and quantity != sum(number(x.get("stock")) or 0 for x in stocks):
                        raise ValueError("多仓变种请在店小秘逐仓修改库存后重新读取")
                    if len(stocks) <= 1 and quantity is not None:
                        location = stocks[0].get("location_id", "CNZ") if stocks else "CNZ"
                        row["sellerStock"] = json.dumps([dict(location_id=location, stock=quantity)])
        if seen != set(edits):
            raise ValueError("部分变种已变更，请重新读取商品后编辑")
    for row in rows:
        if row.get("idStr"):
            row["id"] = str(row["idStr"])
        if row.get("modelIdStr"):
            row["modelId"] = str(row["modelIdStr"])
    if rows:
        out["variationsStr" if mode == "site" else "variationListStr"] = json.dumps(rows, ensure_ascii=False)
        # 校验和提交必须看到同一份变种，不能读到原详情中的旧行。
        out["variations" if mode == "site" else "variationList"] = rows
    for key in ("attributes", "brandInfo", "logisticsInfo", "sellerStock"):
        if key in out and isinstance(out[key], (dict, list)):
            out[key] = json.dumps(out[key], ensure_ascii=False)
    return out


def validate(payload, *, mode="global", limits=None, required_attrs=None):
    from .platform_media import GROUPS, has_inline_media, html_images

    lim = {**DEFAULT_LIMITS, **(limits or {})}
    issues = []
    if not str(payload.get("shopId") or "").strip() or str(payload.get("shopId")) == "-1":
        issues.append("连接与店铺：请选择已授权的 Shopee 店铺")
    if not str(payload.get("categoryId") or "").strip():
        issues.append("类目与属性：请选择末级类目")
    title = str(payload.get("name" if mode == "site" else "itemName") or "")
    if not int(lim["nameMin"]) <= len(title) <= int(lim["nameMax"]):
        issues.append(f"AI 文案：标题需 {lim['nameMin']}–{lim['nameMax']} 字符")
    extended = payload.get("descriptionType") == "extended"
    description = str(payload.get("descriptionInfoStr" if extended else "description") or "")
    detail_images = html_images(description) if extended else []
    if extended:
        max_images = number(lim.get("descImageNumMax")) or GROUPS["shopee"]["detail"].limit
        if len(detail_images) > max_images:
            issues.append(f"商品描述：当前站点的新版描述最多支持 {int(max_images)} 张图片")
        if len(re.findall(r"<img\b", description, re.I)) != len(detail_images):
            issues.append("商品描述：详情图片必须使用有效的 HTTP/HTTPS 地址")
        description = re.sub(r"<[^>]*>", "", description)
    elif has_inline_media(description):
        issues.append("Shopee 普通描述仅支持文字，请将图片移到商品图片栏")
    if len(description) > int(lim["descMax"]) or (len(description) < int(lim["descMin"]) and not detail_images):
        issues.append(f"AI 文案：描述需 {lim['descMin']}–{lim['descMax']} 字符")
    urls = images(payload, mode)
    if not int(lim["imgCountMin"]) <= len(urls) <= int(lim["imgCountMax"]):
        issues.append(f"商品图片：需 {lim['imgCountMin']}–{lim['imgCountMax']} 张图片")
    if any(urlparse(u).scheme not in ("http", "https") or not urlparse(u).netloc for u in urls):
        issues.append("商品图片：图片必须使用有效的 HTTP/HTTPS 地址")
    rows = variation_rows(payload, mode)
    _seen_ids = set()
    for _row in rows if isinstance(rows, list) else []:
        if not isinstance(_row, dict):
            continue
        _key = str(_row.get("idStr") or _row.get("id") or _row.get("modelIdStr") or _row.get("modelId") or "")
        if not _key:
            continue
        if _key in _seen_ids:
            issues.append("SKU 与库存：变种 ID 重复，平台会拒绝；请合并重复规格")
            break
        _seen_ids.add(_key)
    price_key, stock_key = ("price", "stock") if mode == "site" else ("originalPrice", "normalStock")
    for index, row in enumerate(rows or [payload], 1):
        amount, stock = number(row.get(price_key)), number(row.get(stock_key))
        if amount is None or not float(lim["priceMin"]) <= amount <= float(lim["priceMax"]):
            issues.append(f"价格与物流：第 {index} 项价格为空或超出站点限额")
        if stock is None or stock != int(stock) or not float(lim["stockMin"]) <= stock <= float(lim["stockMax"]):
            issues.append(f"SKU 与库存：第 {index} 项库存必须是 {lim['stockMin']}–{lim['stockMax']} 的整数")
    weight = number(payload.get("weight"))
    if weight is None or weight <= 0:
        issues.append("价格与物流：请填写大于 0 的包装重量")
    for native, label in (("packageLength", "长"), ("packageWidth", "宽"), ("packageHeight", "高")):
        if payload.get(native) not in (None, ""):
            value = number(payload[native])
            if value is None or value <= 0:
                issues.append(f"价格与物流：包装{label}必须大于 0")
    if payload.get("daysToShip") not in (None, ""):
        value = number(payload["daysToShip"])
        if value is None or value < 1 or value != int(value):
            issues.append("价格与物流：备货天数必须是正整数")
    present = {str(x.get("attribute_id") or x.get("attributeId")): x
               for x in json_value(payload.get("attributes"), []) if isinstance(x, dict)}
    for definition in required_attrs or []:
        if not isinstance(definition, dict):
            continue
        required = definition.get("isMandatory") or definition.get("mandatory") or definition.get("required")
        if required in (True, 1, "1", "true"):
            key = str(definition.get("attributeId") or definition.get("attribute_id") or definition.get("id"))
            row = present.get(key) or {}
            if not (row.get("attribute_value_list") or row.get("values") or row.get("value")):
                issues.append("类目与属性：缺少 " + str(definition.get("attributeName") or definition.get("name") or key))
    return issues


@dataclass
class SaveResult:
    ok: bool = False
    saved: bool = False
    issues: list = field(default_factory=list)
    message: str = ""
    state: str = ""




def _attribute_rows(value) -> list[dict]:
    """Shopee 属性候选统一成 list[dict]；字符串 JSON/异常值安全降级。"""
    if isinstance(value, dict):
        for key in ("attributeValueList", "valueList", "values", "list"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []

def autofill_required_attributes(product, payload, definitions, cfg=None):
    """发布前按类目必填属性补空值：原商品值 → 候选枚举 → 基础文本。"""
    current = json_value(payload.get("attributes"), [])
    current = [dict(x) for x in current if isinstance(x, dict)]
    present = {str(x.get("attribute_id") or x.get("attributeId")) for x in current}
    filled = []
    source_attrs = json_value((product or {}).get("attributes"), [])
    source_map = {str(x.get("attribute_id") or x.get("attributeId")): x
                  for x in source_attrs if isinstance(x, dict)}
    title = str((product or {}).get("itemName") or (product or {}).get("name") or "")
    for definition in definitions or []:
        if not isinstance(definition, dict):
            continue
        required = definition.get("isMandatory") or definition.get("mandatory") or definition.get("required")
        if required not in (True, 1, "1", "true"):
            continue
        key = str(definition.get("attributeId") or definition.get("attribute_id") or definition.get("id") or "")
        if not key or key in present:
            continue
        source = source_map.get(key) or {}
        values = source.get("attribute_value_list") or source.get("values") or []
        entry = None
        if values:
            entry = {"attribute_id": key, "attribute_value_list": list(values)}
        else:
            choices = _attribute_rows(definition.get("attributeValueList")
                                      or definition.get("valueList")
                                      or definition.get("values") or [])
            if choices:
                selected = choices[0]
                entry = {"attribute_id": key, "attribute_value_list": [{
                    "value_id": selected.get("valueId", selected.get("value_id", selected.get("id", 0))),
                    "original_value_name": selected.get("originalValueName")
                    or selected.get("valueName") or selected.get("name") or ""}]}
            else:
                label = str(definition.get("attributeName") or definition.get("name") or key)
                entry = {"attribute_id": key, "attribute_value_list": [{
                    "value_id": 0, "original_value_name": (title or label)[:120]}]}
        if entry:
            current.append(entry)
            present.add(key)
            filled.append(key)
    payload["attributes"] = deepcopy(current)
    return payload, filled


def prepare(product_id, cfg, overrides=None, *, client=None):
    c = client or make_client(cfg)
    emit("Shopee · 读取商品", "开始", product=str(product_id))
    detail = c.product_detail(product_id)
    mode = getattr(cfg, "shopee_mode", "global")
    payload = build_payload(detail["product"], overrides, mode=mode)
    site = str(getattr(cfg, "shopee_site", "global") or "global")
    limits = c.limits(site)
    attrs = c.attributes(payload.get("categoryId"), payload.get("shopId")) if payload.get("categoryId") else []
    if isinstance(attrs, dict):
        attrs = attrs.get("attributeList") or attrs.get("attributes") or []
    # 所有类目先读取必填属性并补全，再做发布前校验。
    payload, _filled = autofill_required_attributes(detail.get("product") or {}, payload, attrs, cfg)
    if _filled:
        emit("Shopee · 属性补全", "完成", f"补齐 {len(_filled)} 项必填属性",
             product=str(product_id))
    issues = validate(payload, mode=mode, limits=limits, required_attrs=attrs)
    emit("Shopee · 发布前检查", "完成", "；".join(issues) or "检查通过",
         level="WARN" if issues else "OK", product=str(product_id))
    return payload, issues


def save(product_id, cfg, overrides=None, *, op="1", client=None, before_submit=None):
    result = SaveResult()
    try:
        if str(op) not in ("1", "2", "3"):
            raise ValueError("不支持的保存动作")
        if os.environ.get("AURORA_TEST") or os.environ.get("AURORA_NO_SAVE"):
            raise ValueError("当前为本地验收环境，已阻止真实提交")
        from ..core.guard import require
        require(cfg)
        c = client or make_client(cfg)
        payload, issues = prepare(product_id, cfg, overrides, client=c)
        result.issues = issues
        if issues:
            raise ValueError("请先修正发布前检查中的缺项")
        from .image_preflight import preflight_field
        _sep = ";" if str(getattr(cfg, "shopee_mode", "global")) == "site" else "|"
        _img_errors = preflight_field(payload, "mainImage", _sep, client=c)
        if _img_errors:
            raise ValueError("；".join(_img_errors))
        emit("Shopee · 保存", "开始", "提交平台", product=str(product_id))
        if before_submit is not None:
            before_submit()
        c.save_product(payload, op=op)
        result.saved = result.ok = True
        result.message = "已保存草稿" if op == "1" else "已提交，正在回读平台状态"
        emit("Shopee · 保存", "已接受", result.message, level="OK", product=str(product_id))
        try:
            latest = c.product_detail(product_id)["product"]
            result.state = str(latest.get("dxmOfflineState") or latest.get("productStatusType")
                                or latest.get("dxmState") or "")
            error = str(latest.get("errMsg") or "")
            if str(op) != "1":
                from .publish_outcome import judge
                _out = judge("publish" if str(op) == "2" else "save_to_waiting",
                             result.state, error)
                result.ok = bool(_out.get("ok"))
                result.message = ("已确认：" + str(_out.get("label") or result.state)
                                  if result.ok else
                                  "未达成目标状态：" + str(_out.get("label") or result.state)
                                  + (("：" + error) if error else ""))
            elif error or result.state == "publishFail":
                result.ok = False
                result.message = "已保存，平台处理失败：" + (error or "平台未返回详细原因，请在店小秘查看")
            emit("Shopee · 状态回读", "完成", result.message,
                 level="OK" if result.ok else "ERROR", product=str(product_id))
        except Exception as error:
            result.message += "；回读失败，请刷新商品状态：" + str(error)
            emit("Shopee · 状态回读", "失败", str(error), level="WARN", product=str(product_id))
    except Exception as error:
        result.message = str(error)
        emit("Shopee · 保存", "失败", result.message, level="ERROR", product=str(product_id))
    return result


def generate_copy(product, cfg, kind, *, site="global", client=None):
    if kind not in DEFAULT_PROMPTS:
        raise ValueError("未知文案类型")
    from ..api import ApiClient
    from . import model_of
    api = client or ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)
    language = LANGUAGES.get(site, "英语")
    prompt = str(getattr(cfg, "shopee_" + kind + "_prompt", "") or DEFAULT_PROMPTS[kind])
    prompt = prompt.replace("{language}", language)
    context = {k: product.get(k) for k in ("itemName", "name", "itemSku", "description", "descriptionInfoStr", "attributes")}
    emit("Shopee · AI 文案", "开始", kind)
    text = api.chat(model_of(cfg, "text"), [{"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)}], max_tokens=3000)
    if not str(text or "").strip():
        raise ValueError("AI 返回空文案，请重试")
    emit("Shopee · AI 文案", "完成", kind, level="OK")
    return str(text).strip()

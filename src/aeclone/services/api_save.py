"""add.json 保存接口的直接 API 保存（绕开 DOM 模拟）。

逆向成果（docs/ADD-JSON-REVERSE-NOTES.md）：
- 传输：multipart/form-data，字段 `file` = ZIP 压缩包，内含 `smtEditSave.txt`
  （内容 = JSON.stringify(payload)）。
- payload：60 个扁平键，本质是 edit.json 商品对象的"提交形态"。
- 属性格式：系统属性 {attrNameId, attrValueId}；自定义属性 {attrName, attrValue}。
"""
from __future__ import annotations

import json

# 保存动作 → op 值（逆向自 add.js 按钮 onClick）：
#   保存 L(1)=1 / 立即发布 key=2 / 保存并移入待发布 L(3)=3 / 更新=4 / 定时发布 key=5
OP_MAP = {"save_only": 1, "publish": 2, "save_to_waiting": 3}


# 系统属性：从模板产品合并到目标时必须带 attrNameId/attrValueId（不能只靠名字）
SYSTEM_ATTR_NAMES = {
    "Brand Name": "品牌",
    "Origin": "产地",
    "Charge Status": "是否带电",
    "High-concerned chemical": "高关注化学品",
}

# payload 里与 edit.json 一一对应的字段
_DIRECT_KEYS = [
    "shopId", "categoryId", "subject", "sourceUrl", "groupId", "groupIds",
    "imageURLs", "extraImages", "marketImg1", "marketImg2",
    "productMinPrice", "productMaxPrice", "aeopAeProductSKUs",
    "cspVideoId", "aeopAEMultimedia", "installVideoId", "productManual",
    "bulkOrder", "bulkDiscount", "dropshipDiscount", "productUnit", "packageType", "lotNum",
    "deliveryTime", "wsValidNum", "reduceStrategy", "ladderPriceListJson",
    "aeopNationalQuoteConfiguration", "sizeChartIdListJson", "sizechartId",
    "detail", "mobileDetail", "grossWeight", "packageLength", "packageWidth",
    "packageHeight", "baseUnit", "addUnit", "addWeight", "isPackSell",
    "specialProductTypeList", "freightTemplateId", "promiseTemplateId",
    "shippingTempateName", "freightTemplateIsAi", "hsCode",
    "aeopQualificationStructList", "manufactureId", "msrEuId", "msrTrId",
    "submitEuQualification", "taxType", "productType",
]


def _props_of(product: dict) -> list[dict]:
    """把 edit.json 的 aeopAeProductPropertys（可能是 JSON 字符串）转成 list。"""
    raw = product.get("aeopAeProductPropertys") or "[]"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return raw if isinstance(raw, list) else []


def to_submit_props(product: dict, template: dict | None = None,
                    custom_attributes: list | None = None) -> list[dict]:
    """生成保存用的属性数组。

    - 系统属性（有 attrNameId）→ {attrNameId, attrValueId}
    - 自定义属性（无 id）→ {attrName, attrValue}
    - 关键：**自动检测并删除自定义属性里与系统属性重名的项**（品牌/型号/产地等，
      不限于品牌）。系统属性名集合从「模板带 id 的属性」自动推导，所以任何类目都适用。
    """
    def _lower(name: str) -> str:
        return (name or "").strip().lower()

    # 系统属性名集合：模板里所有带 attrNameId 的属性名 + 内置 4 个兜底
    system_names: set[str] = {_lower(k) for k in SYSTEM_ATTR_NAMES}
    template_system: dict[str, dict] = {}
    if template is not None:
        for a in _props_of(template):
            nm = str(a.get("attrName") or "").strip()
            if nm and a.get("attrNameId"):
                system_names.add(_lower(nm))
                template_system[_lower(nm)] = a

    out: list[dict] = []
    seen_system: set[str] = set()
    have_names: set[str] = set()

    # 先放目标自己的属性
    for a in _props_of(product):
        name = str(a.get("attrName") or "").strip()
        if not name:
            continue
        name_id = str(a.get("attrNameId") or "")
        value_id = str(a.get("attrValueId") or "")
        value = str(a.get("attrValue") or "").strip()
        have_names.add(name)

        # ① 有 id → 系统属性，只留 id 对
        if name_id and value_id:
            if _lower(name) not in seen_system:
                out.append({"attrNameId": name_id, "attrValueId": value_id})
                seen_system.add(_lower(name))
            continue
        # ② 无 id 但名字与系统属性重名 → 删除（重复的自定义属性）
        if _lower(name) in system_names:
            continue
        # ③ 纯自定义属性
        if value and value.upper() not in ("NONE", "NULL"):
            out.append({"attrName": name, "attrValue": value})

    # 合并模板的系统属性（目标缺失的才补）
    for ln, a in template_system.items():
        if ln in seen_system:
            continue
        out.insert(0, {"attrNameId": str(a.get("attrNameId")),
                       "attrValueId": str(a.get("attrValueId"))})
        seen_system.add(ln)

    # 用户手动追加的自定义属性（"Name=Value" 列表），重名的同样跳过
    for c in (custom_attributes or []):
        if isinstance(c, str) and "=" in c:
            _n, _v = c.split("=", 1)
            _n, _v = _n.strip(), _v.strip()
            if _n and _v and _n not in have_names and _lower(_n) not in system_names:
                out.append({"attrName": _n, "attrValue": _v})
                have_names.add(_n)
    return out


def merge_skus(product: dict, template: dict | None) -> list[dict]:
    """把目标 SKU 缺失的字段（originalBox/logisticAttrList/specialProductTypeList）
    从模板 SKU 补上。"""
    raw = product.get("aeopAeProductSKUs")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    skus = raw if isinstance(raw, list) else []
    tpl_skus = None
    if template is not None:
        tr = template.get("aeopAeProductSKUs")
        if isinstance(tr, str):
            try:
                tr = json.loads(tr)
            except Exception:
                tr = []
        tpl_skus = (tr or [])[0] if isinstance(tr, list) and tr else None

    out = []
    for sku in skus:
        s = dict(sku)
        if tpl_skus:
            for k in ("originalBox", "logisticAttrList", "specialProductTypeList"):
                if s.get(k) in (None, "") and tpl_skus.get(k) not in (None, ""):
                    s[k] = tpl_skus[k]
        out.append(s)
    return out


def build_save_payload(product: dict, template: dict | None = None,
                       op: int = 1, action: str = "save_only",
                       overrides: dict | None = None,
                       custom_attributes: list | None = None) -> dict:
    """从 edit.json 商品对象构建 add.json 的 payload。

    product / template 都是 edit.json 返回的 `data.product`。
    overrides: AI 产物覆盖（subject / detail / imageURLs / aeopAeProductSKUs …），
              最后套用，覆盖从 edit.json 直接拷来的值。
    """
    p: dict = {}
    for k in _DIRECT_KEYS:
        p[k] = product.get(k)

    # 缺失的必填字段从模板补
    for k in ("categoryId", "productType", "taxType", "sizechartId",
              "grossWeight", "packageLength", "packageWidth", "packageHeight",
              "productManual", "freightTemplateId", "promiseTemplateId",
              "productUnit", "reduceStrategy", "manufactureId", "msrEuId",
              "msrTrId", "shippingTempateName", "wsValidNum",
              "hsCode", "ladderPriceListJson", "sizeChartIdListJson",
              "deliveryTime", "bulkOrder", "bulkDiscount",
              "aeopQualificationStructList", "specialProductTypeList"):
        if not p.get(k) and template is not None and template.get(k) not in (None, "", 0, "0"):
            p[k] = template.get(k)

    p["aeopAeProductSKUs"] = json.dumps(merge_skus(product, template), ensure_ascii=False)

    p["aeopAeProductPropertys"] = json.dumps(
        to_submit_props(product, template, custom_attributes=custom_attributes),
        ensure_ascii=False)
    p["motorAfid"] = ""
    p["motorIds"] = ""
    p["newAfid"] = ""
    # 服务模板：店铺的 Service Template for New Sellers 的 templateId 就是 "0"
    # （promiseList 接口返回 templateId="0"），None 会被后端判"服务模板不能为空"
    if p.get("promiseTemplateId") in (None, ""):
        p["promiseTemplateId"] = "0"

    # ★ 数据安全：类目 ID 为空时保存会把商品类目清掉（历史事故），直接拒绝
    if p.get("categoryId") in (None, "", 0, "0"):
        raise ValueError(
            "商品缺少类目 ID（categoryId 为空）。为避免清空类目/属性，本次保存已中止。"
            "请先在店小秘给该商品选好类目，或在「商品设置」选一个同类目模板商品。")

    p["showQualificationModal"] = False
    p["op"] = OP_MAP.get(action, op)
    p["id"] = product.get("id") or ""
    p["dxmScheduleTimeStr"] = ""
    if overrides:
        p.update(overrides)
    return p


def build_ai_overrides(product_edit: dict, p: dict, cfg, images: list[str] | None = None,
                       ship: tuple | None = None) -> dict:
    """把 AI 产物（标题/描述/SKU/图片）转成 payload 的 overrides。

    product_edit: 目标商品的 edit.json product（有原始 aeopAeProductSKUs 结构）
    p: Publisher 处理后的商品（title_en / detail_html / skus / price_result / images）
    """
    import json as _j

    overrides: dict = {}

    # 标题
    if p.get("title_en"):
        overrides["subject"] = p["title_en"]

    # 详情（PC 端 HTML）
    if p.get("detail_html"):
        overrides["detail"] = p["detail_html"]
    elif p.get("detail_local_images_html"):
        # 没开 AI 详情时：把本地详情图追加到商品原有详情上
        _base = str(product_edit.get("detail") or "")
        _pos = str(getattr(cfg, "detail_local_image_position", "tail") or "tail").lower()
        _mh = p["detail_local_images_html"]
        overrides["detail"] = (_mh + _base) if _pos == "head" else (_base + _mh)

    # 主图（公网 URL 分号连接）
    if images:
        overrides["imageURLs"] = ";".join(images)

    # SKU：在原 SKU 结构上套用 AI 名称 + 价格 + 库存
    raw_skus = product_edit.get("aeopAeProductSKUs")
    if isinstance(raw_skus, str):
        try:
            raw_skus = _j.loads(raw_skus)
        except Exception:
            raw_skus = []
    skus = raw_skus if isinstance(raw_skus, list) else []
    ai_skus = p.get("skus") or []

    price = None
    try:
        pr = p.get("price_result")
        price = float(getattr(pr, "final", 0) or 0)
    except Exception:
        price = 0

    stock = str(getattr(cfg, "stock_quantity", "") or "").strip()
    if not stock and skus:
        stock = str(skus[0].get("ipmSkuStock") or "")

    for i, sku in enumerate(skus):
        # SKU 名称（AI）
        if ai_skus and i < len(ai_skus) and getattr(cfg, "write_sku_codes_to_product", True):
            ai = ai_skus[i]
            name = (ai.get("optimized_name") or ai.get("name") or ai.get("sku") or "").strip()
            if name:
                sku["skuCode"] = name[:20]
        # 价格（开关 write_price_to_product）
        if price > 0 and getattr(cfg, "write_price_to_product", True):
            sku["skuPrice"] = f"{price:.2f}"
        # 库存
        if stock:
            sku["ipmSkuStock"] = stock
        # SKU 图（AI 编辑后）
        if ai_skus and i < len(ai_skus):
            img = ai_skus[i].get("processed_image") or ai_skus[i].get("image")
            if img:
                prop = (sku.get("aeopSKUProperty") or [{}])
                if prop and isinstance(prop, list):
                    prop[0]["imageUrlList"] = str(img)
                    sku["aeopSKUProperty"] = prop
                sku["mobileModelSkuImage"] = str(img)

    if skus and (price > 0 or stock or ai_skus):
        overrides["aeopAeProductSKUs"] = _j.dumps(skus, ensure_ascii=False)

    # 价格同步：改 skuPrice 后 productMinPrice/MaxPrice 要一致
    if price > 0 and getattr(cfg, "write_price_to_product", True):
        overrides["productMinPrice"] = f"{price:.2f}"
        overrides["productMaxPrice"] = f"{price:.2f}"

    # 批发（店小秘风格：购买 N 件减免 X% + 一件代发折扣）
    if getattr(cfg, "wholesale_enable", False):
        try:
            _bo = int(float(str(getattr(cfg, "bulk_order", "2") or "2")))
        except Exception:
            _bo = 0
        try:
            _bd = int(float(str(getattr(cfg, "bulk_discount", "15") or "15")))
        except Exception:
            _bd = 0
        try:
            _dd = int(float(str(getattr(cfg, "dropship_discount", "0") or "0")))
        except Exception:
            _dd = 0
        if 2 <= _bo <= 100000:
            overrides["bulkOrder"] = _bo
        if 1 <= _bd <= 99:
            overrides["bulkDiscount"] = _bd
        if 0 <= _dd <= 99:
            overrides["dropshipDiscount"] = _dd

    # 区域调价：aeopNationalQuoteConfiguration = {configurationType, configurationData}
    if getattr(cfg, "regional_quote_enable", False):
        _rq = getattr(cfg, "regional_quotes", "[]")
        if isinstance(_rq, str):
            try:
                _rq = json.loads(_rq)
            except Exception:
                _rq = []
        rows = []
        for r in (_rq if isinstance(_rq, list) else []):
            if not isinstance(r, dict):
                continue
            c = str(r.get("country") or "").strip().upper()
            try:
                _pv = float(r.get("price") or 0)
            except Exception:
                continue
            if c and _pv > 0:
                rows.append({"shiptoCountry": c, "absoluteQuoteMap": {"": _pv}})
        if rows:
            overrides["aeopNationalQuoteConfiguration"] = json.dumps({
                "configurationType": "ABSOLUTE",
                "configurationData": json.dumps(rows, ensure_ascii=False),
            }, ensure_ascii=False)

    # 主图视频：cspVideoId
    if p.get("csp_video_id"):
        overrides["cspVideoId"] = str(p["csp_video_id"])

    # 营销图：marketImg1（1:1）/ marketImg2（3:4）
    if p.get("market_images"):
        _mk = p["market_images"]
        if len(_mk) >= 1:
            overrides["marketImg1"] = str(_mk[0])
        if len(_mk) >= 2:
            overrides["marketImg2"] = str(_mk[1])
        elif len(_mk) == 1:
            overrides["marketImg2"] = str(_mk[0])

    # 重量/尺寸：AI 估算或设置（ship = (weight, length, width, height)）
    if ship:
        w, l, wd, h = ship[0], ship[1], ship[2], ship[3]
        if w:
            overrides["grossWeight"] = str(w)
        if l and wd and h:
            overrides["packageLength"] = str(l)
            overrides["packageWidth"] = str(wd)
            overrides["packageHeight"] = str(h)

    return overrides

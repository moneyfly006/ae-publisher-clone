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
    # 与老软件对齐：type / model / condition 也是站点系统属性，
    # 自定义属性里出现同名（含同义词）要删除，否则与系统属性重复导致保存失败
    "Type": "类型",
    "Model Number": "型号",
    "Condition": "成色",
    "Certification": "认证",
}

# payload 里与 edit.json 一一对应的字段
_DIRECT_KEYS = [
    "shopId", "categoryId", "subject", "sourceUrl", "groupId", "groupIds",
    "imageURLs", "extraImages", "marketImg1", "marketImg2",
    "productMinPrice", "productMaxPrice", "aeopAeProductSKUs",
    "cspVideoId", "aeopAEMultimedia", "installVideoId", "productManual",
    "bulkOrder", "bulkDiscount", "dropshipDiscount", "productUnit", "packageType", "lotNum",
    "originalBox", "cargoPrice",
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


# 属性名归一化 + 同义词：用来判断「自定义属性」是否和「系统属性」重复
_ATTR_SYNONYMS = {
    "place of origin": "origin",
    "country of origin": "origin",
    "origin place": "origin",
    "brand": "brand name",
    "brands": "brand name",
    "gross weight": "weight",
    "item weight": "weight",
    "product weight": "weight",
    "net weight": "weight",
    "package weight": "weight",
    "weight kg": "weight",
    "product type": "type",
    "item type": "type",
    "product name": "name",
    "model number": "model",
    "warranty duration": "warranty",
}
_ATTR_PREFIX = ("product ", "item ", "custom ", "the ")
# 店小秘返回的属性经常只有 id 没有名字，这里补上常见系统属性名，
# 否则「自定义属性重名」判断会失效、系统属性也不会被提交
_TPL_ATTR_NAMES = {
    "2": "Brand Name",
    "219": "Origin",
    "400074001": "Charge Status",
    "400000603": "High-concerned chemical",
}


def _norm_attr_name(name: str) -> str:
    """属性名归一化：小写、去标点/单位、去复数、去前缀、同义词归并。"""
    import re as _re
    t = str(name or "").strip().lower()
    t = _re.sub(r"[^a-z0-9 ]+", " ", t)
    t = _re.sub(r"\s+", " ", t).strip()
    for pre in _ATTR_PREFIX:
        if t.startswith(pre):
            t = t[len(pre):]
    # 去掉结尾的 (kg)/(mm) 之类
    t = _re.sub(r"\s*\([^)]*\)\s*$", "", t).strip()
    if t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return _ATTR_SYNONYMS.get(t, t)


def relaxed_payload(payload: dict, level: int, template: dict | None = None) -> dict:
    """在「保存失败！」(店小秘的笼统错误) 时，逐级放宽 payload。

    店小秘对属性/SKU 维度与类目不匹配时只回一句「保存失败！」，
    所以失败后依次尝试更保守的结构：
      level=1：属性只保留模板的系统属性 + 纯自定义属性；SKU id 清空
      level=2：属性只保留模板的系统属性；SKU 属性也清空
    """
    import copy as _copy
    import json as _json
    q = _copy.deepcopy(payload)

    # 模板里的有效系统属性
    tpl_sys = []
    if template is not None:
        raw = template.get("aeopAeProductPropertys")
        try:
            arr = _json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            arr = []
        for a in arr:
            if not isinstance(a, dict):
                continue
            nid, vid = a.get("attrNameId"), a.get("attrValueId")
            if nid in (None, "", "-1", 0) or vid in (None, "", "None", "null", "-1", 0):
                continue
            tpl_sys.append({"attrNameId": str(nid), "attrValueId": str(vid)})

    # 属性
    try:
        cur = _json.loads(q.get("aeopAeProductPropertys") or "[]")
    except Exception:
        cur = []
    if level >= 2:
        q["aeopAeProductPropertys"] = _json.dumps(tpl_sys, ensure_ascii=False)
    else:
        kept = []
        for a in cur:
            if not isinstance(a, dict):
                continue
            if "attrNameId" in a:            # 系统属性：只保留模板里有的
                if any(str(a.get("attrNameId")) == x["attrNameId"] and
                       str(a.get("attrValueId")) == x["attrValueId"] for x in tpl_sys):
                    kept.append(a)
            elif "attrName" in a:            # 纯自定义属性
                kept.append(a)
        for x in tpl_sys:
            if not any(str(a.get("attrNameId")) == x["attrNameId"] for a in kept):
                kept.insert(0, x)
        q["aeopAeProductPropertys"] = _json.dumps(kept, ensure_ascii=False)

    # SKU：清空 id（避免引用旧类目的 SKU 维度），level=2 连属性一起清
    try:
        sk = _json.loads(q.get("aeopAeProductSKUs") or "[]")
    except Exception:
        sk = []
    for s2 in sk:
        if not isinstance(s2, dict):
            continue
        s2["id"] = ""
        if level >= 2:
            s2["aeopSKUProperty"] = []
    q["aeopAeProductSKUs"] = _json.dumps(sk, ensure_ascii=False)
    return q

def mobile_detail_ok(md) -> bool:
    """判断已有 mobileDetail 是否可用。

    历史 bug 会把详情里的 CSS 当成正文塞进去（content 里出现 `#xx .yy{...}`），
    这种必须重生成，否则保存/发布会失败。
    """
    if md in (None, ""):
        return False
    try:
        d = json.loads(md) if isinstance(md, str) else md
    except Exception:
        return False
    mods = (d or {}).get("moduleList")
    if not isinstance(mods, list) or not mods:
        return False
    for m in mods:
        if not isinstance(m, dict):
            return False
        if m.get("type") == "text":
            for t in (m.get("texts") or []):
                c = str((t or {}).get("content") or "")
                # 典型的 CSS 片段特征
                if ("{" in c and "}" in c) or "magic-" in c or "#detail_decorate" in c:
                    return False
    return True


def _valid_attr_id(v) -> bool:
    """属性 id 是否「真的有效」。

    店小秘里 -1 / None / "None" / "null" / 0 都表示「没有这个 id」，
    当成系统属性提交会报 null#attr_value_id。
    """
    if v is None:
        return False
    s = str(v).strip().lower()
    return s not in ("", "none", "null", "-1", "0")


def to_submit_props(product: dict, template: dict | None = None,
                    custom_attributes: list | None = None) -> list[dict]:
    """生成保存用的属性数组。

    - 系统属性（attrNameId/attrValueId 都有效）→ {attrNameId, attrValueId}
    - 其余（含 attrNameId = -1 的自定义属性）→ {attrName, attrValue}
    - 自动删除：自定义属性里与系统属性重名的项
    - 自动过滤：id 为 -1/None/"None"/null 的脏属性
    """
    def _bad_value(v: str) -> bool:
        return str(v or "").strip().upper() in ("", "NONE", "NULL")

    # 模板里的「真系统属性」：id 有效才算
    system_names: set[str] = {_norm_attr_name(k) for k in SYSTEM_ATTR_NAMES}
    template_system: dict[str, dict] = {}
    if template is not None:
        for a in _props_of(template):
            nid = a.get("attrNameId")
            vid = a.get("attrValueId")
            if not (_valid_attr_id(nid) and _valid_attr_id(vid)):
                continue
            _nm = str(a.get("attrName") or "").strip() or _TPL_ATTR_NAMES.get(str(nid), "")
            _key = _norm_attr_name(_nm) if _nm else f"id:{nid}"
            template_system[_key] = a
            if _nm:
                system_names.add(_key)

    out: list[dict] = []
    seen_system: set[str] = set()
    seen_custom: set[str] = set()        # 自定义属性名（归一化）→ 同名去重
    have_names: set[str] = set()

    # 预扫目标自己的系统属性（有效 id）：把名字（含 id→名称兜底）加入 system_names，
    # 这样后面处理自定义属性时，凡与目标系统属性重名（含同义词）都会被删除。
    # 否则自定义 "Type" / "Model Number" 等会和系统属性重复，导致保存失败。
    for a in _props_of(product):
        _nid = a.get("attrNameId")
        _vid = a.get("attrValueId")
        if not (_valid_attr_id(_nid) and _valid_attr_id(_vid)):
            continue
        _nm = str(a.get("attrName") or "").strip() or _TPL_ATTR_NAMES.get(str(_nid), "")
        if _nm:
            system_names.add(_norm_attr_name(_nm))

    # 目标自己的属性
    for a in _props_of(product):
        name = str(a.get("attrName") or "").strip()
        name_id = a.get("attrNameId")
        value_id = a.get("attrValueId")
        value = str(a.get("attrValue") or "").strip()

        # ① id 都有效 → 系统属性（即使没有 attrName 也按 id 提交，不再丢弃）
        if _valid_attr_id(name_id) and _valid_attr_id(value_id):
            _nm = name or _TPL_ATTR_NAMES.get(str(name_id), "")
            _key = _norm_attr_name(_nm) if _nm else f"id:{name_id}"
            if _key not in seen_system:
                out.append({"attrNameId": str(name_id), "attrValueId": str(value_id)})
                seen_system.add(_key)
            if name:
                have_names.add(name)
            continue
        # ② 无有效 id → 自定义属性（名字叫 "None"/空 的丢弃）
        if _bad_value(name) or _bad_value(value):
            continue
        _ck = _norm_attr_name(name)
        if _ck in system_names:
            continue                      # 与系统属性重名（含同义词）→ 删除
        if _ck in seen_custom:
            continue                      # 两个自定义属性同名 → 只保留第一个（CHK_CUSTOM_PROP_NAME）
        seen_custom.add(_ck)
        out.append({"attrName": name, "attrValue": value})
        have_names.add(name)

    # 模板系统属性：目标缺的才补（只补 id 有效的）
    for ln, a in template_system.items():
        if ln in seen_system:
            continue
        out.insert(0, {"attrNameId": str(a.get("attrNameId")),
                       "attrValueId": str(a.get("attrValueId"))})
        seen_system.add(ln)

    # 用户手动追加的自定义属性
    for c in (custom_attributes or []):
        if isinstance(c, str) and "=" in c:
            _n, _v = c.split("=", 1)
            _n, _v = _n.strip(), _v.strip()
            _ck = _norm_attr_name(_n) if _n else ""
            if (_n and _v and _ck and _ck not in seen_custom
                    and _ck not in system_names and _n not in have_names):
                out.append({"attrName": _n, "attrValue": _v})
                have_names.add(_n)
                seen_custom.add(_ck)
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
                if s.get(k) in (None, "", []) and tpl_skus.get(k) not in (None, "", []):
                    s[k] = tpl_skus[k]
        out.append(s)

    # 商品没有 SKU（采集草稿常见）时，用模板首个 SKU 当骨架，否则站点会报
    # 「是否原箱不能为空」「请完善填写 SKU 编码」。
    if not out:
        _base = dict(tpl_skus) if tpl_skus else {}
        _base.setdefault("skuCode", "SKU-1")
        _base.setdefault("skuPrice", str(_base.get("skuPrice") or "0"))
        _base.setdefault("ipmSkuStock", str(_base.get("ipmSkuStock") or "999"))
        _base.setdefault("originalBox", "1")
        out = [_base]

    # 补齐站点必填：是否原箱 / SKU 编码 / 货值
    for s in out:
        if s.get("originalBox") in (None, ""):
            s["originalBox"] = "1"
        if not str(s.get("skuCode") or "").strip():
            s["skuCode"] = str(s.get("sku") or "SKU-1")
    return out


def build_save_payload(product: dict, template: dict | None = None,
                       op: int = 1, action: str = "save_only",
                       overrides: dict | None = None,
                       custom_attributes: list | None = None,
                       cargo_price: str | None = None) -> dict:
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

    _sku_list = merge_skus(product, template)
    _sku_list_all = _sku_list
    p["aeopAeProductSKUs"] = json.dumps(_sku_list, ensure_ascii=False)
    # 站点要求商品级「是否原箱」不能为空；取第一个 SKU 的值（与店小秘前端一致）
    if p.get("originalBox") in (None, ""):
        try:
            p["originalBox"] = str((_sku_list_all[0] or {}).get("originalBox") or "1")
        except Exception:
            p["originalBox"] = "1"

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

    # 货值（站点必填）：优先用传入值，其次沿用商品原值，最后默认 50
    if not str(p.get("cargoPrice") or "").strip():
        p["cargoPrice"] = str(cargo_price or "50").strip() or "50"
    _cv = str(p["cargoPrice"]).strip()
    # 每个 SKU 也必须带货值（站点校验按 SKU 走）
    for _s in _sku_list_all:
        if not str(_s.get("cargoPrice") or "").strip():
            _s["cargoPrice"] = _cv
    p["aeopAeProductSKUs"] = json.dumps(_sku_list_all, ensure_ascii=False)

    # 无线端描述：为空或被 CSS 污染 → 从 PC 详情重新生成
    if not mobile_detail_ok(p.get("mobileDetail")):
        p["mobileDetail"] = build_mobile_detail(p.get("detail") or "")

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
        # overrides 可能整体重建了 SKU：再按模板补一次站点必填，防止
        # 「物流属性不能为空 / 货值不能为空 / 是否原箱不能为空」这类整单失败
        try:
            _sk = json.loads(p.get("aeopAeProductSKUs") or "[]")
            _t0 = None
            if template is not None:
                _tt = template.get("aeopAeProductSKUs")
                if isinstance(_tt, str):
                    _tt = json.loads(_tt)
                _t0 = (_tt or [])[0] if isinstance(_tt, list) and _tt else None
            if isinstance(_sk, list):
                _cv2 = str(p.get("cargoPrice") or "50")
                for _s in _sk:
                    if not str(_s.get("cargoPrice") or "").strip():
                        _s["cargoPrice"] = _cv2
                    if _t0:
                        for _k in ("logisticAttrList", "specialProductTypeList", "originalBox"):
                            if _s.get(_k) in (None, "", []) and _t0.get(_k) not in (None, "", []):
                                _s[_k] = _t0[_k]
                p["aeopAeProductSKUs"] = json.dumps(_sk, ensure_ascii=False)
        except Exception:
            pass
    return p


# ---------------- 无线端（手机）描述 ----------------
# 店小秘/速卖通的 mobileDetail 是结构化 JSON：
#   {"moduleList":[{"type":"text","texts":[{...}]},{"type":"image","images":[{...}]}], "version": ...}
# 商品缺这个字段时，发布会报「请补充无线端描述后重新发布」。
_TEXT_STYLE = {"fontSize": 13, "color": "#434649", "fontWeight": "normal", "align": "left"}


def build_mobile_detail(detail_html: str, max_images: int = 20) -> str:
    """把 PC 端详情 HTML 转成无线端 moduleList JSON。

    - 文字段 → text 模块（去掉标签、压缩空白）
    - 连续图片 → 合并成一个 image 模块（与店小秘一致）
    """
    import re as _re

    html = str(detail_html or "")
    # ★ 必须先去掉 <style>/<script>/注释：详情 HTML 开头常有大量 CSS，
    # 不去掉会把 CSS 文本当成正文塞进无线端描述（会导致发布/保存失败）
    html = _re.sub(r"<style[^>]*>.*?</style>", "", html, flags=_re.I | _re.S)
    html = _re.sub(r"<script[^>]*>.*?</script>", "", html, flags=_re.I | _re.S)
    html = _re.sub(r"<!--.*?-->", "", html, flags=_re.S)
    modules: list[dict] = []
    if not html.strip():
        modules.append({"type": "text", "texts": [{"class": "body",
                                                   "content": "", "style": dict(_TEXT_STYLE)}]})
        return json.dumps({"moduleList": modules, "version": "2.0.0"}, ensure_ascii=False)

    # 按 <img ...> 切分
    parts = _re.split(r"(<img[^>]*>)", html, flags=_re.I)
    pending_imgs: list[dict] = []
    img_total = 0

    def _flush_imgs():
        nonlocal pending_imgs
        if pending_imgs:
            modules.append({"type": "image", "images": pending_imgs[:max_images]})
            pending_imgs = []

    for part in parts:
        if not part:
            continue
        if _re.match(r"<img", part, _re.I):
            m = _re.search(r'src=["\']([^"\']+)["\']', part, _re.I)
            if m:
                _u = m.group(1).strip()
                if _u.startswith("//"):
                    _u = "https:" + _u        # 协议相对地址
            else:
                _u = ""
            if _u.startswith("http"):
                # 店小秘规则：无线端不支持 GIF；总数最多 50 张
                if _u.lower().split("?")[0].endswith(".gif"):
                    continue
                if img_total < 50:
                    pending_imgs.append({"url": _u,
                                         "style": {"height": "500", "width": "800", "hasMargin": True}})
                    img_total += 1
            continue
        # 文字：去标签 → 压缩空白
        txt = _re.sub(r"<br\s*/?>", "\n", part, flags=_re.I)
        txt = _re.sub(r"</p>", "\n", txt, flags=_re.I)
        txt = _re.sub(r"<[^>]+>", "", txt)
        txt = _re.sub(r"[ \t]+", " ", txt)
        txt = _re.sub(r"\n{3,}", "\n\n", txt).strip()
        if txt:
            _flush_imgs()
            modules.append({"type": "text",
                            "texts": [{"class": "body", "content": txt[:4000],
                                       "style": dict(_TEXT_STYLE)}]})
    _flush_imgs()

    if not modules:
        modules.append({"type": "text", "texts": [{"class": "body", "content": "",
                                                   "style": dict(_TEXT_STYLE)}]})
    return json.dumps({"moduleList": modules, "version": "2.0.0"}, ensure_ascii=False)


def build_ai_overrides(product_edit: dict, p: dict, cfg, images: list[str] | None = None,
                       ship: tuple | None = None, template: dict | None = None) -> dict:
    """把 AI 产物（标题/描述/SKU/图片）转成 payload 的 overrides。

    product_edit: 目标商品的 edit.json product（有原始 aeopAeProductSKUs 结构）
    p: Publisher 处理后的商品（title_en / detail_html / skus / price_result / images）
    """
    import json as _j

    overrides: dict = {}

    # 标题
    if p.get("title_en"):
        overrides["subject"] = p["title_en"]

    # 详情（PC 端 HTML）+ 同步生成无线端描述
    if p.get("detail_html"):
        overrides["detail"] = p["detail_html"]
        overrides["mobileDetail"] = build_mobile_detail(p["detail_html"])
    elif p.get("detail_local_images_html"):
        # 没开 AI 详情时：把本地详情图追加到商品原有详情上
        _base = "" if getattr(cfg, "delete_detail_text", False) else str(product_edit.get("detail") or "")
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

    # 模板首个 SKU：变种主题 / 物流属性 / 原箱等站点必填项都从它沿用
    _tpl_sku0 = None
    if template:
        _tr = template.get("aeopAeProductSKUs")
        if isinstance(_tr, str):
            try:
                _tr = _j.loads(_tr)
            except Exception:
                _tr = []
        _tpl_sku0 = (_tr or [])[0] if isinstance(_tr, list) and _tr else None

    # ★ 变种主题（平台新规）：平台要求变种里必须有 Voltage/plug type 主题，
    # 否则报 isv.7CHK_PLUG_SALE_PROP_REQUIRED_CHECK: Voltage/plug type required。
    # 两种情况都要沿用模板的变种：
    #   ① 非标准格式（如 [{"Model Number": "..."}]，没有 skuPropertyId）
    #   ② 格式标准、但缺少 plug 变种（无 plug_parent_property_id / parentPropertyId）
    #      —— 线上真实案例：单变种 Model Number(skuPropertyId=200007763) 照样失败
    if _tpl_sku0:
        _tpl_props = _tpl_sku0.get("aeopSKUProperty")
        if isinstance(_tpl_props, list) and _tpl_props:
            _tpl_has_plug = any(
                isinstance(x, dict) and (x.get("plug_parent_property_id") or x.get("parentPropertyId"))
                for x in _tpl_props)
            for sku in skus:
                _p0 = sku.get("aeopSKUProperty")
                _std = isinstance(_p0, list) and any(
                    isinstance(x, dict) and "skuPropertyId" in x for x in _p0)
                _has_plug = isinstance(_p0, list) and any(
                    isinstance(x, dict) and (x.get("plug_parent_property_id") or x.get("parentPropertyId"))
                    for x in _p0)
                if (not _std) or (_tpl_has_plug and not _has_plug):
                    sku["aeopSKUProperty"] = [dict(x) for x in _tpl_props if isinstance(x, dict)]

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

    # ★ 关键：这些 SKU 会覆盖 build_save_payload 里 merge_skus 的结果，
    # 所以必须把站点必填字段补齐，否则依次会报
    # 「是否原箱不能为空」「请完善填写 SKU 编码」「货值不能为空」「物流属性不能为空」
    _cv_sku = str(getattr(cfg, "declared_value", "") or "").strip() or "50"
    for _s in skus:
        if _s.get("originalBox") in (None, "", []):
            _s["originalBox"] = "1"
        if not str(_s.get("skuCode") or "").strip():
            _s["skuCode"] = str(_s.get("sku") or "SKU-1")
        # 货值：站点按 SKU 逐个校验，缺一个就整单「货值不能为空」
        if not str(_s.get("cargoPrice") or "").strip():
            _s["cargoPrice"] = _cv_sku
        # 物流属性 / 特殊品类 / 原箱：缺就沿用模板 SKU，否则「物流属性不能为空」
        if _tpl_sku0:
            for _k in ("logisticAttrList", "specialProductTypeList", "originalBox"):
                if _s.get(_k) in (None, "", []) and _tpl_sku0.get(_k) not in (None, "", []):
                    _s[_k] = _tpl_sku0[_k]
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

    # 货值（站点必填，报错「货值不能为空」）：取设置里的申报货值，默认 50
    try:
        _dv = str(getattr(cfg, "declared_value", "") or "").strip()
        if not _dv:
            _dv = "50"
        overrides["cargoPrice"] = _dv
    except Exception:
        overrides["cargoPrice"] = "50"

    # 主图视频：cspVideoId
    if p.get("csp_video_id"):
        overrides["cspVideoId"] = str(p["csp_video_id"])

    # 营销图（线上实测：02:02 通过 / 反序时 020051 too small）：
    #   marketImg1 = 3:4 场景图（≥750x1000）
    #   marketImg2 = 1:1 白底图（≥800x800）
    # _mk 由 run()/ _market_images_for 按 [场景3:4, 白底1:1] 顺序返回
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

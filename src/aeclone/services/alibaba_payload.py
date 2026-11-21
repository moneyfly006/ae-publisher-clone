"""把 AI 产物 / 预设资料映射成 Alibaba(alibabaSave.txt) 保存结构，并做必填校验。"""
from __future__ import annotations

import json
import re
from copy import deepcopy

MAX_SUBJECT = 128
MAX_KEYWORDS = 5
MIN_KEYWORDS = 3
MAX_MAIN_IMAGES = 6

STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "your", "our", "are",
    "you", "can", "will", "all", "new", "high", "low", "good", "best", "hot",
    "sale", "free", "shipping", "custom", "customized", "oem", "odm",
}


def _cfg_get(cfg, key: str, default=""):
    """cfg 可能是 Config 对象、dict 或 None（测试/脚本）。"""
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _as_list(v, sep=None) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                d = json.loads(s)
                return d if isinstance(d, list) else []
            except Exception:
                return []
        return [x.strip() for x in (s.split(sep) if sep else s.split(",")) if x.strip()]
    return []


def _json(v) -> str:
    return v if isinstance(v, str) else json.dumps(v if v is not None else [], ensure_ascii=False)


def main_images_from(product: dict, images: list | None = None) -> list[str]:
    if images:
        out = [str(u).strip() for u in images if str(u).strip()]
    else:
        out = [u for u in (product.get("mainImage") or "").split(",") if u.strip()]
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq[:MAX_MAIN_IMAGES]


def detail_urls(product: dict, images: list | None = None) -> list[str]:
    if images:
        return [str(u).strip() for u in images if str(u).strip()]
    rows = _as_list(product.get("detailImage"))
    out = []
    for r in rows:
        if isinstance(r, dict) and r.get("url"):
            out.append(str(r["url"]))
        elif isinstance(r, str):
            out.append(r)
    return out


def clean_keywords(text: str, limit: int = MAX_KEYWORDS) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", str(text or "").lower())
    seen, out = set(), []
    for w in words:
        if w in STOPWORDS or w in seen:
            continue
        seen.add(w); out.append(w)
        if len(out) >= limit:
            break
    return out


def keywords_for(save: dict, cfg=None, override: list | str | None = None) -> list[str]:
    """关键词：override > 配置 alibaba_keywords > 从标题提取，至少 3 个。"""
    kws = []
    if override:
        kws = ([x.strip() for x in override.split(",")] if isinstance(override, str)
               else [str(x).strip() for x in override])
    if not kws and cfg is not None:
        kws = [x.strip() for x in str(_cfg_get(cfg, "alibaba_keywords", "") or "").split(",") if x.strip()]
    kws = [k for k in kws if k]
    if len(kws) < MIN_KEYWORDS:
        for w in clean_keywords(save.get("subject", ""), MAX_KEYWORDS):
            if w not in kws:
                kws.append(w)
            if len(kws) >= MAX_KEYWORDS:
                break
    return kws[:MAX_KEYWORDS]


def price_tiers(cfg=None, overrides: dict | None = None) -> list[dict]:
    """阶梯价：overrides['price_tiers'] > 配置 alibaba_price_tiers > 单品价。"""
    raw = None
    if overrides and overrides.get("price_tiers"):
        raw = overrides["price_tiers"]
    elif cfg is not None:
        raw = _cfg_get(cfg, "alibaba_price_tiers", "") or ""
    rows = _as_list(raw) if not isinstance(raw, list) else raw
    tiers = []
    for r in rows:
        if isinstance(r, dict):
            q, p = r.get("start_quantity") or r.get("qty"), r.get("price")
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            q, p = r[0], r[1]
        else:
            continue
        try:
            q, p = int(float(q)), float(p)
        except Exception:
            continue
        if q > 0 and p > 0:
            tiers.append({"start_quantity": q, "price": round(p, 2)})
    tiers.sort(key=lambda x: x["start_quantity"])
    if not tiers:
        price = None
        if overrides and overrides.get("price"):
            price = overrides["price"]
        elif cfg is not None:
            price = _cfg_get(cfg, "price_fixed", None)
        try:
            price = float(price) if price else 0
        except Exception:
            price = 0
        if price > 0:
            tiers = [{"start_quantity": 1, "price": round(price, 2)}]
    return tiers


def merge_attributes(product: dict, cfg=None, overrides: dict | None = None) -> list[dict]:
    """保留原属性，按预设补齐缺失项（预设格式：{名称: 值}，值可为字符串）。"""
    attrs = _as_list(product.get("attributes"))
    attrs = [deepcopy(a) for a in attrs if isinstance(a, dict)]
    presets = {}
    raw = (overrides or {}).get("attributes") or _cfg_get(cfg, "alibaba_attr_presets", "") or ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            presets = json.loads(raw)
        except Exception:
            presets = {}
    elif isinstance(raw, dict):
        presets = raw
    have = {str(a.get("attribute_name") or "").strip().lower() for a in attrs}
    for name, val in presets.items():
        key = str(name).strip().lower()
        if not key or key in have:
            continue
        attrs.append({"attribute_id": "-1", "attribute_name": str(name),
                      "value_id": "-1", "value_name": str(val)})
    return attrs


def build_alibaba_save(product: dict, cfg=None, overrides: dict | None = None,
                       client_cls=None, attr_defs: list | None = None) -> dict:
    """edit.json 商品 + AI 产物 → alibabaSave 字典。"""
    from ..integrations.alibaba import AlibabaClient as _AC
    cls = client_cls or _AC
    save = cls.build_save_dict(product)
    ov = dict(overrides or {})

    subject = str(ov.get("subject") or save.get("subject") or "").strip()
    save["subject"] = subject[:MAX_SUBJECT]

    kws = keywords_for(save, cfg, ov.get("keywords"))
    save["keywords"] = ", ".join(kws)

    imgs = main_images_from(product, ov.get("main_images"))
    if imgs:
        save["mainImage"] = ",".join(imgs)
        save["draftImgUrl"] = "|".join(imgs)

    det = detail_urls(product, ov.get("detail_images"))
    if det:
        save["detailImage"] = json.dumps([{"displayName": "default", "value": -2, "url": u,
                                           "text": ""} for u in det], ensure_ascii=False)

    if ov.get("text_desc"):
        save["textDesc"] = str(ov["text_desc"])

    attrs = merge_attributes(product, cfg, ov)
    filled, unresolved = [], []
    if attr_defs:
        attrs, filled, unresolved = autofill_required_attributes(
            {"attributes": attrs}, attr_defs, cfg, ov)
    if attrs:
        save["attributes"] = json.dumps(attrs, ensure_ascii=False)
    save["_autofilled"] = filled
    save["_unresolved"] = unresolved

    tiers = price_tiers(cfg, ov)
    if tiers:
        save["priceType"] = "ladder_price"
        save["bulkDiscountPrices"] = json.dumps(tiers, ensure_ascii=False)

    # wholesaleTrade：MOQ / 包装 / 重量 / HS / 库存
    try:
        trade = json.loads(save.get("wholesaleTrade") or "{}")
    except Exception:
        trade = {}
    if cfg is not None:
        def num(key, default=None):
            v = getattr(cfg, key, default)
            try:
                return float(v) if v not in (None, "") else default
            except Exception:
                return default

        moq = num("alibaba_min_order_quantity", None)
        trade["min_order_quantity"] = int(moq) if moq and moq > 0 else int(trade.get("min_order_quantity") or 1)
        trade["unit_type"] = str(getattr(cfg, "alibaba_unit_type", trade.get("unit_type") or "Piece") or "Piece")
        pkg = str(getattr(cfg, "alibaba_package_size", "") or "").strip()
        if pkg:
            trade["package_size"] = pkg
        weight = num("alibaba_weight", None)
        if weight and weight > 0:
            trade["weight"] = str(int(weight) if weight == int(weight) else round(weight, 2))
        hs = str(getattr(cfg, "alibaba_hs_code", "") or "").strip()
        if hs:
            rows = trade.get("tariffsHsCode") or []
            if not rows:
                rows = [{"toCountry": "US", "hsCode": hs}]
            for r in rows:
                if not r.get("hsCode"):
                    r["hsCode"] = hs
            trade["tariffsHsCode"] = rows
        stock = num("alibaba_stock", None)
        if stock and stock > 0:
            trade["inventory"] = [{"warehouseCode": "CN_LOCAL_01", "value": int(stock)}]
    save["wholesaleTrade"] = json.dumps(trade, ensure_ascii=False)

    group = str(getattr(cfg, "alibaba_group_id", "") or "").strip() if cfg is not None else ""
    if group:
        save["groupId"] = group
    if getattr(cfg, "alibaba_company_desc", "") if cfg is not None else "":
        # 公司介绍不走 product 保存接口，这里留痕给发布层（companyDesc 需要 schema 字段）
        save.setdefault("_company", {})["companyDesc"] = str(getattr(cfg, "alibaba_company_desc", "") or "")
    return save




GENERIC_VALUES = ("other", "others", "not applicable", "none", "no")


def autofill_required_attributes(product: dict, attr_defs: list | None, cfg=None,
                                 overrides: dict | None = None):
    """按类目 required 标记补齐必填属性：预设 > Other/China > 第一个可选值。

    返回 (attrs, filled, unresolved)
    """
    attrs = [deepcopy(a) for a in _as_list(product.get("attributes")) if isinstance(a, dict)]
    presets = {}
    raw = (overrides or {}).get("attributes") or _cfg_get(cfg, "alibaba_attr_presets", "") or ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            presets = {str(k).strip().lower(): v for k, v in json.loads(raw).items()}
        except Exception:
            presets = {}
    elif isinstance(raw, dict):
        presets = {str(k).strip().lower(): v for k, v in raw.items()}
    have = {str(a.get("attribute_name") or "").strip().lower() for a in attrs}
    filled, unresolved = [], []
    for d in attr_defs or []:
        if int(d.get("required") or 0) != 1:
            continue
        name = str(d.get("nameEn") or d.get("nameZh") or d.get("attribute_name") or "").strip()
        key = name.lower()
        if not name or key in have:
            continue
        values = _as_list(d.get("values"))
        val = presets.get(key)
        v_name = v_id = None
        if isinstance(val, dict):
            v_name, v_id = val.get("value_name") or val.get("name"), val.get("value_id") or val.get("id")
        elif val is not None:
            v_name = str(val)
        if not v_name:
            for cand in values:
                nm = str(cand.get("name_en") or cand.get("name") or "").strip()
                if nm.lower() in GENERIC_VALUES:
                    v_name, v_id = nm, cand.get("id"); break
        if not v_name and values:
            cand = values[0]
            v_name = str(cand.get("name_en") or cand.get("name") or "").strip()
            v_id = cand.get("id")
        if v_name:
            attrs.append({"attribute_id": str(d.get("attrNameId") or d.get("attribute_id") or "-1"),
                          "attribute_name": name,
                          "value_id": str(v_id if v_id is not None else "-1"),
                          "value_name": str(v_name)})
            have.add(key); filled.append(f"{name}={v_name}")
        else:
            unresolved.append(name)
    return attrs, filled, unresolved


def validate_alibaba_save(product: dict, save: dict, cfg=None,
                          required_attrs: list | None = None,
                          schema_fields: list | None = None) -> list[str]:
    """保存前校验：返回问题列表（空 = 通过）。必填项基于类目真实 required 标记。"""
    issues: list[str] = []
    subject = str(save.get("subject") or "").strip()
    if not subject:
        issues.append("产品名称（subject）为空")
    elif len(subject) > MAX_SUBJECT:
        issues.append(f"产品名称 {len(subject)} 字符，超过 {MAX_SUBJECT}")

    kws = [k for k in str(save.get("keywords") or "").split(",") if k.strip()]
    if len(kws) < MIN_KEYWORDS:
        issues.append(f"关键词 {len(kws)} 个，至少 {MIN_KEYWORDS} 个")
    elif len(kws) > MAX_KEYWORDS:
        issues.append(f"关键词 {len(kws)} 个，最多 {MAX_KEYWORDS} 个")

    imgs = [u for u in str(save.get("mainImage") or "").split(",") if u.strip()]
    if not imgs:
        issues.append("主图为空")
    elif len(imgs) > MAX_MAIN_IMAGES:
        issues.append(f"主图 {len(imgs)} 张，最多 {MAX_MAIN_IMAGES} 张")

    tiers = _as_list(save.get("bulkDiscountPrices"))
    if not tiers:
        issues.append("阶梯价为空")
    else:
        qs = [int(t.get("start_quantity") or 0) for t in tiers if isinstance(t, dict)]
        if qs != sorted(qs) or len(set(qs)) != len(qs):
            issues.append("阶梯价数量区间必须递增且不重复")
        if any(int(t.get("start_quantity") or 0) <= 0 for t in tiers if isinstance(t, dict)):
            issues.append("阶梯价起订数量必须 > 0")

    try:
        trade = json.loads(save.get("wholesaleTrade") or "{}")
    except Exception:
        trade = {}
    if int(trade.get("min_order_quantity") or 0) <= 0:
        issues.append("MOQ（min_order_quantity）必须 > 0")
    if trade.get("package_size") in (None, "", "0X0X0"):
        issues.append("包装尺寸 package_size 未填写")

    have = {str(a.get("attribute_name") or "").strip().lower()
            for a in _as_list(save.get("attributes")) if isinstance(a, dict)}
    for a in required_attrs or []:
        name = str(a.get("nameEn") or a.get("nameZh") or a.get("attribute_name") or "").strip()
        if name and name.lower() not in have:
            issues.append(f"必填属性缺失：{name}")

    field_ids = [f.get("fieldId") for f in (schema_fields or []) if isinstance(f, dict)]
    if "detailImage" in field_ids and not _as_list(save.get("detailImage")):
        issues.append("详情图片（detailImage）为空")
    if "textDesc" in field_ids and not str(save.get("textDesc") or "").strip():
        issues.append("商品卖点/详情文字（textDesc）为空")
    return issues

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


def split_keywords(raw, limit: int = MAX_KEYWORDS) -> list[str]:
    """把 keywords 字符串（逗号/分号/列表）按「短语」拆开并去重。

    与 clean_keywords 不同：这里保留短语原样（"fiberglass pool" 不会被拆成
    fiberglass/pool），用于保留商品原有语义。
    """
    if isinstance(raw, (list, tuple, set)):
        rows = [str(x).strip() for x in raw]
    else:
        s = str(raw or "").strip()
        if not s:
            return []
        sep = ";" if ("," not in s and ";" in s) else ","
        rows = [x.strip() for x in s.split(sep)]
    out, seen = [], set()
    for x in rows:
        key = x.lower()
        if x and key not in seen:
            seen.add(key)
            out.append(x)
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


def keywords_effective(save: dict, cfg=None, override: list | str | None = None) -> list[str]:
    """关键词优先级：overrides.keywords > cfg.alibaba_keywords > 商品原 keywords > 标题提取。

    之前无配置时直接用标题提取，把商品原有关键词整个覆盖掉（如
    "fiberglass pool, container pool" → "fiberglass, swimming, pool"）。这里保留
    商品原有短语，只在完全没有关键词时才从标题生成。
    """
    if override:
        kws = split_keywords(override)
        if kws:
            return kws
    cfg_kws = split_keywords(_cfg_get(cfg, "alibaba_keywords", "") or "")
    if cfg_kws:
        return cfg_kws
    prod_kws = split_keywords(save.get("keywords") or "")
    if prod_kws:
        return prod_kws
    return clean_keywords(save.get("subject", ""), MAX_KEYWORDS)


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


def resolve_category(product: dict, cfg=None, overrides: dict | None = None) -> tuple[str, str]:
    """确定本次保存使用的类目：(categoryId, fullCid)。

    优先级：overrides > cfg > 商品原值。只给了 categoryId 时，fullCid 用
    原 fullCid / cfg.fullCid 的路径前缀 + 新类目末段拼出来，避免「校验用新类目、
    保存用旧类目」这种 UI 选类目不生效的问题。
    """
    ov = dict(overrides or {})
    product = product or {}

    def _pick(*vals) -> str:
        for v in vals:
            if isinstance(v, (list, tuple)):
                s = ",".join(str(x).strip() for x in v if str(x).strip())
            else:
                s = str(v or "").strip()
            if s:
                return s
        return ""

    cid = _pick(ov.get("category_id"), ov.get("categoryId"),
                _cfg_get(cfg, "alibaba_category_id"), product.get("categoryId"))
    full = _pick(ov.get("full_cid"), ov.get("fullCid"),
                 _cfg_get(cfg, "alibaba_full_cid"), product.get("fullCid"))
    parts = [p.strip() for p in full.split(",") if p.strip()] if full else []
    if not cid and parts:
        cid = parts[-1]
    if cid:
        if parts and parts[-1] == cid:
            full = ",".join(parts)
        else:
            prefix = parts[:-1] if parts else []
            if not prefix:
                prod_full = str(product.get("fullCid") or "").strip()
                prod_parts = [p.strip() for p in prod_full.split(",") if p.strip()]
                prefix = prod_parts[:-1] if prod_parts else []
            full = ",".join(prefix + [cid])
    return cid, full


def build_alibaba_save(product: dict, cfg=None, overrides: dict | None = None,
                       client_cls=None, attr_defs: list | None = None) -> dict:
    """edit.json 商品 + AI 产物 → alibabaSave 字典。"""
    from ..integrations.alibaba import AlibabaClient as _AC
    cls = client_cls or _AC
    save = cls.build_save_dict(product)
    ov = dict(overrides or {})

    # 类目：overrides > cfg > 商品原值。UI 里选的类目必须真的写进保存包，
    # 否则会「按新类目校验、按旧类目保存」。
    cid, full_cid = resolve_category(product, cfg, ov)
    if cid:
        save["categoryId"] = cid
    if full_cid:
        save["fullCid"] = full_cid

    subject = str(ov.get("subject") or save.get("subject") or "").strip()
    save["subject"] = subject[:MAX_SUBJECT]

    kws = keywords_effective(save, cfg, ov.get("keywords"))
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

    # 价格类型：sku_price 按规格 / ladder_price 按数量 / fob_price 单一区间
    # 没有显式覆盖时不能把商品原本的定价模型静默改掉（sku_price→空阶梯价、
    # fob_price→阶梯价）。只有用户在 UI 显式选过定价方式（alibaba_price_type_explicit）
    # 或给了阶梯价档位/overrides 时，才以配置为准。
    _VALID_PTYPES = {"sku_price", "ladder_price", "fob_price"}
    prod_ptype = str(product.get("priceType") or "").strip()
    cfg_ptype = str(_cfg_get(cfg, "alibaba_price_type", "") or "").strip()
    ov_ptype = str(ov.get("price_type") or "").strip()
    cfg_tiers = ov.get("price_tiers") or _cfg_get(cfg, "alibaba_price_tiers", "") or ""
    _explicit = str(_cfg_get(cfg, "alibaba_price_type_explicit", "") or "").strip().lower() \
        in ("1", "true", "yes", "on")
    # 只有「没选过 + 配置仍是默认 ladder_price + 没填阶梯档位」才算隐式默认，
    # 这时跟随商品原定价模型；配置里明确写了 sku_price/fob_price 则尊重配置。
    _implicit_default = (not _explicit) and (not cfg_tiers) and cfg_ptype in ("", "ladder_price")
    if ov_ptype:
        ptype = ov_ptype
    elif _explicit or cfg_tiers:
        ptype = cfg_ptype or (prod_ptype if prod_ptype in _VALID_PTYPES else "ladder_price")
    elif prod_ptype in _VALID_PTYPES and _implicit_default:
        # 跟随商品原有定价模型（更新已有商品最安全）
        ptype = prod_ptype
    else:
        ptype = cfg_ptype or (prod_ptype if prod_ptype in _VALID_PTYPES else "ladder_price")
    tiers = price_tiers(cfg, ov)
    if ptype == "fob_price":
        fob = {
            "fob_unit_type": str(_cfg_get(cfg, "alibaba_unit_type", "") or ""),
            "fob_currency": str(_cfg_get(cfg, "alibaba_fob_currency", "USD") or "USD"),
            "fob_min_price": _num(_cfg_get(cfg, "alibaba_fob_min_price", "")),
            "fob_max_price": _num(_cfg_get(cfg, "alibaba_fob_max_price", "")),
            "min_order_quantity": int(_num(_cfg_get(cfg, "alibaba_min_order_quantity", 1)) or 1),
            "min_order_unit_type": str(_cfg_get(cfg, "alibaba_unit_type", "") or ""),
            "sale_type": str(_cfg_get(cfg, "alibaba_sale_type", "normal") or "normal"),
        }
        save["priceType"] = "fob_price"
        save["sourcingTrade"] = json.dumps(fob, ensure_ascii=False)
        # 规则（docs/ALIBABA_RULES.md §4）：FOB 模式不写阶梯价、不写 tariffsHsCode。
        save["bulkDiscountPrices"] = json.dumps([], ensure_ascii=False)
    elif ptype == "sku_price":
        # 按规格定价：保留商品原 productSku，不写阶梯价。
        save["priceType"] = "sku_price"
        save["bulkDiscountPrices"] = json.dumps([], ensure_ascii=False)
    elif tiers:
        save["priceType"] = "ladder_price"
        save["bulkDiscountPrices"] = json.dumps(tiers, ensure_ascii=False)
    else:
        save["priceType"] = ptype

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
        sale_type = str(getattr(cfg, "alibaba_sale_type", "") or "normal")
        trade["sale_type"] = sale_type
        batch = str(getattr(cfg, "alibaba_batch_number", "") or "").strip()
        trade["batch_number"] = int(batch) if sale_type == "batch" and batch.isdigit() else None
        freight_id = str(getattr(cfg, "alibaba_freight_template_id", "") or "").strip()
        if freight_id:
            trade["shipping_line_template_id"] = freight_id
        pkg = str(getattr(cfg, "alibaba_package_size", "") or "").strip()
        if pkg:
            trade["package_size"] = pkg
        weight = num("alibaba_weight", None)
        if weight and weight > 0:
            trade["weight"] = str(int(weight) if weight == int(weight) else round(weight, 2))
        hs = str(getattr(cfg, "alibaba_hs_code", "") or "").strip()
        if hs and ptype != "fob_price":
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
    if ptype == "fob_price":
        # FOB 下不允许带 HS 关税行（前端逻辑：tariffsHsCodeConfig && !fob && !overseaSpot）
        trade.pop("tariffsHsCode", None)
    save["wholesaleTrade"] = json.dumps(trade, ensure_ascii=False)

    group = str(getattr(cfg, "alibaba_group_id", "") or "").strip() if cfg is not None else ""
    if group:
        save["groupId"] = group
    if getattr(cfg, "alibaba_company_desc", "") if cfg is not None else "":
        # 公司介绍不走 product 保存接口，这里留痕给发布层（companyDesc 需要 schema 字段）
        save.setdefault("_company", {})["companyDesc"] = str(getattr(cfg, "alibaba_company_desc", "") or "")
    return save




def _num(v, default=None):
    try:
        return float(v) if v not in (None, "") else default
    except Exception:
        return default


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


_INVALID_CATEGORY_VALUES = {"none", "null", "nan", "undefined"}


def _json_container(v):
    """把 save 里的 JSON 字符串/对象统一成 dict/list；解析失败返回 None。"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def sku_summary(product_sku) -> tuple[bool, int, bool]:
    """productSku 体检：返回 (是否有数据, 有效 SKU 数, 是否有 SKU 维度属性)。

    真实 edit.json 的 productSku 是 JSON 字符串：
    {"attributes": [...], "special_skus": [{"price":..., "sku_code":..., "attributes":[...]}]}
    """
    obj = _json_container(product_sku)
    if obj is None:
        return False, 0, False
    if isinstance(obj, list):
        rows, dims = obj, False
    elif isinstance(obj, dict):
        rows = obj.get("special_skus") or obj.get("skus") or obj.get("sku_list") or []
        dims = bool(obj.get("attributes"))
    else:
        return False, 0, False
    if not isinstance(rows, list):
        rows = []
    valid = 0
    for r in rows:
        if not isinstance(r, dict) or r.get("exclude"):
            continue
        try:
            price = float(r.get("price") or r.get("sku_price") or 0)
        except Exception:
            price = 0
        has_code = bool(str(r.get("sku_code") or r.get("code") or "").strip())
        attrs = r.get("attributes") or []
        has_attr = isinstance(attrs, list) and any(
            isinstance(a, dict) and (a.get("attribute_name") or a.get("value_name")) for a in attrs)
        if price > 0 and (has_code or has_attr):
            valid += 1
    return True, valid, dims


def validate_alibaba_save(product: dict, save: dict, cfg=None,
                          required_attrs: list | None = None,
                          schema_fields: list | None = None) -> list[str]:
    """保存前校验：返回问题列表（空 = 通过）。必填项基于类目真实 required 标记。"""
    issues: list[str] = []
    # 类目合法性与脏值：9466975 有 27 个草稿 categoryId 是空/None，批量保存会把脏值提交。
    cid = str(save.get("categoryId") or "").strip()
    if not cid or cid.lower() in _INVALID_CATEGORY_VALUES:
        issues.append("类目（categoryId）为空或非法值，请先选择末级类目")
    elif not cid.isdigit() or int(cid) <= 0:
        issues.append(f"类目（categoryId={cid}）不是有效数字，请重新选择末级类目")
    else:
        full = str(save.get("fullCid") or "").strip()
        parts = [p.strip() for p in full.split(",") if p.strip()]
        if parts and parts[-1] != cid:
            issues.append(f"fullCid 末段（{parts[-1]}）与 categoryId（{cid}）不一致，请重新选择类目")
        if any(p.lower() in _INVALID_CATEGORY_VALUES or not p.isdigit() for p in parts):
            issues.append(f"fullCid 含非法类目路径段：{full or '空'}")

    subject = str(save.get("subject") or "").strip()
    if not subject:
        issues.append("产品名称（subject）为空")
    elif len(subject) > MAX_SUBJECT:
        issues.append(f"产品名称 {len(subject)} 字符，超过 {MAX_SUBJECT}")

    kws = [k for k in str(save.get("keywords") or "").split(",") if k.strip()]
    if not kws:
        issues.append("关键词为空（至少 1 个，建议 3~5 个）")
    elif len(kws) > MAX_KEYWORDS:
        issues.append(f"关键词 {len(kws)} 个，最多 {MAX_KEYWORDS} 个")

    imgs = [u for u in str(save.get("mainImage") or "").split(",") if u.strip()]
    if not imgs:
        issues.append("主图为空")
    elif len(imgs) > MAX_MAIN_IMAGES:
        issues.append(f"主图 {len(imgs)} 张，最多 {MAX_MAIN_IMAGES} 张")

    ptype = str(save.get("priceType") or "ladder_price")
    tiers = _as_list(save.get("bulkDiscountPrices"))
    if ptype == "fob_price":
        try:
            fob = json.loads(save.get("sourcingTrade") or "{}")
        except Exception:
            fob = {}
        if not fob.get("fob_min_price") or not fob.get("fob_max_price"):
            issues.append("FOB 价格区间（fob_min_price / fob_max_price）未填写")
        elif float(fob.get("fob_min_price")) > float(fob.get("fob_max_price")):
            issues.append("FOB 最低价不能大于最高价")
        if not fob.get("fob_currency"):
            issues.append("FOB 币种未填写")
    elif ptype == "sku_price":
        has_sku, valid_skus, has_dims = sku_summary(save.get("productSku"))
        if not has_sku:
            issues.append("按规格定价（sku_price）但商品缺少 SKU 数据（productSku 为空），"
                          "请补 SKU 或改用按数量定价")
        else:
            if not has_dims:
                issues.append("按规格定价（sku_price）但 productSku 缺少 SKU 维度属性（attributes）")
            if valid_skus == 0:
                issues.append("按规格定价（sku_price）但 productSku 无有效 SKU"
                              "（special_skus 为空、全部 exclude 或无价格/规格），"
                              "请补 SKU 或改用按数量定价")
    elif not tiers:
        issues.append("阶梯价为空（按数量定价必须有数量区间）")
    if tiers:
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
    if not _num(trade.get("weight")):
        issues.append("包装重量 weight 未填写")

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

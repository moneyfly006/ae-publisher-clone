"""Amazon 真实发布自动修复闭环 + 政策预检（生产用户流程）。

用户语义：**不做预演**，直接真实发布；发布失败时按 Amazon 返回的失败原因自动
修复（补属性 / 去重标题 / 清洗描述等）并自动重发，最多 ``max_rounds`` 轮。

安全边界
--------
- 本模块只走真实写（``op=2`` 保存并发布）；不做 dry-run 用户流程；
- ``AURORA_TEST`` / ``AURORA_NO_SAVE`` 仅保留为 CI 护栏：命中时直接返回 blocked，
  不调用任何写接口（单元测试也用它们防误写）；
- 价格/库存缺失或 ≤0 一律阻断，**不编造价格/库存**；属性值只来自用户输入、
  商品原文、同类目在线模板与确定性映射，不编造认证/参数。

核心接口
--------
``auto_publish(product_id, cfg, overrides=None, *, op="2", max_rounds=3,
wait_seconds=90, auto_fix_attrs=True, auto_fix_title=True,
auto_fix_description=True, auto_ai_fix=False, client=None) -> AutoPublishResult``

每轮：补齐必填属性 → 本地政策预检+自动修复 → op=2 真实发布 → 轮询发布状态 →
失败分类（B 类属性 / 100470 标题重复 / 内容违规 / 其它）→ 自动修下一轮或停止。
"""
from __future__ import annotations

import os
import re
import time

from ..integrations.amazon import AmazonError, parse_required_attrs
from .amazon_payload import (
    OVERRIDE_KEYS, REQUIRED_COMMON, attribute_present, parse_product_attr,
    has_contact_number, remove_contact_numbers,
)
from .amazon_pipeline import (
    OP_PUBLISH, OP_SAVE, _autofill_attributes_with_notes, prepare_save,
    required_attributes_from_schema, verify_publish,
)
from .amazon_prompts import (
    BULLET_MAX_CHARS, BULLET_MAX_ITEMS, DESCRIPTION_MAX_BYTES,
    SEARCH_TERMS_MAX_BYTES, SEARCH_TERMS_MAX_CHARS, TITLE_MAX,
    clean_bullet, clean_for_site, clean_title, has_chinese,
    parse_search_terms, remove_emoji, sanitize_description, site_meta,
    strip_html,
)
from .amazon_publish import make_client, save_to_amazon

# ============================= 结果对象 =============================
class AutoPublishResult(dict):
    """可序列化的自动发布结果（dict 子类，同时支持 ``result.ok`` 属性访问）。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - 属性拼错时才触发
            raise AttributeError(name) from exc

    def to_dict(self) -> dict:
        return dict(self)


def _new_result(product_id: str, op: str) -> AutoPublishResult:
    return AutoPublishResult({
        "ok": False,
        "product_id": str(product_id or ""),
        "op": str(op or OP_PUBLISH),
        "saved": False,
        "saved_round": 0,
        "attempts": 0,
        "rounds": [],
        "fix_log": [],
        "blockers": [],
        "warnings": [],
        "attribute_notes": [],
        "final_state": "",
        "publish_status": "",
        "publish_error": "",
        "asin": "",
        "detail": "",
        "required_attrs": [],
        "elapsed": 0.0,
    })


# ============================= 文本清洗规则 =============================
_PROMO_RE = re.compile(
    r"(?:\bbest\b|\bcheapest\b|\blowest\s+price\b|\bfree\s+shipping\b|\bhot\s+sale\b|"
    r"\bdiscount\b|\bsale\b|\bpromotion\b|\bcashback\b|\bguarantee(?:d)?\b|"
    r"100\s*%|\bno\.?\s*1\b|\bnumber\s+one\b|\bmiracle\b|\bcure\b|"
    r"\bworld['’]?s\s+best\b|\btop\s+rated\b|#1)",
    re.I)
_MEDICAL_ABS_RE = re.compile(
    r"(?:\bmedical\s+grade\b|\bfda\s+approved\b|\bclinically\s+proven\b|"
    r"\bcures?\b|\btreats?\s+disease\b|\b100\s*%\s*(?:safe|effective)\b)",
    re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_PHONE_RE = re.compile(r"(?<![\d.])[+]?\d[\d\s\-()]{7,}\d(?![\d.])")
_MESSAGING_RE = re.compile(
    r"\b(?:whatsapp|wechat|weixin|skype|telegram|viber)\b", re.I)
_CONTACT_RE = re.compile(r"\b(?:contact\s+(?:us|seller)|call\s+us)\b", re.I)
# 中文联系方式/导流词（采集来源常把「电话/微信/联系」写进描述）
_CN_CONTACT_RE = re.compile(
    r"(?:联系电话|联系方式|电话|手机|微信|微店|QQ|邮箱|网址|二维码|客服|联系人|"
    r"阿里旺旺|旺旺)\s*[:：]?\s*[^\s<>，。;；、]{0,40}", re.I)
# 中文促销/绝对化词
_CN_PROMO_RE = re.compile(
    r"(?:厂家直销|厂家直供|现货供应|现货|包邮|特价|促销|最低价|批发价|"
    r"保证质量|质量保证|100%|百分百|第一品牌|全网最低)", re.I)

_TITLE_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_ANY_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
_ALLOWED_DESC_TAGS = {"p", "ul", "li", "b", "br"}


def _strip_forbidden(text: str, *, keep_numbers: bool = True) -> str:
    """删除促销/联系方式/医疗绝对化/站外链接；不改动正常参数。"""
    s = str(text or "")
    s = _PROMO_RE.sub(" ", s)
    s = _MEDICAL_ABS_RE.sub(" ", s)
    s = _EMAIL_RE.sub(" ", s)
    s = _URL_RE.sub(" ", s)
    s = _MESSAGING_RE.sub(" ", s)
    s = _CONTACT_RE.sub(" ", s)
    s = _CN_CONTACT_RE.sub(" ", s)
    s = _CN_PROMO_RE.sub(" ", s)
    s = remove_contact_numbers(s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _brand_tokens(product: dict | None) -> list[str]:
    """品牌/制造商/型号里不应出现在 Search Terms 的词（去重、忽略空与 Generic）。"""
    product = product or {}
    out: list[str] = []
    for key in ("brand", "manufacturer", "brandName", "manufacturerName"):
        value = str(product.get(key) or "").strip()
        if len(value) >= 2 and value.lower() not in ("generic", "none", "null"):
            if value.lower() not in out:
                out.append(value.lower())
    return out


def _is_all_caps(text: str) -> bool:
    letters = [c for c in str(text or "") if c.isalpha()]
    if len(letters) < 6:
        return False
    upper = sum(1 for c in letters if c.isupper())
    return upper / len(letters) >= 0.9 and not any(c.islower() for c in letters)


def _fix_all_caps(text: str) -> str:
    def cap(match: re.Match) -> str:
        word = match.group(0)
        return word[:1].upper() + word[1:].lower() if word.isupper() else word
    return re.sub(r"\b[A-Z]{3,}\b", cap, str(text or ""))


def dedupe_title_words(title: str, max_repeat: int = 2) -> str:
    """标题同词去重：同一实词最多保留 ``max_repeat`` 次，其余删除。"""
    limit = max(1, int(max_repeat or 2))
    words = str(title or "").split()
    counts: dict[str, int] = {}
    out: list[str] = []
    for word in words:
        key_match = _TITLE_WORD_RE.search(word)
        key = (key_match.group(0).lower() if key_match else word.lower().strip(".,;:!?"))
        if not key or len(key) <= 1:
            out.append(word)
            continue
        if counts.get(key, 0) >= limit:
            continue
        counts[key] = counts.get(key, 0) + 1
        out.append(word)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def fix_title_repetition(title: str, max_repeat: int = 2,
                         banned_word: str = "") -> str:
    """标题重复修复：可选移除指定重复词，再全局同词去重并清洗。"""
    s = str(title or "")
    banned = str(banned_word or "").strip()
    if banned:
        pattern = re.compile(rf"\b{re.escape(banned)}\b", re.I)
        seen = 0

        def _keep(match: re.Match) -> str:
            nonlocal seen
            seen += 1
            return match.group(0) if seen <= max(1, int(max_repeat or 2)) else ""
        s = pattern.sub(_keep, s)
    before = str(title or "")
    s = dedupe_title_words(s, max_repeat)
    fixed = sanitize_title(s)
    # 服务端仍报同一重复词（可能只出现 2 次就被判重复）：再收紧到 1 次
    if banned and fixed == sanitize_title(before):
        pattern = re.compile(rf"\b{re.escape(banned)}\b", re.I)
        seen = 0

        def _keep_one(match: re.Match) -> str:
            nonlocal seen
            seen += 1
            return match.group(0) if seen <= 1 else ""
        fixed = sanitize_title(pattern.sub(_keep_one, before))
    return fixed


def sanitize_title(title: str, limit: int = TITLE_MAX) -> str:
    """标题清洗：去 emoji/HTML/促销/联系方式/URL，全大写转正常大小写，按词截断。"""
    s = _strip_forbidden(remove_emoji(strip_html(title)))
    if _is_all_caps(s):
        s = _fix_all_caps(s)
    s = clean_title(s, limit)
    return s


def sanitize_bullets(bullets, limit: int = BULLET_MAX_CHARS,
                     max_items: int = BULLET_MAX_ITEMS) -> list[str]:
    """五点清洗：去 HTML/emoji/±/●/促销/联系方式，超长截断、超出条数截断。"""
    if isinstance(bullets, str):
        items = [b for b in bullets.split("±")]
    elif isinstance(bullets, (list, tuple)):
        items = list(bullets)
    else:
        items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = _strip_forbidden(clean_bullet(item, limit))
        clean = clean.strip(" -；;")
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
        if len(out) >= max_items:
            break
    return out


def sanitize_description(html: str, max_bytes: int = DESCRIPTION_MAX_BYTES) -> str:
    """描述清洗：去促销/联系方式/URL/医疗绝对化 → HTML 白名单 → 字节截断。"""
    raw = str(html or "")
    raw = _strip_forbidden(raw)
    raw = _MEDICAL_ABS_RE.sub(" ", raw)
    from .amazon_prompts import sanitize_description as _sanitize_html
    return _sanitize_html(raw, max_bytes)




def fallback_description(title: str, bullets=None) -> str:
    """描述被清洗到过短时，用标题+卖点生成最小合规 HTML 描述（不编造参数）。"""
    head = re.sub(r"\s+", " ", str(title or "")).strip()[:180]
    items = []
    for b in (bullets or []):
        text = re.sub(r"\s+", " ", str(b or "")).strip()
        if text:
            items.append(text[:220])
        if len(items) >= 5:
            break
    parts = []
    if head:
        parts.append(f"<p>{head}.</p>")
    if items:
        parts.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
    parts.append("<p>Please refer to the product title and specifications for details.</p>")
    return "".join(parts)


def sanitize_search_terms(terms: str, max_bytes: int = SEARCH_TERMS_MAX_BYTES,
                          max_chars: int = SEARCH_TERMS_MAX_CHARS,
                          banned=()) -> str:
    """Search Terms 清洗：小写去重、去标点/停用词/促销词/品牌词，双上限截断。"""
    cleaned = parse_search_terms(_strip_forbidden(remove_emoji(terms)), max_bytes)
    cleaned = _PROMO_RE.sub(" ", cleaned)
    for token in banned or ():
        name = str(token or "").strip().lower()
        if name:
            cleaned = re.sub(rf"\b{re.escape(name)}\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned.encode("utf-8")) > max_bytes:
        while cleaned and len(cleaned.encode("utf-8")) > max_bytes:
            cleaned = cleaned.rsplit(" ", 1)[0] if " " in cleaned else cleaned[:-1]
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
    return cleaned


def sanitize_sku(sku: str) -> str:
    from .amazon_prompts import clean_sku
    return clean_sku(sku)


# ============================= 政策预检 =============================
# schema required 里由保存包其它字段承载、或属于多变体/条件字段的名字（不按属性缺失去阻断）
_PAYLOAD_MAPPED_ATTRS = {
    "parentage_level", "child_parent_sku_relationship", "variation_theme",
    "item_name", "title_differentiation", "brand", "manufacturer",
    "product_description", "bullet_point", "generic_keyword",
    "recommended_browse_nodes", "externally_assigned_product_identifier",
    "merchant_suggested_asin", "main_product_image_locator",
    "swatch_product_image_locator", "package_level", "package_contains_sku",
    "condition_type", "condition_note", "list_price", "merchant_shipping_group",
    "fulfillment_availability", "purchasable_offer", "skip_offer", "map_policy",
    "main_offer_image_locator", "gpsr_safety_attestation",
    "gpsr_manufacturer_reference", "dsa_responsible_party_address",
}
_CONDITIONAL_ATTR_RE = re.compile(
    r"^(?:other_product_image|other_offer_image|parentage|child_|variation|"
    r"package_|number_of_(?:items|boxes|lithium)|item_package_quantity|"
    r"supplier_declared_has_product_identifier_exemption)")


def filter_schema_required(names) -> list[str]:
    """过滤 schema required 噪声：去掉保存包已承载/多变体/条件属性。"""
    out: list[str] = []
    for name in names or []:
        key = str(name or "").strip()
        if not key or key in out:
            continue
        if key in _PAYLOAD_MAPPED_ATTRS:
            continue
        if _CONDITIONAL_ATTR_RE.match(key):
            continue
        out.append(key)
    return out


def _cfg_dict(cfg) -> dict:
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return dict(cfg)
    if hasattr(cfg, "as_dict"):
        try:
            return dict(cfg.as_dict())
        except Exception:
            return {}
    return {}


def _cfg_value(cfg, key: str, default=None):
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _required_list_from_cfg(cfg) -> list[str]:
    raw = _cfg_value(cfg, "_preflight_required", None)
    if raw is None:
        raw = _cfg_value(cfg, "amazon_last_category_required", "") or ""
    if isinstance(raw, str):
        parts = re.split(r"[,;，；\s]+", raw)
    else:
        parts = list(raw or [])
    out: list[str] = []
    for item in parts:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _payload_attribute_value(payload: dict, key: str) -> bool:
    """必填属性是否已由 productAttr 或保存包字段承载。"""
    mapped = {
        "item_name": "title", "brand": "brand", "manufacturer": "manufacturer",
        "product_description": "description", "bullet_point": "bulletPoint",
        "generic_keyword": "searchTerms", "recommended_browse_nodes": "categoryId",
        "externally_assigned_product_identifier": "standardProductId",
        "main_product_image_locator": "mainImage",
        "condition_type": "conditionType",
        "merchant_shipping_group": "merchantShippingGroup",
        "gpsr_safety_attestation": "gpsrSafetyAttestation",
    }
    field = mapped.get(key)
    if field:
        value = payload.get(field)
        if isinstance(value, str):
            return bool(value.strip())
        return value not in (None, "", 0, {})
    return attribute_present(parse_product_attr(payload.get("productAttr")), key)


def policy_preflight(payload: dict, product: dict | None, cfg) -> dict:
    """发布前政策预检：返回 ``blockers`` / ``warnings`` / ``autofix_actions``。

    - blockers：不可自动修复的阻断（缺价格/库存、缺类目/主图/品牌、必填属性缺失）；
    - autofix_actions：可由本地 fix_* / sanitize_* 自动修复的动作名；
    - warnings：建议项（不阻断）。
    """
    p = payload or {}
    cfgd = _cfg_dict(cfg)
    blockers: list[str] = []
    warnings: list[str] = []
    actions: list[str] = []

    def _act(name: str) -> None:
        if name not in actions:
            actions.append(name)

    mp = str(p.get("marketPlaceId") or "").strip()
    meta = site_meta(mp) if mp else {}
    lang = str(meta.get("lang") or "en").lower()

    # ---- 标题 ----
    title = str(p.get("title") or "").strip()
    if not title:
        blockers.append("标题为空")
    else:
        if len(title) > TITLE_MAX:
            warnings.append(f"标题超过 {TITLE_MAX} 字符（可自动截断）")
            _act("sanitize_title")
        words = [w.lower() for w in _TITLE_WORD_RE.findall(title)]
        repeated = [w for w in set(words) if words.count(w) > 2]
        if repeated:
            warnings.append("标题同词超过 2 次：" + ", ".join(sorted(repeated)[:5]))
            _act("dedupe_title_words")
        if _is_all_caps(title):
            warnings.append("标题全大写（可自动修正大小写）")
            _act("sanitize_title")
        if remove_emoji(title) != title or re.search(r"</?[a-zA-Z][^>]*>", title):
            warnings.append("标题含 emoji/HTML")
            _act("sanitize_title")
        if (_PROMO_RE.search(title) or _EMAIL_RE.search(title) or _URL_RE.search(title)
                or _MESSAGING_RE.search(title) or has_contact_number(title)
                or _CN_CONTACT_RE.search(title) or _CN_PROMO_RE.search(title)):
            warnings.append("标题含促销/联系方式/URL")
            _act("sanitize_title")
        if not lang.startswith("zh") and has_chinese(title):
            warnings.append("非中文站点标题含中文")
            _act("sanitize_title")

    # ---- 五点 ----
    bullets = [b.strip() for b in str(p.get("bulletPoint") or "").split("±") if b.strip()]
    if len(bullets) > BULLET_MAX_ITEMS:
        warnings.append(f"卖点超过 {BULLET_MAX_ITEMS} 条（可自动截断）")
        _act("sanitize_bullets")
    if 0 < len(bullets) < 3:
        warnings.append(f"卖点仅 {len(bullets)} 条（Amazon 建议 5 条）")
    for i, b in enumerate(bullets, 1):
        if len(b) > BULLET_MAX_CHARS:
            warnings.append(f"卖点 {i} 超过 {BULLET_MAX_CHARS} 字符（可自动截断）")
            _act("sanitize_bullets")
        if ("±" in b or "●" in b or re.search(r"</?[a-zA-Z][^>]*>", b)
                or remove_emoji(b) != b):
            warnings.append(f"卖点 {i} 含 ±/●/HTML/emoji")
            _act("sanitize_bullets")
        if (_PROMO_RE.search(b) or _EMAIL_RE.search(b) or _URL_RE.search(b)
                or _MESSAGING_RE.search(b) or has_contact_number(b)
                or _CN_CONTACT_RE.search(b) or _CN_PROMO_RE.search(b)):
            warnings.append(f"卖点 {i} 含促销/联系方式/URL")
            _act("sanitize_bullets")
        if not lang.startswith("zh") and has_chinese(b):
            warnings.append(f"卖点 {i} 含中文（非中文站点）")
            _act("sanitize_bullets")

    # ---- 描述 ----
    desc = str(p.get("description") or "")
    if not strip_html(desc).strip():
        blockers.append("产品描述为空")
    else:
        _desc_text = strip_html(desc)
        if (_PROMO_RE.search(_desc_text) or _EMAIL_RE.search(_desc_text)
                or _URL_RE.search(_desc_text) or _MESSAGING_RE.search(_desc_text)
                or has_contact_number(_desc_text) or _CN_CONTACT_RE.search(_desc_text)
                or _CN_PROMO_RE.search(_desc_text)):
            warnings.append("描述含促销/联系方式/URL（可自动清洗）")
            _act("sanitize_description")
        if not lang.startswith("zh") and has_chinese(_desc_text):
            warnings.append("描述含中文（非中文站点，可自动清洗）")
            _act("sanitize_description")
        if re.search(r"<\s*(script|style|iframe|img|object|embed)\b", desc, re.I):
            warnings.append("描述含脚本/样式/框架/图片标签（可自动清洗）")
            _act("sanitize_description")
        extra_tags = sorted({tag.lower() for tag in _ANY_TAG_RE.findall(desc)
                             if tag.lower() not in _ALLOWED_DESC_TAGS})
        if extra_tags:
            warnings.append("描述含白名单外标签 "
                            + ", ".join(extra_tags[:6]) + "（可自动清洗）")
            _act("sanitize_description")
        if len(desc.encode("utf-8")) > DESCRIPTION_MAX_BYTES:
            warnings.append("描述超过字节上限（可自动截断）")
            _act("sanitize_description")
        plain = strip_html(desc)
        if (_PROMO_RE.search(plain) or _EMAIL_RE.search(plain) or _URL_RE.search(plain)
                or _MESSAGING_RE.search(plain) or _MEDICAL_ABS_RE.search(plain)):
            warnings.append("描述含促销/联系方式/URL/医疗绝对化表述（可自动清洗）")
            _act("sanitize_description")
        if not lang.startswith("zh") and has_chinese(plain):
            warnings.append("描述含中文（非中文站点）")
            _act("sanitize_description")

    # ---- Search Terms ----
    terms = str(p.get("searchTerms") or "")
    if terms:
        if len(terms) > SEARCH_TERMS_MAX_CHARS or \
                len(terms.encode("utf-8")) > SEARCH_TERMS_MAX_BYTES:
            warnings.append("Search Terms 超长（可自动截断）")
            _act("sanitize_search_terms")
        if re.search(r"[^a-z0-9\s-]", terms.lower()):
            warnings.append("Search Terms 含标点/非法字符（可自动清洗）")
            _act("sanitize_search_terms")
        if _PROMO_RE.search(terms):
            warnings.append("Search Terms 含促销词（可自动清洗）")
            _act("sanitize_search_terms")
        brand_tokens = _brand_tokens(product)
        low_terms = terms.lower()
        if any(re.search(rf"\b{re.escape(t)}\b", low_terms) for t in brand_tokens):
            warnings.append("Search Terms 含品牌词（可自动清洗）")
            _act("sanitize_search_terms")

    # ---- SKU ----
    sku = str(p.get("sku") or "").strip()
    if not sku:
        blockers.append("SKU 为空")
    elif len(sku) > 40 or re.search(r"[^A-Za-z0-9_-]", sku):
        warnings.append("SKU 超长或含非法字符（可自动清洗）")
        _act("sanitize_sku")

    # ---- 价格 / 库存（绝不编造） ----
    try:
        price = float(p.get("standardPrice"))
    except Exception:
        price = 0.0
    if price <= 0:
        blockers.append("价格缺失或≤0（不自动编造，需用户填写）")
    try:
        qty = int(float(p.get("quantity") or 0))
    except Exception:
        qty = 0
    if qty <= 0:
        blockers.append("库存为 0（不自动编造，需用户填写）")

    # ---- 必备基础字段 ----
    main_image = str(p.get("mainImage") or "").strip()
    if not re.match(r"^https?://", main_image, re.I):
        blockers.append("主图缺失或不是 http(s) 地址")
    if not str(p.get("categoryId") or "").strip():
        blockers.append("类目为空")
    if not str(p.get("categoryBrowsePath") or "").strip():
        warnings.append("类目路径为空（发布可能被拒）")
    if not str(p.get("brand") or "").strip():
        blockers.append("品牌为空")
    if not str(p.get("manufacturer") or "").strip():
        blockers.append("制造商为空")
    if not mp:
        blockers.append("站点 marketPlaceId 为空")

    # ---- 必填属性完整性（common + 显式 required + schema） ----
    required = list(REQUIRED_COMMON) + _required_list_from_cfg(cfg)
    missing_required = [k for k in dict.fromkeys(required)
                        if not _payload_attribute_value(p, k)]
    if missing_required:
        blockers.append("必填属性缺失：" + ", ".join(missing_required))
    schema_required = filter_schema_required(cfgd.get("_schema_required") or [])
    strict = bool(cfgd.get("_schema_required_strict", False))
    if schema_required:
        missing_schema = [k for k in schema_required
                          if not _payload_attribute_value(p, k)]
        if strict and missing_schema:
            blockers.append("类目 schema 必填属性缺失：" + ", ".join(missing_schema))
        elif missing_schema:
            warnings.append("schema 建议属性未填（不阻断）：" + ", ".join(missing_schema[:12]))
    return {"blockers": blockers, "warnings": warnings,
            "autofix_actions": actions}


# ============================= 自动发布闭环 =============================
_ENV_GUARDS = ("AURORA_NO_SAVE", "AURORA_TEST")
_CONTENT_ERR_HINTS = ("促销", "联系方式", "绝对化", "医疗", "大写", "描述",
                      "内容", "违规", "禁止", "敏感", "attribute", "invalid",
                      "listing", "content")
_TITLE_REPEAT_HINTS = ("100470", "重复", "duplicate", "repeated")
_ATTR_ERR_HINTS = ("90220", "属性:", "必填", "required")


def _forced_preview() -> bool:
    """CI 护栏：只有显式设置 env 时才拒绝真实写。"""
    return any(os.environ.get(name) for name in _ENV_GUARDS)


def _product_of(detail: dict) -> dict:
    if not isinstance(detail, dict):
        return {}
    product = detail.get("product")
    if isinstance(product, dict) and product:
        return dict(product)
    return dict(detail)


def _merge_unique(base: list[str], extra) -> list[str]:
    out = list(base)
    for item in extra or []:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _remove_cjk(text: str) -> str:
    """删除中文/全角字符（非中文站点发布前必须清洗；不编造内容）。"""
    s = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf]+", " ", str(text or ""))
    s = re.sub(r"[\u3000-\u303f\uff00-\uffef]", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _apply_local_fixes(ov: dict, actions: list[str], payload: dict,
                       apply_title: bool, apply_description: bool,
                       product: dict | None = None) -> list[str]:
    """按预检动作修改 overrides；返回实际执行的修复动作。"""
    done: list[str] = []
    acts = set(actions)
    if "sanitize_title" in acts or "dedupe_title_words" in acts:
        if apply_title:
            title_now = payload.get("title") or ov.get("title") or ""
            if "dedupe_title_words" in acts:
                fixed = fix_title_repetition(title_now, max_repeat=2)
                done.append("dedupe_title_words")
            else:
                fixed = sanitize_title(title_now)
            mp = str((product or {}).get("marketPlaceId") or "")
            lang = str((site_meta(mp) if mp else {}).get("lang") or "en").lower()
            if not lang.startswith("zh"):
                fixed = sanitize_title(_remove_cjk(fixed))
            ov["title"] = fixed
            if "sanitize_title" in acts and "sanitize_title" not in done:
                done.append("sanitize_title")
    if "sanitize_bullets" in acts:
        fixed = sanitize_bullets(payload.get("bulletPoint") or ov.get("bullets") or [])
        mp = str((product or {}).get("marketPlaceId") or "")
        lang = str((site_meta(mp) if mp else {}).get("lang") or "en").lower()
        if not lang.startswith("zh"):
            fixed = [sanitize_bullets(_remove_cjk(x))[0] if _remove_cjk(x)
                     else "" for x in fixed]
            fixed = [x for x in fixed if x]
        ov["bullets"] = fixed
        done.append("sanitize_bullets")
    if "sanitize_description" in acts:
        if apply_description:
            fixed = sanitize_description(payload.get("description") or "")
            mp = str((product or {}).get("marketPlaceId") or "")
            lang = str((site_meta(mp) if mp else {}).get("lang") or "en").lower()
            if not lang.startswith("zh"):
                fixed = sanitize_description(_remove_cjk(strip_html(fixed)))
            # 清洗后过短 → 标题+卖点兜底，避免平台判「描述为空/不完整」
            try:
                from .amazon_payload import strip_html as _strip_html
                if len(_strip_html(fixed)) < 60:
                    fixed = fallback_description(
                        str(ov.get("title") or payload.get("title") or ""),
                        [str(b) for b in (ov.get("bullets") or [])]
                        or [b for b in str(payload.get("bulletPoint") or "").split("±") if b])
                    done.append("fallback_description")
            except Exception:
                pass
            ov["description"] = fixed
            done.append("sanitize_description")
    if "sanitize_search_terms" in acts:
        terms = payload.get("searchTerms") or ""
        mp = str((product or {}).get("marketPlaceId") or "")
        lang = str((site_meta(mp) if mp else {}).get("lang") or "en").lower()
        if not lang.startswith("zh"):
            terms = _remove_cjk(terms)
        ov["search_terms"] = sanitize_search_terms(
            terms, banned=_brand_tokens(product))
        done.append("sanitize_search_terms")
    if "sanitize_sku" in acts:
        ov["sku"] = sanitize_sku(payload.get("sku") or ov.get("sku") or "")
        done.append("sanitize_sku")
    return done


def _preflight_cfg(cfg, required: list[str], schema_required: list[str],
                   strict_schema: bool) -> dict:
    view = _cfg_dict(cfg)
    view["_preflight_required"] = list(required)
    view["_schema_required"] = list(schema_required)
    view["_schema_required_strict"] = bool(strict_schema)
    return view


def _round_record(round_no: int, op: str, state: str, status: str, err: str,
                  fixes: list[str], blockers: list[str], warnings: list[str],
                  elapsed: float, saved: bool, asin: str = "") -> dict:
    return {
        "round": round_no, "op": op, "publish_state": state, "status": status,
        "err": str(err or "")[:600], "fixes": list(fixes),
        "blockers": list(blockers), "warnings": list(warnings)[:20],
        "elapsed": round(elapsed, 2), "saved": bool(saved), "asin": str(asin or ""),
    }


def auto_publish(product_id: str, cfg, overrides: dict | None = None, *,
                 op: str = OP_PUBLISH, max_rounds: int = 3,
                 wait_seconds: int = 90, auto_fix_attrs: bool = True,
                 auto_fix_title: bool = True, auto_fix_description: bool = True,
                 auto_ai_fix: bool = False, client=None) -> AutoPublishResult:
    """真实发布 + 失败自动修复重发（最多 ``max_rounds`` 轮）。

    成功 = 发布状态 online / reviewing / processing；失败返回 ok=False，
    ``rounds`` 里保留每轮状态与修复动作，``fix_log`` 汇总修复记录。
    """
    pid = str(product_id or "").strip()
    op = str(op or OP_PUBLISH)
    res = _new_result(pid, op)
    started = time.time()
    if not pid:
        res["blockers"] = ["缺少 product_id"]
        res["elapsed"] = 0.0
        return res
    if op not in (OP_SAVE, "3", OP_PUBLISH):
        res["blockers"] = [f"未知操作 op={op}（可选 1/2/3）"]
        return res
    # CI 护栏：测试环境只允许 dry-run 测试，绝不真实写
    if _forced_preview():
        res["blockers"] = ["AURORA_NO_SAVE/AURORA_TEST 生效：已拒绝真实发布（CI 护栏）"]
        res["detail"] = "预演/测试环境不执行真实发布"
        return res

    try:
        c = client if client is not None else make_client(cfg)
    except Exception as exc:  # noqa: BLE001
        res["blockers"] = [f"创建客户端失败：{str(exc)[:160]}"]
        return res

    try:
        detail = c.product_detail(pid)
    except Exception as exc:  # noqa: BLE001
        res["blockers"] = [f"读取商品失败：{str(exc)[:160]}"]
        return res
    product = _product_of(detail)
    if not product:
        res["blockers"] = [f"读不到商品 {pid}"]
        return res

    shop_id = str(product.get("shopId") or _cfg_value(cfg, "amazon_shop_id", "") or "")
    mp = str(product.get("marketPlaceId") or "").strip()
    category_id = str(product.get("categoryId") or "").strip()
    site_code = ""
    try:
        detail_site = detail.get("site") if isinstance(detail, dict) else ""
        site_code = str(detail_site or site_meta(mp).get("code") or "")
    except Exception:
        site_code = ""

    base: dict = {k: v for k, v in (overrides or {}).items()
                  if k in OVERRIDE_KEYS and v not in (None, "")}
    attrs: dict = dict(base.get("attributes") or {})
    explicit_required = _merge_unique(
        list(REQUIRED_COMMON),
        base.get("required_attributes") or _cfg_value(
            cfg, "amazon_last_category_required", "") or [])
    required = list(explicit_required)
    schema_required: list[str] = []
    strict_schema = False
    template: dict = {}
    if auto_fix_attrs:
        try:
            raw_schema = required_attributes_from_schema(
                category_id, str(product.get("productType") or ""), site_code,
                client=c)
            schema_required = filter_schema_required(raw_schema)
            # 提取结果噪声很大（含多变体/条件字段）：只有列表较小时才当作阻断
            strict_schema = 0 < len(schema_required) <= 25
        except Exception:
            schema_required = []
        try:
            template = c.attribute_template(category_id, mp, shop_id) or {}
        except Exception:
            template = {}

    previous_state = ""
    previous_err = ""
    for round_no in range(1, max(1, int(max_rounds or 1)) + 1):
        round_started = time.time()
        fixes: list[str] = []
        # 1) 属性自动补齐（只补缺失；用户显式 overrides 优先）
        if auto_fix_attrs:
            try:
                autofill_required = _merge_unique(required, schema_required)
                filled, attr_notes = _autofill_attributes_with_notes(
                    product, cfg, mp,
                    {"attributes": attrs, "required_attributes": autofill_required,
                     **{k: v for k, v in base.items() if k != "attributes"}},
                    required=autofill_required, template=template)
                for key, value in filled.items():
                    attrs.setdefault(key, value)
                if filled:
                    fixes.append("autofill:" + ",".join(sorted(filled)))
                for note in attr_notes:
                    if (note not in res["attribute_notes"]
                            and len(res["attribute_notes"]) < 30):
                        res["attribute_notes"].append(note)
            except Exception as exc:  # noqa: BLE001
                res["warnings"].append(f"属性补齐异常：{str(exc)[:120]}")

        # 2) 政策预检 + 本地自动修复（最多 3 次内部迭代）
        pf: dict = {"blockers": [], "warnings": [], "autofix_actions": []}
        payload: dict = {}
        issues: list[str] = []
        for _pass in range(3):
            ov = dict(base)
            if attrs:
                ov["attributes"] = dict(attrs)
            ov["required_attributes"] = list(required)
            try:
                _detail2, payload, issues, _warns = prepare_save(
                    pid, cfg, ov, client=c)
            except Exception as exc:  # noqa: BLE001
                payload = {}
                issues = [f"构建保存包失败：{str(exc)[:160]}"]
            pf = policy_preflight(
                payload, product,
                _preflight_cfg(cfg, required, schema_required, strict_schema))
            if not pf["autofix_actions"]:
                break
            done = _apply_local_fixes(ov, pf["autofix_actions"], payload,
                                      auto_fix_title, auto_fix_description,
                                      product=product)
            if not done:
                break
            for action in done:
                if action not in fixes:
                    fixes.append(action)
            # 把修复结果回写到 base（下一轮沿用）
            for key in ("title", "bullets", "description", "search_terms", "sku"):
                if key in ov:
                    base[key] = ov[key]

        # 3) 仍有阻断项 → 不盲目发布
        blockers = list(pf.get("blockers") or []) + [
            f"标准校验：{item}" for item in (issues or [])]
        if blockers:
            res["rounds"].append(_round_record(
                round_no, op, previous_state, "blocked", previous_err, fixes,
                blockers, pf.get("warnings") or [],
                time.time() - round_started, False))
            res["blockers"] = blockers
            res["warnings"] = _merge_unique(res["warnings"], pf.get("warnings") or [])
            res["detail"] = "本地政策预检未通过，已停止（不盲目发布）"
            break

        # 4) 真实保存并发布（唯一写入口）
        ov = dict(base)
        if attrs:
            ov["attributes"] = dict(attrs)
        ov["required_attributes"] = list(required)
        try:
            saved = save_to_amazon(pid, cfg, overrides=ov, op=op, client=c)
        except RuntimeError as exc:  # 授权/CI 护栏
            res["rounds"].append(_round_record(
                round_no, op, previous_state, "blocked", str(exc), fixes,
                [str(exc)], pf.get("warnings") or [],
                time.time() - round_started, False))
            res["blockers"] = [str(exc)]
            res["detail"] = "被安全护栏拒绝（未写入）"
            break
        except ValueError as exc:
            res["rounds"].append(_round_record(
                round_no, op, previous_state, "blocked", str(exc), fixes,
                [str(exc)], pf.get("warnings") or [],
                time.time() - round_started, False))
            res["blockers"] = [str(exc)]
            break
        except Exception as exc:  # noqa: BLE001
            res["rounds"].append(_round_record(
                round_no, op, previous_state, "save_failed", str(exc), fixes,
                [], pf.get("warnings") or [],
                time.time() - round_started, False))
            res["publish_error"] = str(exc)[:400]
            res["detail"] = "保存/发布请求失败"
            break
        res["saved"] = True
        res["saved_round"] = round_no
        res["attempts"] = round_no
        res["required_attrs"] = list(required)

        # 5) op=2 轮询发布结果
        if op != OP_PUBLISH:
            res.update({"ok": True, "final_state": "saved",
                        "publish_status": "saved", "detail": "保存成功",
                        "elapsed": round(time.time() - started, 2)})
            res["rounds"].append(_round_record(
                round_no, op, "saved", "saved", "", fixes,
                [], pf.get("warnings") or [], time.time() - round_started, True))
            break

        verified = verify_publish(pid, cfg, client=c,
                                  wait_seconds=max(0, int(wait_seconds or 0)))
        state = str(verified.get("state") or "")
        status = str(verified.get("status") or "unknown")
        err = str(verified.get("err_msg") or "")
        res["final_state"] = state
        res["publish_status"] = status
        res["asin"] = str(verified.get("asin") or "")
        res["publish_error"] = err if status in ("failed", "not_submitted") else ""
        req_in_err = list(verified.get("required_attrs") or [])
        res["required_attrs"] = _merge_unique(required, req_in_err)
        res["rounds"].append(_round_record(
            round_no, op, state, status, err, fixes,
            [], pf.get("warnings") or [], time.time() - round_started, True,
            res["asin"]))
        if status in ("online", "reviewing", "processing"):
            res.update({"ok": True, "detail": verified.get("detail") or "已提交"})
            break
        if round_no >= max(1, int(max_rounds or 1)):
            res["detail"] = "已达到最大自动修复轮次，发布仍未成功"
            break

        # 6) 失败分类 → 下一轮修复
        next_fixes: list[str] = []
        err_low = err.lower()
        if req_in_err and auto_fix_attrs:
            required = _merge_unique(required, req_in_err)
            next_fixes.append("attrs:" + ",".join(req_in_err))
        elif any(h in err or h in err_low for h in _TITLE_REPEAT_HINTS):
            if auto_fix_title:
                word_match = re.search(r"\[([^\]]+)\]", err)
                banned = word_match.group(1) if word_match else ""
                fixed = fix_title_repetition(
                    str(base.get("title") or payload.get("title") or ""),
                    max_repeat=2, banned_word=banned)
                if fixed:
                    base["title"] = fixed
                    next_fixes.append("fix_title_repetition")
        elif any(h in err or h in err_low for h in _ATTR_ERR_HINTS):
            if auto_fix_attrs:
                required = _merge_unique(required, parse_required_attrs(err))
                next_fixes.append("attrs:scan")
        elif any(h in err or h in err_low for h in _CONTENT_ERR_HINTS):
            if auto_fix_description:
                base["description"] = sanitize_description(
                    str(base.get("description") or payload.get("description") or ""))
                base["bullets"] = sanitize_bullets(
                    base.get("bullets") or payload.get("bulletPoint") or [])
                try:
                    from .amazon_payload import strip_html as _strip_html
                    if len(_strip_html(str(base.get("description") or ""))) < 60:
                        base["description"] = fallback_description(
                            str(base.get("title") or payload.get("title") or ""),
                            [str(b) for b in (base.get("bullets") or [])])
                        next_fixes.append("fallback_description")
                except Exception:
                    pass
                base["search_terms"] = sanitize_search_terms(
                    str(base.get("search_terms") or payload.get("searchTerms") or ""))
                next_fixes.append("sanitize_content")
            if auto_ai_fix:
                try:
                    from .amazon_pipeline import ai_generate_field
                    value, _notes = ai_generate_field(
                        product, cfg, "description", mp)
                    if value:
                        base["description"] = sanitize_description(str(value))
                        next_fixes.append("ai_rewrite_description")
                except Exception:
                    pass
        if not next_fixes:
            res["detail"] = "发布失败且无可用自动修复规则，已停止"
            break
        for fix in next_fixes:
            if fix not in res["fix_log"]:
                res["fix_log"].append(fix)
        previous_state, previous_err = state, err

    res["attempts"] = max(res.get("attempts") or 0, len(res.get("rounds") or []))
    res["elapsed"] = round(time.time() - started, 2)
    if res["ok"]:
        res["fix_log"] = list(dict.fromkeys(res["fix_log"]))
    return res


__all__ = [
    "AutoPublishResult", "auto_publish", "policy_preflight",
    "fix_title_repetition", "dedupe_title_words", "sanitize_title",
    "sanitize_bullets", "sanitize_description", "sanitize_search_terms",
    "sanitize_sku", "filter_schema_required", "fallback_description",
]

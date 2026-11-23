"""TikTok Shop 刊登 AI 提示词与本地后处理（离线、无网络、无第三方依赖）。

本模块只负责三件事：

1. **站点元数据**：``SITE_META``（24 个 L7 站点）+ ``language_for_site`` /
   ``site_meta`` / ``is_us_site``，未接入店铺时也能判断站点语言、币种与
   是否支持五点/搜索关键词（仅 US/4Seller）；
2. **4 类提示词**：标题 / 产品描述 / 五点（key product features）/ 搜索关键词，
   硬约束来自 TikTok 保存限制（标题 25–255 字符、描述 ≤10000 字符、五点 ≤5 条且
   总长 ≤1500、关键词 ≤15 个且总长 ≤250；``DEFAULT_PROMPTS`` 可被 cfg 的
   ``tiktok_*_prompt`` 覆盖）；
3. **AI 输出解析/清洗**：``parse_key_features`` / ``parse_search_terms`` 兼容
   JSON 数组、``{...}`` 包裹、编号、换行、``●``/``•``/``|`` 分隔、代码块围栏；
   ``html_description`` 把模型输出收敛成只含 ``<p><ul><li><b><br>`` 的 HTML 片段；
   ``clean_title`` 做站点语言与非法字符的本地二次清洗。

设计约束（docs/TIKTOK_DESIGN.md §3.3）：
- 站点非英语 → 全部文案输出该站点语言；全球产品只允许英文；
- 标题不得含中文（非中文站点）/emoji/HTML/联系方式/促销词/竞品品牌；
- 描述只允许 ``<p><ul><li><b><br>``；五点/关键词仅 US 使用；
- AI 结果必须可编辑，保存前还会由 ``tiktok_payload.validate_tiktok_payload``
  再做本地校验；这里只负责「把模型输出变成字段能接受的形状」。
"""
from __future__ import annotations

import html as _html
import json
import re

# ---------------- 站点元数据（docs §2，24 站点） ----------------
# 站点代码与站点无关的元数据；US 之外的 L7 站点共 23 个（§0 实测），加 US 共 24。
SITE_META: dict[str, dict] = {
    "US": dict(code="US", country="美国", currency="USD", lang="en", lang_name="英语"),
    "GB": dict(code="GB", country="英国", currency="GBP", lang="en", lang_name="英语"),
    "IE": dict(code="IE", country="爱尔兰", currency="EUR", lang="en", lang_name="英语"),
    "DE": dict(code="DE", country="德国", currency="EUR", lang="de", lang_name="德语"),
    "AT": dict(code="AT", country="奥地利", currency="EUR", lang="de", lang_name="德语"),
    "FR": dict(code="FR", country="法国", currency="EUR", lang="fr", lang_name="法语"),
    "BE": dict(code="BE", country="比利时", currency="EUR", lang="nl", lang_name="荷兰语"),
    "NL": dict(code="NL", country="荷兰", currency="EUR", lang="nl", lang_name="荷兰语"),
    "ES": dict(code="ES", country="西班牙", currency="EUR", lang="es", lang_name="西班牙语"),
    "IT": dict(code="IT", country="意大利", currency="EUR", lang="it", lang_name="意大利语"),
    "PT": dict(code="PT", country="葡萄牙", currency="EUR", lang="pt", lang_name="葡萄牙语"),
    "PL": dict(code="PL", country="波兰", currency="EUR", lang="pl", lang_name="波兰语"),
    "CZ": dict(code="CZ", country="捷克", currency="CZK", lang="cs", lang_name="捷克语"),
    "GR": dict(code="GR", country="希腊", currency="EUR", lang="el", lang_name="希腊语"),
    "HU": dict(code="HU", country="匈牙利", currency="EUR", lang="hu", lang_name="匈牙利语"),
    "JP": dict(code="JP", country="日本", currency="JPY", lang="ja", lang_name="日语"),
    "TH": dict(code="TH", country="泰国", currency="THB", lang="th", lang_name="泰语"),
    "VN": dict(code="VN", country="越南", currency="VND", lang="vi", lang_name="越南语"),
    "MY": dict(code="MY", country="马来西亚", currency="MYR", lang="ms", lang_name="马来语"),
    "SG": dict(code="SG", country="新加坡", currency="SGD", lang="en", lang_name="英语"),
    "PH": dict(code="PH", country="菲律宾", currency="PHP", lang="en", lang_name="英语"),
    "ID": dict(code="ID", country="印尼", currency="IDR", lang="id", lang_name="印尼语"),
    "BR": dict(code="BR", country="巴西", currency="BRL", lang="pt", lang_name="葡萄牙语"),
    "MX": dict(code="MX", country="墨西哥", currency="MXN", lang="es", lang_name="西班牙语"),
}

# 客户端 integrations/tiktok.py 的站点字段用 language/language_name；这里补同一套
# 别名键，保证 UI/流水线无论按哪套键名读取都能拿到语言（避免两处漂移）。
for _meta in SITE_META.values():
    _meta.setdefault("language", _meta["lang"])
    _meta.setdefault("language_name", _meta["lang_name"])

# 保存限制（docs §1.6：nameMin / nameMax / descMax；五点与关键词见 §1.5/§5）
TITLE_MIN = 25
TITLE_MAX = 255
DESCRIPTION_MAX_CHARS = 10000
KEY_FEATURES_MAX = 5
KEY_FEATURES_MIN = 3
KEY_FEATURES_TOTAL_MAX = 1500
KEY_FEATURE_ITEM_MAX = 300
SEARCH_TERMS_MAX = 15
SEARCH_TERMS_TOTAL_MAX = 250

# US 全球模式/4Seller 专用标记（站点代码可能以 "4Seller" 形式传入）
_US_SITE_CODES = {"US", "4SELLER"}

_CN_RE = re.compile(r"[\u4e00-\u9fff]")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_CODE_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*|\s*```\s*$")
_SCRIPT_BLOCK_RE = re.compile(r"<(script|style|iframe)\b[^>]*>[\s\S]*?</\1>", re.I)
_DANGEROUS_TAG_RE = re.compile(
    r"</?(?:script|style|iframe|img|object|embed|link|form|input|svg|video)\b[^>]*>", re.I)
# 只允许这 5 种标签（docs §3.3）
_ALLOWED_TAG_RE = re.compile(r"<\s*(/?)\s*(p|ul|li|b|br)\b[^>]*>", re.I)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•●·–—]|\d{1,2}\s*[.)、:：])\s*")
_SEARCH_TERM_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "of", "to", "in", "on", "by", "or",
    "at", "as", "from", "is", "are", "be", "this", "that", "these", "those",
    "it", "its", "your", "our", "you", "we", "&",
}


# ---------------- 站点辅助 ----------------
def normalize_site(site: str) -> str:
    """站点代码归一化：``gb``/`` Gb `` → ``GB``；``4Seller`` → ``4SELLER``。"""
    s = re.sub(r"[\s_\-]+", "", str(site or "").strip())
    return s.upper()


def site_meta(site: str) -> dict:
    """返回站点元数据**副本**（未知站点按英语兜底，``known=False``）。

    返回键：``site`` / ``code`` / ``country`` / ``currency`` / ``lang`` /
    ``lang_name`` / ``known`` / ``is_us``。
    """
    raw = str(site or "").strip()
    key = normalize_site(raw)
    if key == "4SELLER":
        out = dict(SITE_META["US"])
        out["site"] = "4SELLER"
        out["known"] = True
        out["is_us"] = True
        return out
    base = SITE_META.get(key)
    if base:
        out = dict(base)
        out["site"] = key
        out["known"] = True
        out["is_us"] = key == "US"
        return out
    return dict(
        site=key, code=key, country="", currency="", lang="en",
        lang_name="英语", known=False, is_us=False,
    )


def language_for_site(site: str) -> str:
    """站点 → 语言代码（en/de/fr/…；未知站点按 en）。"""
    return str(site_meta(site).get("lang") or "en")


def is_us_site(site: str) -> bool:
    """是否 US/4Seller 站点（只有它支持 keyProductFeatures / searchTerms）。"""
    return bool(site_meta(site).get("is_us"))


def site_supported(site: str) -> bool:
    """站点是否在 24 个 L7 站点列表内。"""
    return bool(site_meta(site).get("known"))


# ---------------- 配置读取 ----------------
def _cfg_get(cfg, key: str, default=None):
    """cfg 可能是 AppConfig / dict / None（测试与脚本）。"""
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _render(template: str, mapping: dict) -> str:
    """只替换已知占位符，保留用户 prompt 里其它花括号内容。"""
    out = str(template or "")
    for key, value in mapping.items():
        out = out.replace("{" + key + "}", str(value))
    return out


# ---------------- 文本清洗基础工具 ----------------
def remove_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", str(text or ""))


def has_chinese(text: str) -> bool:
    return bool(_CN_RE.search(str(text or "")))


def strip_html(value) -> str:
    """HTML → 纯文本（用于长度/合规检查与提示词上下文）。"""
    s = str(value or "")
    s = _SCRIPT_BLOCK_RE.sub(" ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def truncate_chars(text: str, limit: int) -> str:
    """按字符数硬截断。"""
    s = str(text or "").strip()
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit].strip()


def truncate_words(text: str, limit: int) -> str:
    """按词边界截断到 ≤limit 字符（保留最后一个完整词）。"""
    s = str(text or "").strip()
    if limit <= 0 or len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        head = cut.rsplit(" ", 1)[0].strip()
        if head:
            return head
    return cut.strip()


def clean_for_site(text: str, site: str) -> str:
    """非中文站点去掉中文字符（AI 偶尔回中文，本地不能让它进保存包）。"""
    s = str(text or "")
    if language_for_site(site).lower().startswith("zh"):
        return s
    return _CN_RE.sub(" ", s)


# ---------------- 默认提示词（可在设置页覆盖） ----------------
DEFAULT_PROMPTS: dict[str, str] = {
    "title": (
        "你是 TikTok Shop 跨境电商资深 listing 运营专家。请根据商品原始信息，为 {country} 站点"
        "（TikTok Shop {code}）写一个可直接上架的商品标题。\n"
        "硬性要求：\n"
        "1) 使用{language}（站点语言）撰写，非中文站点禁止出现任何中文字符；\n"
        "2) 长度 {title_min}–{title_limit} 字符（TikTok 硬性限制），按词边界截断；\n"
        "3) 结构：核心产品词 + 关键规格/型号 + 核心卖点 + 适用场景；关键词自然融入，不堆砌；\n"
        "4) 保留原文真实型号与规格（功率/容量/尺寸/材质/数量），不得编造原文没有的认证、参数、产地；\n"
        "5) 禁止 emoji、HTML 标签、引号、全角符号、联系方式（邮箱/电话/网址/WhatsApp）、"
        "促销词（best/No.1/100%/free shipping/sale/guarantee 等）与竞品品牌；\n"
        "6) 只输出一行标题，不要解释、不要编号、不要 Markdown、不要引号包裹。"
    ),
    "description": (
        "你是 TikTok Shop 产品描述文案专家。请为 {country} 站点（TikTok Shop {code}）商品写产品描述。\n"
        "硬性要求：\n"
        "1) 输出 HTML 片段，只允许 <p> <ul> <li> <b> <br> 五种标签；"
        "禁止 <script>/<style>/<iframe>/<img> 及其它标签；\n"
        "2) 使用{language}（站点语言），3–5 段 + 一个参数列表（<ul><li>）；\n"
        "3) 总长度 ≤ {desc_limit} 字符（含 HTML 标签）；\n"
        "4) 内容结构：产品用途/适用场景 → 核心参数（只写原文有的）→ 结构与操作/维护 → 包装与服务；\n"
        "5) 禁止联系方式、网址、二维码、促销返现、绝对化/医疗表述，不得编造认证与参数；\n"
        "6) 只输出 HTML 片段，不要 Markdown 代码块围栏，不要解释。"
    ),
    "key_features": (
        "你是 TikTok Shop 卖点文案专家。请为 {country} 站点（TikTok Shop {code}）商品写商品卖点"
        "（key product features）。\n"
        "硬性要求：\n"
        "1) 恰好输出 {count} 条（{kf_min}–{kf_max} 条之间），以 JSON 字符串数组返回；\n"
        "2) 使用{language}（站点语言），非中文站点禁止出现中文字符；\n"
        "3) 每条 ≤ {item_max} 字符、单行不换行，以 2–5 个词的卖点/利益点短语开头后接冒号"
        "（例如 `Fast Charging: ...`），不要把 `Benefit:`/`Feature:` 占位词写进输出；\n"
        "4) 全部卖点总长度 ≤ {kf_total} 字符；\n"
        "5) 不得包含 emoji、HTML 标签、Markdown、联系方式（邮箱/电话/网址/微信/WhatsApp）；\n"
        "6) 禁止促销/绝对化/医疗表述（best、No.1、100%、free shipping、sale、guarantee、"
        "cheapest、cure 等）；\n"
        "7) 型号、尺寸、功率、材质、认证只能来自原文，不得编造；\n"
        "8) 只输出 JSON 数组（例如 [\"...\",\"...\"]），不要解释、不要代码块围栏。"
    ),
    "search_terms": (
        "你是 TikTok Shop 站内搜索关键词（search terms）优化专家。请为 {country} 站点"
        "（TikTok Shop {code}）商品生成搜索关键词。\n"
        "硬性要求：\n"
        "1) 只输出一行：小写单词/短语，空格分隔，不要标点、不要逗号、不要编号；\n"
        "2) 最多 {st_max} 个关键词，全部关键词总长度 ≤ {st_total} 字符；\n"
        "3) 使用{language}（站点语言），非中文站点禁止中文；\n"
        "4) 去重，不重复单复数（如已用 charger 不再写 chargers）；\n"
        "5) 不含停用词（a the for with and of to in on by or）；\n"
        "6) 不含品牌词/他人品牌/竞品名，不含促销词与联系方式；\n"
        "7) 只输出关键词本身，不要解释。"
    ),
}

# 配置键 → DEFAULT_PROMPTS 键（docs §3.7）
_PROMPT_CFG_KEYS: dict[str, str] = {
    "title": "tiktok_title_prompt",
    "description": "tiktok_description_prompt",
    "key_features": "tiktok_key_features_prompt",
    "search_terms": "tiktok_search_terms_prompt",
}

PROMPT_KINDS: tuple[str, ...] = ("title", "description", "key_features", "search_terms")


def prompt_for(cfg, kind: str) -> str:
    """取提示词模板：cfg 覆盖 > DEFAULT_PROMPTS。"""
    default = DEFAULT_PROMPTS.get(kind, "")
    key = _PROMPT_CFG_KEYS.get(kind, "")
    raw = str(_cfg_get(cfg, key, "") or "").strip() if key else ""
    return raw or default


# ---------------- 提示词拼装 ----------------
def _site_line(site: str) -> str:
    meta = site_meta(site)
    return (f"站点：TikTok Shop {meta.get('code') or '?'} · {meta.get('country') or '未知'} · "
            f"{meta.get('lang_name') or '英语'}（{meta.get('lang') or 'en'}）· "
            f"币种 {meta.get('currency') or '未知'}")


def _plain(value, limit: int = 0) -> str:
    s = strip_html(value)
    return s[:limit] if limit and len(s) > limit else s


def _first_price(product: dict) -> str:
    rows = product.get("variationList")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                for key in ("originalPrice", "price", "salePrice"):
                    val = row.get(key)
                    if val not in (None, ""):
                        return str(val)
    for key in ("price", "originalPrice", "standardPrice"):
        val = product.get(key)
        if val not in (None, ""):
            return str(val)
    return ""


def _product_context(product) -> str:
    """把商品事实整理成提示词上下文（只做事实陈述，不替模型编造）。"""
    p = product if isinstance(product, dict) else {}
    lines = ["【商品原始信息】"]
    pid = str(p.get("id") or p.get("idStr") or p.get("productId") or "").strip()
    if pid:
        lines.append(f"- 商品 ID：{pid}")
    title = str(p.get("productName") or p.get("title") or p.get("subject") or "").strip()
    if title:
        lines.append(f"- 原标题：{title[:400]}")
    sku = str(p.get("sellerSku") or p.get("sku") or "").strip()
    if not sku:
        rows = p.get("variationList")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and str(row.get("sellerSku") or "").strip():
                    sku = str(row["sellerSku"]).strip()
                    break
    if sku:
        lines.append(f"- SKU：{sku}")
    brand = str(p.get("brandInfo") or p.get("brand") or p.get("brandId") or "").strip()
    if brand:
        lines.append(f"- 品牌：{brand[:120]}")
    cat = str(p.get("categoryName") or p.get("categoryId") or "").strip()
    if cat:
        lines.append(f"- 类目：{cat[:200]}")
    price = _first_price(p)
    if price:
        lines.append(f"- 价格：{price}")
    desc = _plain(p.get("description"), 1500)
    if desc:
        lines.append(f"- 原产品描述（纯文本）：{desc}")
    attrs = p.get("productAttributes")
    if attrs:
        if isinstance(attrs, (list, dict)):
            try:
                attrs = json.dumps(attrs, ensure_ascii=False)
            except Exception:
                attrs = str(attrs)
        lines.append(f"- 产品属性 JSON（节选）：{str(attrs)[:1500]}")
    source = str(p.get("sourceUrl") or p.get("source_url") or "").strip()
    if source:
        lines.append(f"- 来源链接：{source[:300]}")
    return "\n".join(lines)


def _common_mapping(site: str, count: int = KEY_FEATURES_MAX) -> dict:
    meta = site_meta(site)
    want = int(count or KEY_FEATURES_MAX)
    want = max(KEY_FEATURES_MIN if KEY_FEATURES_MIN < KEY_FEATURES_MAX else 1,
               min(want, KEY_FEATURES_MAX))
    return {
        "site": meta.get("site") or "",
        "code": meta.get("code") or "",
        "country": meta.get("country") or "目标",
        "language": meta.get("lang_name") or "英语",
        "lang": meta.get("lang") or "en",
        "title_min": TITLE_MIN,
        "title_limit": TITLE_MAX,
        "desc_limit": DESCRIPTION_MAX_CHARS,
        "count": want,
        "kf_min": KEY_FEATURES_MIN,
        "kf_max": KEY_FEATURES_MAX,
        "kf_total": KEY_FEATURES_TOTAL_MAX,
        "item_max": KEY_FEATURE_ITEM_MAX,
        "st_max": SEARCH_TERMS_MAX,
        "st_total": SEARCH_TERMS_TOTAL_MAX,
    }


def build_title_prompt(product, cfg, site) -> str:
    """标题提示词（站点语言、25–255、禁中文/emoji/HTML/联系方式/促销）。"""
    mapping = _common_mapping(site)
    return "\n\n".join([
        _site_line(site),
        _render(prompt_for(cfg, "title"), mapping),
        _product_context(product),
        "请直接输出最终标题（一行，不要引号、不要解释）。",
    ])


def build_description_prompt(product, cfg, site) -> str:
    """产品描述提示词（HTML 白名单 <p><ul><li><b><br>，≤10000 字符）。"""
    mapping = _common_mapping(site)
    return "\n\n".join([
        _site_line(site),
        _render(prompt_for(cfg, "description"), mapping),
        _product_context(product),
        "请直接输出 HTML 片段（只允许 <p> <ul> <li> <b> <br>），不要代码块围栏。",
    ])


def build_key_features_prompt(product, cfg, site, count: int = KEY_FEATURES_MAX) -> str:
    """五点提示词（仅 US/4Seller 使用；恰好 count 条、总长 ≤1500）。"""
    mapping = _common_mapping(site, count)
    parts = [_site_line(site)]
    if not is_us_site(site):
        parts.append(
            "注意：keyProductFeatures（五点）仅美国站（US/4Seller）使用；"
            f"当前站点 {mapping.get('code') or '?'} 不使用该字段，以下内容仅作草稿参考。")
    parts.extend([
        _render(prompt_for(cfg, "key_features"), mapping),
        _product_context(product),
        f"请直接输出 JSON 字符串数组，恰好 {mapping['count']} 条卖点。",
    ])
    return "\n\n".join(parts)


def build_search_terms_prompt(product, cfg, site) -> str:
    """搜索关键词提示词（仅 US/4Seller；≤15 个且总长 ≤250）。"""
    mapping = _common_mapping(site)
    parts = [_site_line(site)]
    if not is_us_site(site):
        parts.append(
            "注意：searchTerms（搜索关键词）仅美国站（US/4Seller）使用；"
            f"当前站点 {mapping.get('code') or '?'} 不使用该字段，以下内容仅作草稿参考。")
    parts.extend([
        _render(prompt_for(cfg, "search_terms"), mapping),
        _product_context(product),
        "请直接输出一行小写关键词，空格分隔，不要标点与解释。",
    ])
    return "\n\n".join(parts)


def build_prompt(field: str, product, cfg, site, count: int = KEY_FEATURES_MAX) -> str:
    """按字段名（title/description/key_features/search_terms）分发。"""
    key = str(field or "").strip().lower()
    if key in ("title", "productName", "product_name"):
        return build_title_prompt(product, cfg, site)
    if key in ("description", "desc"):
        return build_description_prompt(product, cfg, site)
    if key in ("key_features", "keyfeatures", "key_product_features", "features"):
        return build_key_features_prompt(product, cfg, site, count=count)
    if key in ("search_terms", "searchterms", "keywords"):
        return build_search_terms_prompt(product, cfg, site)
    raise ValueError(f"未知提示词字段：{field}")


# ---------------- 本地清洗（AI 输出 → 可保存字段） ----------------
def clean_title(text: str, site: str = "", limit: int = TITLE_MAX) -> str:
    """标题清洗：单行、去 HTML/Markdown/emoji/引号、非中文站点去中文，≤limit。"""
    s = _CODE_FENCE_RE.sub("", str(text or "").strip())
    s = re.sub(r"^\s*(?:title|商品标题|标题)\s*[:：]\s*", "", s, flags=re.I)
    s = strip_html(s)
    s = remove_emoji(s)
    s = s.replace("±", " ").replace("●", " ").replace("•", " ")
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    s = s.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    if site:
        s = clean_for_site(s, site)
    s = s.strip().strip('"').strip("'").strip("“”‘’")
    s = re.sub(r"\s+", " ", s).strip()
    return truncate_words(s, limit)


def _clean_feature_item(text: str) -> str:
    """单条卖点清洗：去 HTML/emoji/±/●/编号/引号，压成单行。"""
    s = strip_html(text)
    s = remove_emoji(s)
    s = s.replace("±", " ").replace("●", " ").replace("•", " ")
    s = re.sub(r"^\s*(?:\d{1,2}\s*[.)、:：]|[-*–—·])\s*", "", s)
    s = s.strip().strip('"').strip("'").strip("“”‘’")
    s = re.sub(r"\s+", " ", s).strip()
    return truncate_chars(s, KEY_FEATURE_ITEM_MAX)


def _json_list_items(raw: str, keys: tuple[str, ...]) -> list[str] | None:
    """尝试把输出解析成 JSON 列表；失败返回 None。"""
    data = None
    if raw[:1] in "[{":
        try:
            data = json.loads(raw)
        except Exception:
            data = None
    if data is None:
        m = re.search(r"\[[\s\S]*\]", raw)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if isinstance(data, list):
        items = [str(x) for x in data if isinstance(x, (str, int, float)) and str(x).strip()]
        return items or None
    if isinstance(data, dict):
        for key in keys:
            val = data.get(key)
            if isinstance(val, list):
                items = [str(x) for x in val
                         if isinstance(x, (str, int, float)) and str(x).strip()]
                return items or None
    return None


def _split_feature_lines(raw: str) -> list[str]:
    """按换行 / 行内编号 / ●•| 分隔符拆条。"""
    parts: list[str] = []
    for line in re.split(r"\r?\n+", raw):
        line = line.strip()
        if not line:
            continue
        # 行内编号："1. ... 2. ... 3. ..."
        numbered = re.split(r"(?<=[^\d\s.])\s+(?=\d{1,2}\s*[.)、:：]\s*\S)", line)
        if len(numbered) > 1:
            parts.extend(numbered)
        elif re.search(r"[●•|]", line):
            parts.extend(re.split(r"[●•|]", line))
        else:
            parts.append(line)
    return parts


def parse_key_features(text: str, count: int = KEY_FEATURES_MAX) -> list[str]:
    """解析 AI 五点输出 → 清洗后的字符串列表（最多 ``count`` 条，总长 ≤1500）。

    兼容：JSON 字符串数组、``{"key_features": [...]}``、``1.``/``-``/``●``/``•``
    编号或分隔、纯换行分隔、代码块围栏。返回列表不补空串（不足即少量）。
    """
    want = int(count or KEY_FEATURES_MAX)
    want = max(1, min(want, KEY_FEATURES_MAX))
    raw = str(text or "").strip()
    raw = _CODE_FENCE_RE.sub("", raw)
    if not raw or raw.lower() in ("null", "none", "[]", "{}"):
        return []
    raw = re.sub(
        r"^\s*(?:key\s*product\s*features?|key\s*features?|五点描述|卖点)\s*[:：]\s*",
        "", raw, flags=re.I)
    items = _json_list_items(
        raw, ("key_features", "keyProductFeatures", "key_product_features",
              "features", "bullets", "list", "items", "data"))
    if items is None:
        items = _split_feature_lines(raw)
    return _fit_features(items, want)


def _fit_features(items: list[str], want: int) -> list[str]:
    """清洗 + 去重 + 限条数 + 限总长（1500 字符）。"""
    cleaned: list[str] = []
    seen: set[str] = set()
    total = 0
    for item in items:
        text = _clean_feature_item(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        if len(cleaned) >= want:
            break
        remain = KEY_FEATURES_TOTAL_MAX - total
        if remain <= 0:
            break
        if len(text) > remain:
            text = truncate_chars(text, remain)
            if not text:
                break
        seen.add(key)
        cleaned.append(text)
        total += len(text)
    return cleaned


def parse_search_terms(text: str) -> list[str]:
    """解析 AI 关键词输出 → 小写关键词列表（≤15 个、总长 ≤250）。

    兼容 JSON 数组、逗号/换行/``|``/``、`` 分隔、代码块围栏；去停用词、
    去标点、去重、去单复数重复。
    """
    raw = str(text or "").strip()
    raw = _CODE_FENCE_RE.sub("", raw)
    if not raw or raw.lower() in ("null", "none", "[]", "{}"):
        return []
    raw = re.sub(r"^\s*(?:search\s*terms?|key\s*words?|keywords?|关键词)\s*[:：]\s*",
                 "", raw, flags=re.I)
    items = _json_list_items(raw, ("search_terms", "searchTerms", "keywords", "terms",
                                   "list", "items", "data"))
    if items is not None:
        raw = " ".join(items)
    raw = remove_emoji(raw)
    raw = raw.lower()
    raw = re.sub(r"[,;|/、，。；\n\r\t]+", " ", raw)
    raw = re.sub(r"[^a-z0-9\s-]", " ", raw)
    words = [w.strip("-") for w in raw.split() if w.strip("-")]
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        if len(out) >= SEARCH_TERMS_MAX:
            break
        if len(word) < 2 or word in _SEARCH_TERM_STOPWORDS:
            continue
        if word in seen:
            continue
        if word.endswith("s") and word[:-1] in seen:
            continue
        if word.endswith("s") and word[:-1] in words and word[:-1] not in seen:
            continue
        candidate = " ".join(out + [word])
        if len(candidate) > SEARCH_TERMS_TOTAL_MAX:
            if not out:
                word = truncate_chars(word, SEARCH_TERMS_TOTAL_MAX)
                if not word:
                    break
            else:
                break
        seen.add(word)
        out.append(word)
    return out


def _plain_text_to_html(text: str) -> str:
    """纯文本 → 只含 <p>/<ul><li> 的 HTML（列表行识别 -、*、1. 等前缀）。"""
    lines = [ln.strip() for ln in re.split(r"\r?\n+", text) if ln.strip()]
    if not lines:
        return ""
    out: list[str] = []
    list_buf: list[str] = []

    def flush_list() -> None:
        if list_buf:
            out.append("<ul>" + "".join(f"<li>{_html.escape(x)}</li>" for x in list_buf)
                       + "</ul>")
            list_buf.clear()

    for line in lines:
        if _LIST_ITEM_RE.match(line):
            list_buf.append(_LIST_ITEM_RE.sub("", line, count=1).strip())
        else:
            flush_list()
            out.append(f"<p>{_html.escape(line)}</p>")
    flush_list()
    return "".join(x for x in out if x)


def _truncate_html(text: str, limit: int) -> str:
    """HTML 片段按字符数截断，并去掉截断产生的半个标签/空标签。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 去掉末尾不完整的标签
    cut = re.sub(r"<[^<]*$", "", cut)
    # 补齐未闭合的简单块级标签（尽力而为，不引入额外内容）
    for tag in ("ul", "li", "p", "b"):
        opens = len(re.findall(rf"<{tag}\b[^>]*>", cut, re.I))
        closes = len(re.findall(rf"</{tag}>", cut, re.I))
        if opens > closes:
            cut += f"</{tag}>" * min(opens - closes, 3)
    cut = re.sub(r"<(p|li)>\s*</\1>", "", cut, flags=re.I)
    return cut.strip()


def html_description(text: str, limit: int = DESCRIPTION_MAX_CHARS, *, allow_images: bool = False) -> str:
    """AI 描述输出 → 只含 ``<p><ul><li><b><br>`` 的 HTML 片段，≤limit 字符。

    - 先去 ``<script>/<style>/<iframe>`` 等危险标签与其它标签（保留内部文字）；
    - 允许标签统一成无属性小写形式（``<br>`` → ``<br/>``）；
    - 纯文本按段落包 ``<p>``，``- ``/``1. `` 开头的行归入 ``<ul><li>``；
    - 最终按字符数截断到 ``limit``（默认 10000，TikTok descMax）。
    """
    s = _CODE_FENCE_RE.sub("", str(text or "").strip())
    if not s:
        return ""
    s = _SCRIPT_BLOCK_RE.sub(" ", s)

    placeholders: list[str] = []

    if allow_images:
        from .platform_media import _image_matches
        parts, start = [], 0
        for tag, _, url in _image_matches(s):
            parts.append(s[start:tag.start()])
            parts.append(f"\x00TAG{len(placeholders)}\x00")
            placeholders.append(f'<img src="{_html.escape(url, quote=True)}">')
            start = tag.end()
        parts.append(s[start:])
        s = "".join(parts)
    s = _DANGEROUS_TAG_RE.sub(" ", s)

    def _stash(m: re.Match) -> str:
        closing = bool(m.group(1))
        name = m.group(2).lower()
        if name == "br":
            tag = "<br/>"
        else:
            tag = f"</{name}>" if closing else f"<{name}>"
        placeholders.append(tag)
        return f"\x00TAG{len(placeholders) - 1}\x00"

    s = _ALLOWED_TAG_RE.sub(_stash, s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    s = remove_emoji(s)
    for i, tag in enumerate(placeholders):
        s = s.replace(f"\x00TAG{i}\x00", tag)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()

    if not _HTML_TAG_RE.search(s):
        s = _plain_text_to_html(s)
    if not s:
        return ""
    # 空段落清理
    s = re.sub(r"<p>\s*</p>", "", s, flags=re.I)
    s = re.sub(r"<li>\s*</li>", "", s, flags=re.I)
    return _truncate_html(s, int(limit or DESCRIPTION_MAX_CHARS))


# ---------------- 类目属性补全提示词（AI 兜底，单次批量） ----------------
ATTRIBUTE_PROMPT = (
    "你是 TikTok Shop 类目属性补全助手。下面给出目标站点类目的**必填/待补属性**"
    "与商品原始信息，请为每个属性推断最可能的取值。\n"
    "硬性要求：\n"
    "1) 只输出一个 JSON 对象：{\"属性ID\": \"取值\"}，不要解释、不要 Markdown 代码块；\n"
    "2) 有枚举值（options）的属性，取值**必须**是 options 里的某一项原文，不得自造；\n"
    "3) 无枚举值（自由填写）的属性，取值用{language}简短填写（≤60 字符），"
    "只写商品信息里能支撑的内容；\n"
    "4) 商品信息不足以判断的属性，**不要猜测**，直接省略该属性（宁缺勿错）；\n"
    "5) 不得编造认证编号、产地、型号、保修条款；非中文站点不得输出中文。\n"
)


def build_attributes_prompt(product, attrs, site, limit: int = 12) -> str:
    """构造「类目属性补全」批量提示词（一次请求补多个属性）。

    ``attrs``：已规范化的属性列表（``load_category_attributes`` 输出），
    每项含 ``attrId``/``label``/``values``。只取前 ``limit`` 个属性，
    避免提示词过长；无属性时返回空串（调用方跳过 AI）。
    """
    rows = [a for a in (attrs or []) if isinstance(a, dict) and str(a.get("attrId") or "")]
    rows = rows[:max(1, int(limit or 12))]
    if not rows:
        return ""
    meta = site_meta(site)
    lang = str(meta.get("lang_name") or "英语")
    lines: list[str] = []
    for attr in rows:
        label = str(attr.get("transName") or attr.get("attrName") or attr.get("label") or "")
        english = str(attr.get("attrName") or "")
        head = (f"- 属性ID：{attr.get('attrId')}｜名称：{label}"
                + (f"（{english}）" if english and english != label else ""))
        if attr.get("isMultipleSelected"):
            head += "｜可多选"
        if attr.get("isMandatory"):
            head += "｜必填"
        values = [str(v.get("name") or "") for v in (attr.get("values") or [])
                  if isinstance(v, dict) and str(v.get("name") or "")]
        if values:
            shown = values[:40]
            head += "｜options：" + " / ".join(shown)
            if len(values) > len(shown):
                head += f" …（共 {len(values)} 个）"
        else:
            head += "｜options：无（自由填写）"
        lines.append(head)
    return "\n\n".join([
        ATTRIBUTE_PROMPT.replace("{language}", lang),
        f"站点：TikTok Shop {meta.get('code') or site or '?'} · "
        f"{meta.get('country') or '未知'} · {lang}",
        "【待补属性】\n" + "\n".join(lines),
        "【商品信息】\n" + _product_context(product),
        "请直接输出 JSON 对象（键=属性ID，值=取值）。",
    ]).strip()


def parse_attributes_reply(text, allowed_ids=None) -> dict[str, str]:
    """解析 AI 批量属性回复 → ``{attrId: value}``（容错代码块/多余文本）。

    ``allowed_ids`` 非空时只保留其中的键（防止模型编造属性）。
    """
    raw = str(text or "").strip()
    if not raw:
        return {}
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S | re.I)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    allowed = {str(x).strip() for x in allowed_ids} if allowed_ids else None
    out: dict[str, str] = {}
    for key, value in data.items():
        attr_id = str(key).strip()
        if not attr_id or (allowed is not None and attr_id not in allowed):
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        if isinstance(value, dict):
            value = value.get("value") or value.get("name") or value.get("id") or ""
        text_value = str(value or "").strip().strip('"').strip("'")
        if not text_value:
            continue
        if text_value.upper() in ("UNKNOWN", "N/A", "NA", "NULL", "NONE", "未知", "无法确定"):
            continue
        out[attr_id] = text_value[:120]
    return out


__all__ = [
    "SITE_META", "DEFAULT_PROMPTS", "PROMPT_KINDS",
    "TITLE_MIN", "TITLE_MAX", "DESCRIPTION_MAX_CHARS",
    "KEY_FEATURES_MAX", "KEY_FEATURES_MIN", "KEY_FEATURES_TOTAL_MAX",
    "KEY_FEATURE_ITEM_MAX", "SEARCH_TERMS_MAX", "SEARCH_TERMS_TOTAL_MAX",
    "normalize_site", "site_meta", "language_for_site", "is_us_site", "site_supported",
    "prompt_for", "build_prompt",
    "build_title_prompt", "build_description_prompt",
    "build_key_features_prompt", "build_search_terms_prompt",
    "parse_key_features", "parse_search_terms", "html_description",
    "clean_title", "clean_for_site", "remove_emoji", "has_chinese",
    "strip_html", "truncate_chars", "truncate_words",
    "ATTRIBUTE_PROMPT", "build_attributes_prompt", "parse_attributes_reply",
]

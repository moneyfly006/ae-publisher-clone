"""Amazon 刊登 AI 提示词与本地后处理（离线、无网络、无第三方依赖）。

本模块只负责三件事：

1. **站点元数据**：``SITE_META`` / ``language_for_site`` / ``site_meta``，
   未接入店铺时也能判断站点语言与币种；
2. **4 类提示词**：标题 / 五点描述 / 产品描述 / Search Terms，
   硬约束来自 Amazon 产品类型 schema（item_name ≤200、bullet_point ≤10 条
   且单条 ≤700、product_description ≤20000 字节、generic_keyword ≤500 字符 /
   ≤2000 字节）；``DEFAULT_PROMPTS`` 可在设置页用 cfg 的
   ``amazon_*_prompt`` 覆盖；
3. **AI 输出解析/清洗**：``parse_bullets`` / ``parse_search_terms`` 兼容
   JSON 数组、编号、行分隔、``●`` 分隔；再按站点语言做二次清洗
   （去 emoji、``±``、HTML、非中文站点的中文）。

设计约束：AI 结果必须可编辑，保存前还会由 ``amazon_payload.validate_amazon_payload``
再做一次本地校验；这里清洗只负责「把模型输出变成字段能接受的形状」。
"""
from __future__ import annotations

import html as _html
import json
import re

# ---------------- 站点元数据（未接店铺时也够用） ----------------
SITE_META: dict[str, dict] = {
    "ATVPDKIKX0DER": dict(code="US", country="美国", currency="USD", lang="en", lang_name="英语"),
    "A2EUQ1WTGCTBG2": dict(code="CA", country="加拿大", currency="CAD", lang="en", lang_name="英语"),
    "A1AM78C64UM0Y8": dict(code="MX", country="墨西哥", currency="MXN", lang="es", lang_name="西班牙语"),
    "A2Q3Y263D00KWC": dict(code="BR", country="巴西", currency="BRL", lang="pt", lang_name="葡萄牙语"),
    "A1F83G8C2ARO7P": dict(code="GB", country="英国", currency="GBP", lang="en", lang_name="英语"),
    "A1PA6795UKMFR9": dict(code="DE", country="德国", currency="EUR", lang="de", lang_name="德语"),
    "A13V1IB3VIYZZH": dict(code="FR", country="法国", currency="EUR", lang="fr", lang_name="法语"),
    "APJ6JRA9NG5V4": dict(code="IT", country="意大利", currency="EUR", lang="it", lang_name="意大利语"),
    "A1RKKUPIHCS9HS": dict(code="ES", country="西班牙", currency="EUR", lang="es", lang_name="西班牙语"),
    "A1VC38T7YXB528": dict(code="JP", country="日本", currency="JPY", lang="ja", lang_name="日语"),
    "A39IBJ37TRP1C6": dict(code="AU", country="澳大利亚", currency="AUD", lang="en", lang_name="英语"),
    "A21TJRUUN4KGV": dict(code="IN", country="印度", currency="INR", lang="en", lang_name="英语"),
    "A1805IZSGTT6HS": dict(code="NL", country="荷兰", currency="EUR", lang="nl", lang_name="荷兰语"),
    "A2NODRKZP88ZB9": dict(code="SE", country="瑞典", currency="SEK", lang="sv", lang_name="瑞典语"),
    "A1C3SOZRARQ6R3": dict(code="PL", country="波兰", currency="PLN", lang="pl", lang_name="波兰语"),
    "A33AVAJ2PDY3EV": dict(code="TR", country="土耳其", currency="TRY", lang="tr", lang_name="土耳其语"),
    "A2VIGQ35RCS4UG": dict(code="AE", country="阿联酋", currency="AED", lang="en", lang_name="英语"),
    "A17E79C6D8DWNP": dict(code="SA", country="沙特", currency="SAR", lang="ar", lang_name="阿拉伯语"),
    "A19VAU5U5O7RUS": dict(code="SG", country="新加坡", currency="SGD", lang="en", lang_name="英语"),
    "A28R8C7NBKEWEA": dict(code="IE", country="爱尔兰", currency="EUR", lang="en", lang_name="英语"),
    "AMEN7PMS3EDWL": dict(code="BE", country="比利时", currency="EUR", lang="fr", lang_name="法语"),
    "ARBP9OOSHTCHU": dict(code="EG", country="埃及", currency="EGP", lang="ar", lang_name="阿拉伯语"),
    "AE08WJ6YKNBMC": dict(code="ZA", country="南非", currency="ZAR", lang="en", lang_name="英语"),
    "AAHKV2X7AFYLW": dict(code="CN", country="中国", currency="CNY", lang="zh", lang_name="中文"),
}

# schema 实测硬上限（getAttrJsUrl 下发的 CA/OUTDOOR_LIVING）
TITLE_MAX = 200
BULLET_MAX_ITEMS = 10
BULLET_MAX_CHARS = 700
BULLET_SUGGEST_CHARS = 500
BULLET_MIN_AI = 3
BULLET_DEFAULT_COUNT = 5
DESCRIPTION_MAX_BYTES = 20000
SEARCH_TERMS_MAX_CHARS = 500
SEARCH_TERMS_MAX_BYTES = 2000
SEARCH_TERMS_SUGGEST_BYTES = 250

_CN_RE = re.compile(r"[\u4e00-\u9fff]")
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_SCRIPT_BLOCK_RE = re.compile(
    r"<(script|style|iframe)\b[^>]*>[\s\S]*?</\1>", re.I
)
_DANGEROUS_TAG_RE = re.compile(r"</?(script|style|iframe|img|object|embed|link)\b[^>]*>", re.I)
_ALLOWED_TAG_RE = re.compile(r"</?(p|ul|li|b|br)\b[^>]*>", re.I)
_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")
_SEARCH_TERM_STOPWORDS = {
    "a", "an", "the", "for", "with", "and", "of", "to", "in", "on", "by", "or",
    "at", "as", "from", "is", "are", "be", "this", "that", "these", "those",
    "it", "its", "your", "our", "you", "we", "&",
}
# 人工可放行的词（比如品牌/型号里合法出现，UI/脚本可用 site_meta["promo_whitelist"] 覆盖）
PROMO_WHITELIST: tuple[str, ...] = ()


# ---------------- 站点辅助 ----------------
def site_meta(marketplace_id: str) -> dict:
    """返回站点元数据副本（未知站点按英语兜底，语言判断仍可用）。"""
    mp = str(marketplace_id or "").strip()
    base = SITE_META.get(mp)
    if base:
        out = dict(base)
    else:
        out = dict(code="", country="", currency="", lang="en", lang_name="英语")
    out["marketplace_id"] = mp
    return out


def language_for_site(marketplace_id: str) -> str:
    """站点代码 → 语言（en/es/pt/...；未知站点按 en）。"""
    return str(site_meta(marketplace_id).get("lang") or "en")


def is_chinese_site(marketplace_id: str) -> bool:
    return language_for_site(marketplace_id).lower().startswith("zh")


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


# ---------------- 默认提示词（可在设置页覆盖） ----------------
DEFAULT_PROMPTS: dict[str, str] = {
    "title": (
        "你是 Amazon 跨境电商资深 listing 运营专家。请根据商品原始信息，为 {country} 站点"
        "（Amazon {code}）写一个可直接上架的英文/当地语言商品标题。\n"
        "硬性要求：\n"
        "1) 使用{language}（站点语言），非中文站点禁止出现任何中文字符；\n"
        "2) 长度 ≤ {title_limit} 字符（按字符数，不是字节），超过按词边界截断；\n"
        "3) 结构：核心产品词 + 关键规格/型号 + 核心卖点 + 适用场景；关键词自然融入，不堆砌；\n"
        "4) 保留原文真实型号与规格（功率/容量/尺寸/材质/数量），不得编造原文没有的认证、参数、产地；\n"
        "5) 禁止 emoji、HTML、引号、全角符号、联系方式（邮箱/电话/网址/WhatsApp）、促销词"
        "（best/No.1/100%/free shipping/sale/guarantee 等）与竞品品牌；\n"
        "6) 只输出一行标题，不要解释、不要编号、不要 Markdown、不要引号包裹。"
    ),
    "bullets": (
        "你是 Amazon listing 卖点文案专家。请为 {country} 站点（Amazon {code}）商品写五点描述。\n"
        "硬性要求：\n"
        "1) 恰好输出 {count} 条卖点（JSON 字符串数组），每条 ≤ {bullet_suggest} 字符（schema 硬上限 "
        "{bullet_limit} 字符），单条不换行；\n"
        "2) 使用{language}（站点语言），非中文站点禁止出现中文字符；\n"
        "3) 每条以 2~5 个词的卖点/利益点短语开头，后接冒号（例如 `75 KW DIESEL ENGINE: ...` / "
        "`Compact 4x4 Chassis: ...`），句子里自然融入核心关键词；禁止把 `Benefit:`、`Feature:`、"
        "`feature:`、`FEATURE:` 等占位词本身写进输出；\n"
        "4) 不得包含 ±、●、emoji、HTML 标签、Markdown、联系方式（邮箱/电话/网址/微信/WhatsApp）；\n"
        "5) 禁止促销/绝对化表述（best、No.1、100%、free shipping、sale、guarantee、cheapest 等）"
        "与医疗功效承诺；\n"
        "6) 型号、尺寸、功率、材质、认证只能来自原文，不得编造；\n"
        "7) 只输出 JSON 数组（例如 [\"...\",\"...\"]），不要解释、不要代码块围栏。"
    ),
    "description": (
        "你是 Amazon 产品描述文案专家。请为 {country} 站点（Amazon {code}）商品写产品描述。\n"
        "硬性要求：\n"
        "1) 输出 HTML 片段，只允许 <p> <ul> <li> <b> <br> 标签；禁止 <script>/<style>/<iframe>/<img>；\n"
        "2) 使用{language}（站点语言），3–5 段 + 一个参数列表（<ul><li>），建议 ≤2000 字符"
        "（schema 硬上限 {desc_bytes} 字节）；\n"
        "3) 内容结构：产品用途/适用场景 → 核心参数（只写原文有的）→ 结构与操作/维护 → 包装与服务；\n"
        "4) 禁止联系方式、网址、二维码、促销返现、绝对化/医疗表述，不得编造认证与参数；\n"
        "5) 只输出 HTML 片段，不要 Markdown 代码块围栏，不要解释。"
    ),
    "search_terms": (
        "你是 Amazon 后台 Search Terms（generic_keyword）优化专家。请为 {country} 站点"
        "（Amazon {code}）商品生成搜索关键词。\n"
        "硬性要求：\n"
        "1) 使用{language}（站点语言）；非中文站点禁止中文；\n"
        "2) 只输出一行：小写单词/短语，空格分隔，不要标点、不要逗号、不要编号；\n"
        "3) 建议 ≤250 字节（schema 硬上限 500 字符 / {st_bytes} 字节）；\n"
        "4) 去重，不重复单复数（如已用 loader 不再写 loaders）；\n"
        "5) 不含停用词（a the for with and of to in on by or）；\n"
        "6) 不含品牌词/他人品牌/竞品名，不含促销词与联系方式；\n"
        "7) 只输出关键词本身，不要解释。"
    ),
}

# 配置键 → DEFAULT_PROMPTS 键
_PROMPT_CFG_KEYS = {
    "title": "amazon_title_prompt",
    "bullets": "amazon_bullet_prompt",
    "description": "amazon_description_prompt",
    "search_terms": "amazon_search_terms_prompt",
}


def _prompt_cfg(cfg, kind: str) -> str:
    key = _PROMPT_CFG_KEYS.get(kind, "")
    raw = str(_cfg_get(cfg, key, "") or "").strip() if key else ""
    return raw or DEFAULT_PROMPTS[kind]


def _site_line(marketplace_id: str) -> str:
    meta = site_meta(marketplace_id)
    return (f"站点：Amazon {meta.get('code') or '?'} · {meta.get('country') or '未知'} · "
            f"{meta.get('lang_name') or '英语'}（{meta.get('lang') or 'en'}）· "
            f"币种 {meta.get('currency') or '未知'}")


def _plain(value, limit: int = 0) -> str:
    s = str(value or "")
    s = _SCRIPT_BLOCK_RE.sub(" ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] if limit and len(s) > limit else s


def _product_context(product: dict) -> str:
    """把商品事实整理成提示词上下文（只做事实陈述，不替模型编造）。"""
    p = product or {}
    lines = ["【商品原始信息】"]
    pid = str(p.get("id") or "").strip()
    if pid:
        lines.append(f"- 商品 ID：{pid}")
    title = str(p.get("title") or p.get("subject") or "").strip()
    if title:
        lines.append(f"- 原标题：{title[:400]}")
    for label, key in (("SKU", "sku"), ("品牌", "brand"), ("制造商", "manufacturer")):
        val = str(p.get(key) or "").strip()
        if val:
            lines.append(f"- {label}：{val}")
    cat = str(p.get("productType") or p.get("categoryBrowsePath") or p.get("categoryId") or "").strip()
    if cat:
        lines.append(f"- 类目/产品类型：{cat[:200]}")
    bullets = [b.strip() for b in str(p.get("bulletPoint") or "").split("±") if b.strip()]
    if bullets:
        lines.append("- 原五点描述：" + " / ".join(bullets[:10])[:1200])
    desc = _plain(p.get("description") or "", 1500)
    if desc:
        lines.append(f"- 原产品描述（纯文本）：{desc}")
    attrs = str(p.get("productAttr") or "").strip()
    if attrs:
        lines.append(f"- 产品属性 JSON（节选）：{attrs[:1500]}")
    terms = str(p.get("searchTerms") or "").strip()
    if terms:
        lines.append(f"- 原 Search Terms：{terms[:300]}")
    source = str(p.get("sourceUrl") or p.get("source_url") or "").strip()
    if source:
        lines.append(f"- 来源链接：{source[:300]}")
    return "\n".join(lines)


def _common_mapping(marketplace_id: str, count: int = BULLET_DEFAULT_COUNT) -> dict:
    meta = site_meta(marketplace_id)
    return {
        "code": meta.get("code") or "",
        "country": meta.get("country") or "目标",
        "language": meta.get("lang_name") or "英语",
        "lang": meta.get("lang") or "en",
        "title_limit": TITLE_MAX,
        "bullet_limit": BULLET_MAX_CHARS,
        "bullet_suggest": BULLET_SUGGEST_CHARS,
        "count": int(count or BULLET_DEFAULT_COUNT),
        "desc_bytes": DESCRIPTION_MAX_BYTES,
        "st_bytes": SEARCH_TERMS_MAX_BYTES,
    }


def build_title_prompt(product, cfg, marketplace_id) -> str:
    mapping = _common_mapping(marketplace_id)
    return "\n\n".join([
        _site_line(marketplace_id),
        _render(_prompt_cfg(cfg, "title"), mapping),
        _product_context(product),
        "请直接输出最终标题（一行，不要引号、不要解释）。",
    ])


def build_bullets_prompt(product, cfg, marketplace_id, count=5) -> str:
    mapping = _common_mapping(marketplace_id, count)
    return "\n\n".join([
        _site_line(marketplace_id),
        _render(_prompt_cfg(cfg, "bullets"), mapping),
        _product_context(product),
        f"请直接输出 JSON 字符串数组，恰好 {int(count or BULLET_DEFAULT_COUNT)} 条卖点。",
    ])


def build_description_prompt(product, cfg, marketplace_id) -> str:
    mapping = _common_mapping(marketplace_id)
    return "\n\n".join([
        _site_line(marketplace_id),
        _render(_prompt_cfg(cfg, "description"), mapping),
        _product_context(product),
        "请直接输出 HTML 片段（只允许 <p> <ul> <li> <b> <br>），不要代码块围栏。",
    ])


def build_search_terms_prompt(product, cfg, marketplace_id) -> str:
    mapping = _common_mapping(marketplace_id)
    return "\n\n".join([
        _site_line(marketplace_id),
        _render(_prompt_cfg(cfg, "search_terms"), mapping),
        _product_context(product),
        "请直接输出一行小写关键词，空格分隔，不要标点与解释。",
    ])


# ---------------- 本地清洗（AI 输出 → 可保存字段） ----------------
def remove_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", str(text or ""))


def has_chinese(text: str) -> bool:
    return bool(_CN_RE.search(str(text or "")))


def strip_html(value) -> str:
    """HTML → 纯文本（用于长度/合规检查与提示词上下文）。"""
    return _plain(value)


def clean_for_site(text: str, marketplace_id: str) -> str:
    """非中文站点去掉中文字符（AI 偶尔回中文，本地不能让它进保存包）。"""
    if is_chinese_site(marketplace_id):
        return str(text or "")
    return _CN_RE.sub(" ", str(text or ""))


def truncate_words(text: str, limit: int) -> str:
    """按词边界截断到 ≤limit 字符（保留最后一个完整词）。"""
    s = str(text or "").strip()
    if limit <= 0 or len(s) <= limit:
        return s
    if s[limit] == " ":
        return s[:limit].rstrip()
    cut = s[:limit]
    if " " in cut:
        head = cut.rsplit(" ", 1)[0].strip()
        if head:
            return head
    return cut.strip()


def truncate_bytes(text: str, max_bytes: int, sep: str = " ") -> str:
    """按 UTF-8 字节上限截断（优先按分隔符回退到完整词）。"""
    s = str(text or "")
    if max_bytes <= 0 or len(s.encode("utf-8")) <= max_bytes:
        return s
    out = ""
    for part in s.split(sep):
        candidate = (out + sep + part) if out else part
        if len(candidate.encode("utf-8")) > max_bytes:
            break
        out = candidate
    if out:
        return out
    # 单个词就超限：按字符硬截断
    cut = []
    total = 0
    for ch in s:
        n = len(ch.encode("utf-8"))
        if total + n > max_bytes:
            break
        cut.append(ch)
        total += n
    return "".join(cut).strip()


def clean_title(text: str, limit: int = TITLE_MAX) -> str:
    """标题清洗：单行、去 HTML/Markdown/emoji/引号，按词边界截断到 ≤limit。"""
    s = str(text or "")
    s = _CODE_FENCE_RE.sub("", s.strip())
    s = re.sub(r"^\s*(?:title|标题)\s*[:：]\s*", "", s, flags=re.I)
    s = strip_html(s)
    s = remove_emoji(s)
    s = s.replace("±", " ").replace("●", " ").replace("•", " ")
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    s = s.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    s = s.strip().strip('"').strip("'").strip("“”‘’")
    s = re.sub(r"\s+", " ", s).strip()
    return truncate_words(s, limit)


_BULLET_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:benefit|feature)\s*(?::|：|-|–|—)\s*", re.I)


def clean_bullet(text: str, limit: int = BULLET_MAX_CHARS) -> str:
    """单条卖点清洗：去 HTML/emoji/±/●，去掉开头 ``Benefit:``/``Feature:`` 占位词。"""
    s = strip_html(text)
    s = remove_emoji(s)
    s = s.replace("±", " ").replace("●", " ").replace("•", " ")
    s = re.sub(r"\s+", " ", s).strip().strip('"').strip("'")
    # 模型会把提示词里的占位词照抄成「Benefit: ...」；只在后面还有正文时剥掉，
    # 正常的「POWERFUL ENGINE: ...」不受影响。
    for _ in range(3):
        stripped = _BULLET_PLACEHOLDER_RE.sub("", s, count=1)
        if stripped == s:
            break
        s = stripped.strip()
        if not s:
            break
    s = re.sub(r"\s+", " ", s).strip()
    return truncate_words(s, limit)


def sanitize_description(text: str, max_bytes: int = DESCRIPTION_MAX_BYTES) -> str:
    """产品描述清洗：只保留 <p><ul><li><b><br>，纯文本自动包 <p>，按字节截断。

    Amazon 的 product_description 是 HTML 字段且 required；这里保证：
    - 去 <script>/<style>/<iframe>/<img> 等危险标签；
    - 未知标签保留内部文字、去掉标签本身；
    - 纯文本（没有标签）按换行/段落包成 <p>；
    - 最终 ≤ max_bytes（UTF-8）。
    """
    s = str(text or "").strip()
    s = _CODE_FENCE_RE.sub("", s)
    if not s:
        return ""
    s = re.sub(r"<(script|style|iframe)\b[^>]*>[\s\S]*?</\1>", " ", s, flags=re.I)
    s = _DANGEROUS_TAG_RE.sub(" ", s)

    def _keep(m: re.Match) -> str:
        return m.group(0)

    # 把允许标签先占位，其余标签去掉（保留内部文本）
    placeholders: list[str] = []

    def _stash(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00TAG{len(placeholders) - 1}\x00"

    s = _ALLOWED_TAG_RE.sub(_stash, s)
    s = _HTML_TAG_RE.sub(" ", s)
    s = _html.unescape(s)
    s = remove_emoji(s)
    for i, tag in enumerate(placeholders):
        s = s.replace(f"\x00TAG{i}\x00", tag)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s).strip()

    if not _HTML_TAG_RE.search(s):
        # 纯文本：按行/空行拆段，包 <p>
        paras = [p.strip() for p in re.split(r"\n+", s) if p.strip()]
        s = "".join(f"<p>{_html.escape(p)}</p>" for p in paras)
    # 空标签修补 <br> → <br/>
    s = re.sub(r"<br\s*>", "<br/>", s, flags=re.I)
    if len(s.encode("utf-8")) > max_bytes:
        cut = s
        while len(cut.encode("utf-8")) > max_bytes and len(cut) > 1:
            cut = cut[: int(len(cut) * 0.95)]
        cut = re.sub(r"<[^<]*$", "", cut)  # 去掉截断产生的半个标签
        s = cut.strip()
    return s


def parse_bullets(text: str, count: int = BULLET_DEFAULT_COUNT) -> list[str]:
    """解析 AI 五点输出，返回固定 ``count`` 条（不足补空、超出截断）。

    兼容：JSON 字符串数组、``1.``/``-``/``•``/``●`` 行首编号、``●`` 分隔、
    纯换行分隔、代码块围栏。每条会去 HTML/emoji/±/● 并限制 700 字符。
    """
    want = max(1, int(count or BULLET_DEFAULT_COUNT))
    raw = str(text or "").strip()
    raw = _CODE_FENCE_RE.sub("", raw)
    if raw.lower() in ("", "null", "none", "[]", "{}"):
        return _finish([], want)
    items: list[str] = []

    def _take_list(data) -> bool:
        if isinstance(data, list) and data and all(isinstance(x, (str, int, float)) for x in data):
            items.extend(str(x) for x in data)
            return True
        if isinstance(data, dict):
            for key in ("bullets", "bulletPoints", "bullet_points", "list", "data", "items"):
                val = data.get(key)
                if isinstance(val, list) and val:
                    items.extend(str(x) for x in val)
                    return True
        return False

    # 1) 整个输出就是 JSON
    if raw[:1] in "[{":
        try:
            data = json.loads(raw)
            if _take_list(data):
                return _finish(items, want)
        except Exception:
            pass
    # 2) 文本里夹着 JSON 数组（模型偶尔带解释）
    m = re.search(r"\[[\s\S]*?\]", raw)
    if m:
        try:
            data = json.loads(m.group(0))
            if _take_list(data):
                return _finish(items, want)
        except Exception:
            pass
    # 3) 行分隔 / ● 分隔（两者可同时出现）
    parts: list[str] = []
    for line in re.split(r"\r?\n+", raw):
        if "●" in line or "•" in line:
            parts.extend(re.split(r"[●•]", line))
        else:
            parts.append(line)
    for line in parts:
        s = line.strip()
        if not s:
            continue
        # 行首编号/项目符号
        s = re.sub(r"^\s*(?:\d+[\.\)、:：]|[-*–—]|bullet\s*\d+\s*[:：])\s*", "", s,
                   flags=re.I)
        # 去掉「1) 」但保留 FEATURE: 前缀
        s = re.sub(r"^\s*[\"'“”](.*)[\"'“”]\s*$", r"\1", s)
        if s:
            items.append(s)
    return _finish(items, want)


def _finish(items: list[str], want: int) -> list[str]:
    cleaned: list[str] = []
    seen = set()
    for it in items:
        c = clean_bullet(it)
        if c and c.lower() not in ("null", "none", "n/a"):
            key = c.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(c)
        if len(cleaned) >= want:
            break
    while len(cleaned) < want:
        cleaned.append("")
    return cleaned[:want]


def parse_search_terms(text: str, max_bytes: int = SEARCH_TERMS_SUGGEST_BYTES) -> str:
    """解析 Search Terms：小写、去停用词/标点、去重、空格连接、≤max_bytes 字节。"""
    raw = _CODE_FENCE_RE.sub("", str(text or "").strip())
    raw = re.sub(r"^\s*(?:search\s*terms?|关键词|keyword[s]?)\s*[:：]\s*", "", raw, flags=re.I)
    raw = remove_emoji(raw)
    raw = _CN_RE.sub(" ", raw)
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9\s-]", " ", raw)
    words = [w.strip("-") for w in raw.split() if w.strip("-")]
    out: list[str] = []
    seen: set[str] = set()
    for w in words:
        if len(w) < 2 or w in _SEARCH_TERM_STOPWORDS:
            continue
        if w in seen:
            continue
        # 单复数重复：已有单数形式则跳过复数；反之已有复数则用单数替换
        if w.endswith("s") and w[:-1] in seen:
            continue
        if w.endswith("s") and w[:-1] in words and w[:-1] not in seen:
            continue
        seen.add(w)
        out.append(w)
    joined = " ".join(out)
    return truncate_bytes(joined, max_bytes).strip()


__all__ = [
    "SITE_META", "DEFAULT_PROMPTS", "PROMO_WHITELIST",
    "TITLE_MAX", "BULLET_MAX_ITEMS", "BULLET_MAX_CHARS", "BULLET_SUGGEST_CHARS",
    "BULLET_MIN_AI", "BULLET_DEFAULT_COUNT", "DESCRIPTION_MAX_BYTES",
    "SEARCH_TERMS_MAX_CHARS", "SEARCH_TERMS_MAX_BYTES", "SEARCH_TERMS_SUGGEST_BYTES",
    "language_for_site", "site_meta", "is_chinese_site",
    "build_title_prompt", "build_bullets_prompt", "build_description_prompt",
    "build_search_terms_prompt", "parse_bullets", "parse_search_terms",
    "clean_title", "clean_bullet", "sanitize_description", "clean_for_site",
    "remove_emoji", "has_chinese", "strip_html", "truncate_words", "truncate_bytes",
]

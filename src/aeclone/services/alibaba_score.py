"""Alibaba 商品刊登质量评分（发布门禁）。

设计目标
--------
- 发布（op=2）前必须达到 **4.5/5**，否则给出「哪里没设置到位」的可执行诊断：
  类目属性、标题、关键词、详情、主图/详情图、视频、价格贸易、合规。
- 评分是「规则可解释」的：每个维度独立 0~5 分 + 权重，总分加权；不调用 AI、不写店铺。
- 图片尺寸检查可选（默认不做网络请求）；UI/发布门禁可传 image_checker 做真实检查。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .alibaba_payload import _looks_like_contact_number, sku_summary

PASS_SCORE = 4.5

# 维度权重（合计 100）
WEIGHTS = {
    "attributes": 20,   # 类目/必填属性/占位值
    "title": 15,        # 标题（字符/英文/合规/信息量）
    "keywords": 10,     # 关键词 3~5 英文
    "detail": 20,       # 详情 HTML/字符/图片/结构
    "images": 15,       # 主图数量/尺寸
    "video": 5,         # 主图视频
    "trade": 10,        # 价格模型/MOQ/包装/重量/HS/库存
    "compliance": 5,    # 违禁/绝对化/联系方式/占位
}

LABELS = {
    "attributes": "类目与属性",
    "title": "标题",
    "keywords": "关键词",
    "detail": "详情描述",
    "images": "图片（主图/详情图）",
    "video": "商品视频",
    "trade": "价格与贸易",
    "compliance": "合规",
}

PLACEHOLDER_VALUES = {"other", "others", "n/a", "na", "none", "unknown", "other (please specify)"}

# 这些属性里 Other/None/No 是合法值（如化学品=无、是否带电=否），不算占位
PLACEHOLDER_EXEMPT = ("chemical", "charge status", "place of origin", "origin",
                      "province", "certification", "customs")


def _hit_words(text: str, words) -> list[str]:
    """违禁词匹配：纯字母词用词边界，避免 cure 命中 securely/moisture。"""
    low = str(text or "").lower()
    out = []
    for w in words:
        wl = w.lower()
        if re.fullmatch(r"[a-z0-9 ]+", wl):
            if re.search(rf"\b{re.escape(wl)}\b", low):
                out.append(w)
        elif wl in low:
            out.append(w)
    return out

BANNED_WORDS = (
    "cheap", "cheapest", "replica", "fake", "knockoff", "copy", "best price",
    "best seller", "free shipping", "hot sale", "discount", "promotion",
    "cashback", "lowest price", "no.1", "number one", "100%", "guaranteed",
    "miracle", "cure", "fda", "ce certified", "world leading",
)
CONTACT_RE = re.compile(
    r"(https?://|www\.|@[a-z0-9.-]+\.[a-z]{2,}|\bwhatsapp\b|\bwechat\b|\bskype\b"
    r"|telegram|\bqq\b|\+?\d[\d\s\-()]{7,}\d)", re.I)
_FAB_ABSOLUTE = ("best", "100%", "guaranteed", "world leading", "no.1", "number one")


def contact_hit(text: str) -> str:
    """返回文本里第一处站外联系方式（没有则空串）。

    CONTACT_RE 的电话分支会把「1200 800 900」这类尺寸/年份数字也匹配进来，
    这里复用保存清理的同一套判定，避免对正常规格文本误报联系方式。
    """
    s = str(text or "")
    for m in CONTACT_RE.finditer(s):
        frag = m.group(0)
        if frag.startswith(("http", "www")) or "@" in frag or re.search(r"[A-Za-z]", frag):
            return frag
        if _looks_like_contact_number(frag):
            return frag
    return ""


@dataclass
class ScoreItem:
    key: str
    label: str
    score: float
    weight: float
    problems: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.score >= 4.0


@dataclass
class ScoreResult:
    total: float = 0.0
    passed: bool = False
    threshold: float = PASS_SCORE
    items: list[ScoreItem] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    placeholder_attrs: list[str] = field(default_factory=list)

    def item(self, key: str) -> ScoreItem | None:
        return next((i for i in self.items if i.key == key), None)

    def summary(self) -> str:
        return (f"评分 {self.total:.2f}/{self.threshold:.1f} · "
                + ("通过" if self.passed else "未通过"))

    def log(self) -> str:
        lines = [f"{'✓' if self.passed else '✗'} {self.summary()}"]
        for it in self.items:
            lines.append(f"  {'✓' if it.ok else '✗'} {it.label}: {it.score:.1f}/5"
                         + (f" — {'；'.join(it.problems[:2])}" if it.problems else ""))
        if self.blockers:
            lines.append("  阻断：" + "；".join(self.blockers[:5]))
        return "\n".join(lines)


# ---------------- 工具 ----------------
def _json_val(v):
    if isinstance(v, (list, dict)):
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


def _as_list(v) -> list:
    d = _json_val(v)
    return d if isinstance(d, list) else []


def _plain(html: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>[\s\S]*?</\1>", " ", str(html or ""), flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _attr_map(product: dict) -> dict[str, str]:
    rows = _as_list(product.get("attributes"))
    out = {}
    for a in rows:
        if isinstance(a, dict):
            name = str(a.get("attribute_name") or "").strip()
            if name:
                out[name.lower()] = str(a.get("value_name") or "").strip()
    return out


def _save_attr_map(save: dict) -> dict[str, str]:
    """优先用 build 后 save 的属性（含预设覆盖/自动补齐），否则用商品原属性。"""
    rows = _as_list(save.get("attributes")) if save else []
    out = {}
    for a in rows:
        if isinstance(a, dict):
            name = str(a.get("attribute_name") or "").strip()
            if name:
                out[name.lower()] = str(a.get("value_name") or "").strip()
    # 常见平台同义名：Power ↔ Power (W)、Weight ↔ Weight (kg)、Color ↔ Colors
    for alias, keys in (("power (w)", ("power",)), ("weight", ("weight (kg)",)),
                        ("colors", ("color",))):
        if alias not in out:
            for key in keys:
                if out.get(key):
                    out[alias] = out[key]
                    break
        for key in keys:
            if key not in out and out.get(alias):
                out[key] = out[alias]
    return out


def _img_urls(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x.get("url") if isinstance(x, dict) else x).strip()
                for x in raw if str(x.get("url") if isinstance(x, dict) else x).strip()]
    return [u.strip() for u in str(raw or "").replace("|", ",").split(",") if u.strip()]


def _fmt(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


# ---------------- 评分 ----------------
def score_product(product: dict, cfg=None, *, save: dict | None = None,
                  required_attrs: list | None = None,
                  image_checker=None, check_video: bool = True) -> ScoreResult:
    """对商品（edit.json 详情，可加 build 后的 save）评分。

    image_checker: 可选 callable(url) -> (ok: bool, width: int, height: int, err: str)。
                   传入时会对主图/详情图做真实尺寸检查（网络）；不传则不检查尺寸。
    """
    product = product or {}
    save = save or {}
    res = ScoreResult()
    attrs = _save_attr_map(save) or _attr_map(product)
    req = required_attrs or []

    def add_item(key, score, problems=None, fixes=None):
        score = max(0.0, min(5.0, float(score)))
        res.items.append(ScoreItem(key, LABELS[key], min(score, 5.0), WEIGHTS[key],
                                   [p for p in (problems or []) if p],
                                   [f for f in (fixes or []) if f]))
        return score

    # 1) 类目与属性
    attr_score = 5.0
    attr_problems, attr_fixes = [], []
    category = str(save.get("categoryId", product.get("categoryId")) or "").strip().lower()
    if not category or category in ("none", "null", "undefined", "0"):
        attr_score = 0.0
        attr_problems.append("未选择末级类目")
        attr_fixes.append("在「Alibaba 刊登 → 类目」搜索并选择末级叶子类目")
        res.blockers.append("未选择类目")
    if req:
        missing = [name for row in req if isinstance(row, dict)
                   and (name := str(row.get("nameEn") or row.get("nameZh") or "").strip())
                   and not attrs.get(name.lower())]
        if missing:
            attr_score = min(attr_score, 2.0)
            attr_problems.append("缺少必填属性：" + "、".join(missing[:6]))
            attr_fixes.append("在「类目」页读取必填属性，用属性预设或 AI 建议补齐")
            res.blockers.append("必填属性缺失")
        placeholders = [k for k, v in attrs.items()
                        if v.strip().lower() in PLACEHOLDER_VALUES
                        and not any(x in k for x in PLACEHOLDER_EXEMPT)]
        res.placeholder_attrs = placeholders
        if placeholders:
            attr_score = min(attr_score, 4.0 - min(1.0, len(placeholders) * 0.2))
            attr_problems.append("属性是占位值（需按真实规格修正）：" + "、".join(placeholders[:6]))
            attr_fixes.append("把这些属性改成真实值（Other 只用于确实无法确定时）")
    add_item("attributes", attr_score, attr_problems, attr_fixes)

    # 2) 标题
    title = str(save.get("subject") or product.get("subject") or "").strip()
    title_score, tp, tf = 5.0, [], []
    if not title:
        title_score = 0.0
        tp.append("标题为空")
        tf.append("在「AI 文案」生成或手填英文标题（≤128 字符）")
        res.blockers.append("标题为空")
    else:
        n = len(title)
        if n < 30:
            title_score -= 1.5
            tp.append(f"标题偏短（{n} 字符）")
            tf.append("补上核心规格/用途（建议 60~110 字符）")
        elif n > 128:
            title_score -= 2.0
            tp.append(f"标题超长（{n} > 128）")
            tf.append("压缩到 128 字符内，保留核心词")
        elif n < 50:
            title_score -= 0.5
            tp.append(f"标题略短（{n} 字符，建议 60+）")
        non_ascii = re.findall(r"[^\x00-\x7F]", title)
        if non_ascii:
            title_score -= 1.0
            tp.append("标题含非英文字符：" + "".join(sorted(set(non_ascii)))[:8])
            tf.append("Alibaba 英文标题只用 ASCII；中文/全角请翻译")
        low = title.lower()
        bad = _hit_words(low, BANNED_WORDS)
        if bad:
            title_score -= 1.5
            tp.append("标题含违禁/促销词：" + "、".join(bad[:4]))
            tf.append("删除违禁词/促销词/绝对化用语")
        if contact_hit(title):
            title_score -= 2.0
            tp.append("标题含联系方式/网址")
            tf.append("标题去掉电话/邮箱/网址/WhatsApp")
        if len(re.findall(r"[A-Za-z]{3,}", title)) < 5:
            title_score -= 0.5
            tp.append("标题有效信息词偏少（<5 个英文词）")
    add_item("title", title_score, tp, tf)

    # 3) 关键词
    kws = [k.strip() for k in re.split(r"[,;，；]", str(save.get("keywords") or product.get("keywords") or ""))
           if k.strip()]
    kw_score, kp, kf = 5.0, [], []
    if not (3 <= len(kws) <= 5):
        kw_score -= 2.0 if len(kws) < 3 else 1.5
        kp.append(f"关键词 {len(kws)} 个（要求 3~5）")
        kf.append("在「Alibaba 刊登 → AI 预演」或关键词框生成/补充 3~5 个英文关键词")
        if not kws:
            res.blockers.append("关键词为空")
    for k in kws:
        if re.search(r"[^\x00-\x7F]", k):
            kw_score -= 1.0
            kp.append(f"关键词含非英文：{k}")
            kf.append("关键词改为英文短语")
            break
    if kws and title:
        title_low = title.lower()
        if not any(k.lower() in title_low for k in kws):
            kw_score -= 0.5
            kp.append("关键词与标题完全不重叠")
            kf.append("关键词与标题使用同一组核心词")
    add_item("keywords", kw_score, kp, kf)

    # 4) 详情
    html = str(save.get("textDesc", product.get("textDesc")) or "")
    plain = _plain(html)
    detail_imgs = _new_list(save.get("detailImage", product.get("detailImage")))
    detail_score, dp, df = 5.0, [], []
    n_chars = len(plain)
    # 门槛与「提示词/文档」对齐：Alibaba 详情提示词要求 800~1500 字符、平台上限 2000，
    # 文档 ALIBABA_SCORING 的期望区间是 800~1800。旧实现把 1810→1800 之间的正常详情
    # 扣到 4.0（1747 字符也扣），与自家提示词互相矛盾，且会把 4.45 压到 4.25。
    if n_chars < 300:
        detail_score = 1.0
        dp.append(f"详情纯文本仅 {n_chars} 字符")
        df.append("用 AI 详情生成器补全（建议 800~1500 字符，含参数表/结构/应用）")
        res.blockers.append("详情过短")
    elif n_chars < 800:
        detail_score = 3.0
        dp.append(f"详情偏短（{n_chars} 字符，建议 800+）")
        df.append("补充参数表、使用场景、包装与服务（提示词建议 800~1500 字符）")
    if re.search(r"<[a-zA-Z/][^>]*>", html):
        detail_score -= 1.0
        dp.append("详情含 HTML 标签（平台 textDesc 只接受纯文本，保存时会被剥掉）")
        df.append("详情用纯文本分节（1)/2)/3) + 换行），不要 HTML")
    if len(plain) > 2000:
        detail_score -= 2.0
        dp.append(f"详情纯文本 {len(plain)} 字符，超过平台上限 2000（1000 汉字）")
        df.append("压缩详情到 2000 字符内（AI 详情会自动截断，提示词要求 800~1500 字符）")
    if len(detail_imgs) < 3:
        detail_score -= 1.0
        dp.append(f"详情图仅 {len(detail_imgs)} 张（建议 ≥3）")
        df.append("保留原详情图并在详情中插入")
    low = plain.lower()
    if contact_hit(low):
        detail_score -= 2.0
        dp.append("详情含联系方式/网址/二维码文本")
        df.append("删除电话/邮箱/WhatsApp/网址（Alibaba 禁止站外联系）")
    bad = _hit_words(low, BANNED_WORDS)
    if bad:
        detail_score -= 1.0
        dp.append("详情含违禁/促销词：" + "、".join(bad[:4]))
        df.append("删除促销/绝对化/认证无依据表述")
    add_item("detail", detail_score, dp, df)

    # 5) 图片
    main = _img_urls(save.get("mainImage", product.get("mainImage")))
    img_score, ip, ifx = 5.0, [], []
    n_main = len(main)
    if n_main == 0:
        img_score = 0.0
        ip.append("主图为空")
        ifx.append("至少上传 4~6 张 1:1 主图")
        res.blockers.append("主图为空")
    elif n_main < 3:
        img_score = 2.0
        ip.append(f"主图仅 {n_main} 张（建议 6 张）")
        ifx.append("补到 6 张 1:1 主图（白底/场景/细节）")
    elif n_main < 5:
        img_score = 4.0
        ip.append(f"主图 {n_main} 张（建议 6 张）")
        ifx.append("再补 1~2 张细节/场景图")
    if image_checker and main:
        bad_sizes = []
        for url in main[:6]:
            try:
                ok, w, h, err = image_checker(url)
            except Exception as ex:  # noqa: BLE001
                ok, w, h, err = False, 0, 0, str(ex)
            if not ok:
                bad_sizes.append(f"{str(url)[-28:]}（{err or f'{w}x{h}'}）")
            elif w and h and min(w, h) < 800:
                bad_sizes.append(f"{str(url)[-28:]}（{w}x{h} <800）")
        if bad_sizes:
            img_score = max(1.0, img_score - min(3.0, len(bad_sizes)))
            ip.append("主图尺寸/可访问性不达标：" + "；".join(bad_sizes[:3]))
            ifx.append("主图建议 ≥1000×1000、1:1、白底无 Logo/水印")
    add_item("images", img_score, ip, ifx)

    # 6) 视频
    video_score, vp, vf = 5.0, [], []
    has_video = False
    if check_video:
        for key in ("mainVideoId", "detailVideoId", "videoUrl", "video"):
            v = save.get(key) or product.get(key)
            if v not in (None, "", 0, "0", "None"):
                has_video = True
                break
        if not has_video:
            video_score = 1.0
            vp.append("没有商品视频（mainVideoId/detailVideoId 为空）")
            vf.append("在店小秘/卖家中心上传主图视频；当前保存接口未包含视频字段，"
                      "视频需在平台侧补充后重新评分")

    add_item("video", video_score, vp, vf)

    # 7) 价格与贸易
    trade = _json_val(save.get("wholesaleTrade") or product.get("wholesaleTrade"))
    trade = trade if isinstance(trade, dict) else {}
    ptype = str(save.get("priceType") or product.get("priceType") or "").strip()
    tiers = _new_list(save.get("bulkDiscountPrices")) or _as_list(product.get("bulkDiscountPrices"))
    fob = _json_val(save.get("sourcingTrade") or product.get("sourcingTrade"))
    fob = fob if isinstance(fob, dict) else {}
    trade_score, trp, trf = 5.0, [], []
    if ptype == "ladder_price":
        prices = [float(t.get("price") or 0) for t in tiers if isinstance(t, dict)]
        if not prices or min(prices) <= 0:
            trade_score -= 2.0
            trp.append("阶梯价为空或价格 <=0")
            trf.append("在「价格与贸易」填阶梯价 JSON")
    elif ptype == "sku_price":
        # 真实 edit.json 的 productSku 是 JSON 字符串（dict：attributes + special_skus），
        # 之前用 _as_list 只认数组，dict 形状会被判成「没有 SKU」，
        # 导致所有 sku_price 商品价格维度 2.5 分、总分被压到 4.25（真实回归 2026-11-22）。
        _has_sku, _valid_skus, _has_dims = sku_summary(
            save.get("productSku") or product.get("productSku"))
        if not _has_sku:
            trade_score -= 2.5
            trp.append("按规格定价但缺少 SKU 数据")
            trf.append("补 SKU 或改用按数量定价")
        elif not _has_dims:
            trade_score -= 1.5
            trp.append("productSku 缺少 SKU 维度属性（attributes）")
            trf.append("在「价格与贸易 → SKU」补规格维度（如 Color/Size）")
        elif _valid_skus <= 0:
            trade_score -= 2.5
            trp.append("productSku 无有效 SKU（全部 exclude 或无价格/规格）")
            trf.append("补有效 SKU 价格/编码，或改用按数量定价")
    elif ptype == "fob_price":
        if not (fob.get("fob_min_price") and fob.get("fob_max_price") and fob.get("fob_currency")):
            trade_score -= 2.5
            trp.append("FOB 价格区间/币种不全")
            trf.append("填 FOB 币种、最低价、最高价")
    else:
        trade_score -= 2.0
        trp.append(f"价格类型异常：{ptype or '空'}")
        trf.append("选择按数量/按规格/单一区间 FOB")
    try:
        moq = int(float(trade.get("min_order_quantity") or 0))
    except Exception:
        moq = 0
    if moq <= 0:
        trade_score -= 1.0
        trp.append("MOQ 未填写")
        trf.append("填 MOQ（≥1）")
    pkg = str(trade.get("package_size") or "").strip()
    if not re.match(r"^\d+(\.\d+)?[xX*]\d+(\.\d+)?[xX*]\d+(\.\d+)?$", pkg):
        trade_score -= 1.0
        trp.append(f"包装尺寸格式不对：{pkg or '空'}")
        trf.append("包装尺寸填 LxWxH（如 300X200X150）")
    try:
        weight = float(trade.get("weight") or 0)
    except Exception:
        weight = 0
    if weight <= 0:
        trade_score -= 1.0
        trp.append("包装重量未填写")
        trf.append("填包装重量 kg")
    hs_rows = trade.get("tariffsHsCode") or []
    hs_ok = any(str((r or {}).get("hsCode") or "").strip() for r in hs_rows
                if isinstance(r, dict))
    if not hs_ok and ptype != "fob_price":
        trade_score -= 1.0
        trp.append("HS 编码为空（tariffsHsCode）")
        trf.append("在「价格与贸易 → HS 编码」填写真实 HS Code")
    logistics = str(save.get("logisticsProperty") or product.get("logisticsProperty") or "").strip()
    if (trade.get("package_size") or trade.get("weight")) and not logistics:
        trade_score -= 1.0
        trp.append("重量/尺寸已填但缺少物流属性（logisticsProperty）")
        trf.append("在「价格与贸易」填物流属性（如 general_cargo_0），或把重量/尺寸全部清空")
    periods = trade.get("deliver_periods") or []
    if not (isinstance(periods, list) and any(
            isinstance(x, dict) and int(x.get("process_period") or 0) > 0 for x in periods)):
        trade_score -= 1.0
        trp.append("发货期（deliver_periods）未填写")
        trf.append("在「价格与贸易」填发货期天数（平台发布必填）")
    inv = trade.get("inventory") or []
    if not any(int((x or {}).get("value") or 0) > 0 for x in inv if isinstance(x, dict)):
        trade_score -= 0.5
        trp.append("库存为 0/未填写")
        trf.append("填可用库存")
    add_item("trade", trade_score, trp, trf)

    # 8) 合规
    comp_score, cp, cf = 5.0, [], []
    corpus = " ".join([title, " ".join(kws), plain]).lower()
    bad = _hit_words(corpus, BANNED_WORDS)
    if bad:
        comp_score -= 1.5
        cp.append("含违禁/绝对化词：" + "、".join(bad[:5]))
        cf.append("删除违禁词后重新评分")
    if contact_hit(corpus):
        comp_score -= 2.0
        cp.append("含联系方式/网址/二维码")
        cf.append("删除所有站外联系方式")
    if res.placeholder_attrs:
        comp_score -= min(1.5, 0.3 * len(res.placeholder_attrs))
        cp.append("存在占位属性值")
        cf.append("按真实规格修正占位属性")
    add_item("compliance", comp_score, cp, cf)

    # 汇总
    total_w = sum(i.weight for i in res.items) or 1
    # 门禁阈值是两位小数：先按两位四舍五入再比较，避免加权和 4.495 显示成
    # 「4.50/5」却仍判不通过（旧实现会给出自相矛盾的「4.50 未达 4.5」提示）。
    res.total = round(sum(i.score * i.weight for i in res.items) / total_w, 2)
    res.fixes = [f for i in res.items for f in i.fixes]
    # 去重保序
    res.fixes = list(dict.fromkeys(res.fixes))
    res.blockers = list(dict.fromkeys(res.blockers))
    # 硬阻断：任何维度 0 分 or 有 blockers 都不通过
    res.passed = (res.total >= PASS_SCORE) and not res.blockers and \
        all(i.score > 0 for i in res.items)
    return res


def _new_list(v) -> list:
    d = _json_val(v)
    return d if isinstance(d, list) else []


# ---------------- 图片尺寸探测（可选，真实网络） ----------------
def probe_image(url: str, min_side: int = 800, timeout: int = 15):
    """下载图片头部并读取尺寸；返回 (ok, width, height, err)。"""
    try:
        import io
        import requests
        from PIL import Image
        r = requests.get(str(url), timeout=timeout, stream=True)
        r.raise_for_status()
        data = b""
        for chunk in r.iter_content(65536):
            data += chunk
            if len(data) > 3 * 1024 * 1024:
                break
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
        return (w >= min_side and h >= min_side), w, h, ""
    except Exception as ex:  # noqa: BLE001
        return False, 0, 0, f"{type(ex).__name__}: {str(ex)[:60]}"

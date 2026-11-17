"""提示词预设与试写/对比工具。

三种标题风格：
- standard  标准型：合规、结构清晰、关键词适中（默认）
- keyword   关键词型：堆叠高搜索量词，适合新品抢曝光
- longtail  长尾型：低竞争长尾词，适合精准流量
"""
from __future__ import annotations

import re
from dataclasses import dataclass

TITLE_PRESETS: dict[str, tuple[str, str]] = {
    "standard": ("标准型（稳妥合规）",
                 "你是资深速卖通（AliExpress）英文刊登运营。请把用户给出的中文商品标题改写为专业英文标题。\n"
                 "硬性规则：\n"
                 "1) 总长度不超过 125 个字符（含空格），超长必须删减，不得截断单词；\n"
                 "2) 结构：核心产品词 + 关键规格/型号 + 主要卖点 + 用途/适用场景；\n"
                 "3) 品牌词一律前置 for（例：for CAT），不得暗示官方授权；\n"
                 "4) 禁止：best/cheapest/No.1/100% 等绝对化用语、促销词（free shipping、hot sale）、表情符号、全大写堆砌、联系方式；\n"
                 "5) 数字与单位保留（如 3 Ton、20m、220V），单位用英文缩写；\n"
                 "6) 只输出一行英文标题，不要引号、不要解释、不要中文。"),
    "keyword": ("关键词型（抢搜索曝光）",
                "你是速卖通爆款标题优化师。把中文标题改写为高搜索权重的英文标题。\n"
                "规则：\n"
                "1) ≤125 字符；\n"
                "2) 前 60 字符内必须出现最核心的 2~3 个大词（买家最常搜的词），后段补规格与场景长词；\n"
                "3) 使用买家常用英文搜索词，不用生僻同义词；多写可被搜索的名词短语，少用形容词堆砌；\n"
                "4) 品牌词前置 for；禁止 best/cheap/hot sale/free shipping/表情/全大写；\n"
                "5) 保留关键数字与单位；\n"
                "6) 只输出一行英文标题，不要解释。"),
    "longtail": ("长尾型（低竞争精准流量）",
                 "你是速卖通长尾关键词专家。把中文标题改写为面向精准需求的长尾英文标题。\n"
                 "规则：\n"
                 "1) ≤125 字符；\n"
                 "2) 结构：核心产品词 + 规格/型号 + 2~3 个具体使用场景或人群限定词（如 for narrow space、for indoor warehouse）；\n"
                 "3) 优先低竞争、高转化的限定词，避免与头部卖家正面撞词；\n"
                 "4) 品牌词前置 for；禁止绝对化用语与促销词；\n"
                 "5) 保留数字与单位；\n"
                 "6) 只输出一行英文标题，不要解释。"),
}


@dataclass
class Variant:
    text: str
    ok: bool = True
    error: str = ""


def _clean_title(s: str) -> str:
    s = (s or "").strip().strip('"').strip("'").strip()
    # 只去掉序号/项目符号（1. / 1) / - / • / *），保留开头的数字（如 3 Ton）
    s = re.sub(r"^\s*(?:\d{1,2}[\.,、\)]|[•\-\*])\s+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()[:200]


def _truncate_words(text: str, limit: int = 125) -> str:
    """按单词边界截断到 limit 字符，避免截断半个单词。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-")


def title_variants(client, model: str, prompt: str, source_title: str, n: int = 3,
                   max_tokens: int = 400) -> list[Variant]:
    """用同一条提示词生成 n 个候选标题（一次调用，要求模型分行输出）。"""
    ask = (f"请基于下面的中文商品信息，按系统规则生成 {n} 条不同的英文标题候选，"
           f"每条独占一行，不要编号、不要解释、不要引号：\n{source_title}")
    try:
        out = client.chat(model, [{"role": "system", "content": prompt},
                                  {"role": "user", "content": ask}], max_tokens=max_tokens)
    except Exception as ex:  # noqa: BLE001
        return [Variant("", False, str(ex)[:160])]
    lines = [_clean_title(x) for x in (out or "").splitlines() if x.strip()]
    lines = [x for x in lines if x]
    if not lines:
        return [Variant("", False, "模型没有返回标题")]
    return [Variant(_truncate_words(x, 125)) for x in lines[:n]]


def score_title(t: str, keywords: list[str] | None = None) -> dict:
    """本地粗评分：长度、词数、是否含数字/单位、是否含违禁词。"""
    words = len(re.findall(r"[A-Za-z0-9]+", t))
    has_num = bool(re.search(r"\d", t))
    banned = [w for w in ("best", "cheapest", "no.1", "100%", "hot sale", "free shipping") if w in t.lower()]
    score = 100
    if len(t) > 125:
        score -= 30
    if len(t) < 40:
        score -= 15
    if not has_num:
        score -= 10
    if banned:
        score -= 20
    if keywords:
        hit = sum(1 for k in keywords if k.lower() in t.lower())
        score += min(10, hit * 3)
    return {"score": max(0, min(120, score)), "chars": len(t), "words": words,
            "has_number": has_num, "banned": banned}

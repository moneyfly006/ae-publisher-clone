"""Effective platform prompt defaults shared by UI, batch execution and auditing."""
ALIBABA_PROMPTS = {
    "title": (
        "你是资深 Alibaba.com 国际站（ICBU）工业品与制造业 SEO 运营专家。"
        "请根据商品原始标题、型号、规格和用途，生成一条可直接上架的英文商品标题。\n"
        "硬性要求：\n"
        "1) 保留原文中的真实型号、关键材质（304 SS / carbon steel / UHMWPE 等）、"
        "核心规格（功率/流量/吨位/尺寸/件数）和真实用途；\n"
        "2) 结构建议：品名 + 材质/规格 + 核心卖点/用途，自然融入 2~4 个 B2B 搜索词；\n"
        "3) 全英文，≤128 字符（尽量 ≤120）；\n"
        "4) 禁止中文、emoji、货币符号、引号、绝对化用语（best/No.1/cheapest/100%）"
        "和促销词（sale/discount/free）；\n"
        "5) 不得编造原文没有的型号、认证（CE/ISO/SGS）、产地；\n"
        "6) 只输出一行英文标题，不要解释或 Markdown。"
    ),
    "detail": (
        "你是专业的 Alibaba.com 国际站工业品外贸营销文案专家。"
        "请根据商品原始信息，生成英文产品详情 HTML。\n"
        "内容结构（按此顺序）：\n"
        "① 产品定义与核心用途（1~2 句）\n"
        "② 核心参数表（只用原文已有的数据，格式为 <table> 或 <ul><li>）\n"
        "③ 结构说明与操作要点（2~3 段）\n"
        "④ 适用行业与客户场景\n"
        "⑤ 包装与售后/认证说明（认证只写原文有的）\n\n"
        "排版规则：\n"
        "- 只用 <h3><p><ul><li><table><tr><td><b><br> 标签；\n"
        "- 图片占位用 <img data-detail-img=\"N\">，按章节自然插入，至少 2 张；\n"
        "- 纯文本部分 1500~3000 字符；\n\n"
        "合规禁止：联系方式、邮箱、电话、网址、WhatsApp/WeChat/QQ、二维码、"
        "返现促销、绝对化表述、医疗功效、侵权品牌词、编造参数。\n"
        "只输出 HTML 片段，不要 Markdown 代码块。"
    ),
    "keyword": (
        "你是 Alibaba.com 国际站 B2B 关键词专家。"
        "请根据商品标题与描述，生成 3~5 个英文搜索关键词短语，英文逗号分隔。\n"
        "规则：\n"
        "1) 1 个核心产品词 + 1~2 个规格/材质词 + 1~2 个用途/行业词；\n"
        "2) 每个关键词 1~4 个英文单词，使用真实买家搜索习惯；\n"
        "3) 不堆砌、不重复核心词、不写品牌词和促销词；\n"
        "4) 只输出关键词，不要解释。"
    ),
}


def prompt_specs(platform, section="text"):
    """Return actual config keys and defaults read by the corresponding backend."""
    if platform == "alibaba":
        return [("alibaba_" + kind + "_prompt", label, ALIBABA_PROMPTS[kind]) for kind, label in
                (("title", "标题"), ("detail", "详情"), ("keyword", "关键词"))] if section == "text" else []
    if platform in ("amazon", "tiktok"):
        from importlib import import_module
        module = import_module("aeclone.services." + platform + "_prompts")
        labels = {"title": "标题", "highlight": "商品亮点", "bullets": "五点描述", "description": "产品描述", "search_terms": "搜索词",
                  "key_features": "五点卖点（US）", "sku": "SKU 编码", "attribute": "属性建议"}
        kinds = ("title", "highlight", "bullets", "description", "search_terms") if platform == "amazon" else (
            "title", "description", "key_features", "search_terms")
        # Auxiliary collection rules live in collect_ai and have their own
        # response contracts. Don't expose the old scalar rules beside them.
        if section != "text":
            return []
        return [(module._PROMPT_CFG_KEYS[k], labels[k], module.DEFAULT_PROMPTS[k]) for k in kinds]
    if platform == "shopee" and section == "text":
        from .shopee import DEFAULT_PROMPTS
        return [("shopee_" + kind + "_prompt", label, DEFAULT_PROMPTS[kind]) for kind, label in
                (("title", "标题"), ("description", "产品描述"))]
    return []


def effective_prompt(cfg, key, default):
    return str(getattr(cfg, key, "") or "").strip() or default


TEXT_STYLES = {
    "default": ("通用保真", ""),
    "technical": ("专业参数", "优先清楚表达原文已有的型号、材质、规格和使用条件。没有的参数省略。"),
    "use_case": ("使用场景", "围绕原资料能支持的使用场景和操作方式组织文案，不新增效果承诺。"),
    "concise": ("精简合规", "使用简短清晰的句子，删除空泛修饰和促销内容，保留真实事实与平台要求的输出结构。"),
    "search": ("自然搜索词", "用自然流畅的品类词与用途词表达，不堆砌关键词，不引入竞品品牌，标题同词不超过两次。"),
}
IMAGE_STYLES = {
    "white": ("纯白背景", "将商品图处理为纯白背景棚拍图。保持商品形状、结构、颜色、材质和比例，不新增文字或物件。"),
    "watermark": ("移除叠加水印", "移除叠加水印、促销文字、二维码并自然补全背景。保留商品本体、真实商标、形状、颜色、材质及比例。"),
    "translate": ("翻译图片文字", "将商品说明翻译为目标站点语言，保持排版清晰、数字和规格准确。保留商品本体，不增加任何事实。"),
    "studio": ("柔和棚拍背景", "用简洁柔和的中性棚拍背景突出商品，改善照明和阴影。保持商品原有形状、结构、颜色、材质与比例。"),
}


def styled_prompt(default, style):
    if style not in TEXT_STYLES:
        raise ValueError("未知文案预设")
    suffix = TEXT_STYLES[style][1]
    return default + ("\n补充表达要求：" + suffix if suffix else "")


def ai_fields(platform):
    return {"alibaba": ("title", "description", "keywords"),
            "amazon": ("title", "highlight", "bullets", "description", "search_terms"),
            "tiktok": ("title", "description", "key_features", "search_terms"),
            "shopee": ("title", "description")}.get(platform, ())


def resolve_config_prompts(data, platform):
    for section in ("text", "stock", "category"):
        for key, _, default in prompt_specs(platform, section):
            data[key] = str(data.get(key) or "").strip() or default
    # A blank editor means restore the built-in image rule, never silently skip.
    from ..config import DEFAULTS
    key = platform + "_collect_image_prompt"
    data[key] = str(data.get(key) or "").strip() or DEFAULTS[key]
    from .collect_ai import SKU_PROMPT, ATTRIBUTE_PROMPT
    for suffix, default in (("sku_prompt", SKU_PROMPT), ("attribute_prompt", ATTRIBUTE_PROMPT)):
        key = platform + "_collect_" + suffix
        data[key] = str(data.get(key) or "").strip() or default
    return data

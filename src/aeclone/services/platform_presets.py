"""Effective platform prompt defaults shared by UI, batch execution and auditing."""
ALIBABA_PROMPTS = {
    "title": "根据当前商品的真实标题、参数与用途生成 Alibaba 英文商品标题，最多128字符。保留型号、材质与关键规格；不新增品牌、认证、产地、促销或未经提供的参数。仅输出标题。",
    "detail": "根据当前商品真实资料生成 Alibaba 英文产品详情，包含用途、已知参数和使用说明。不得编造性能、认证、产地、工厂年限或联系方式。保留原详情图片占位，输出简洁 HTML 片段，纯文本不超过2000字符。",
    "keyword": "根据当前商品的真实标题与描述输出3至5个相关英文搜索短语，用逗号分隔。保留商品所属品类，不添加其他行业、促销、品牌或未经证实的特征。",
}


def prompt_specs(platform, section="text"):
    """Return actual config keys and defaults read by the corresponding backend."""
    if platform == "alibaba":
        return [("alibaba_" + kind + "_prompt", label, ALIBABA_PROMPTS[kind]) for kind, label in
                (("title", "标题"), ("detail", "详情"), ("keyword", "关键词"))] if section == "text" else []
    if platform in ("amazon", "tiktok"):
        from importlib import import_module
        module = import_module("aeclone.services." + platform + "_prompts")
        labels = {"title": "标题", "bullets": "五点描述", "description": "产品描述", "search_terms": "搜索词",
                  "key_features": "五点卖点（US）", "sku": "SKU 编码", "attribute": "属性建议"}
        kinds = ("title", "bullets", "description", "search_terms") if platform == "amazon" else (
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
            "amazon": ("title", "bullets", "description", "search_terms"),
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

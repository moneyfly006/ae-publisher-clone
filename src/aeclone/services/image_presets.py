"""图片处理中文预设：界面显示中文，底层用英文模板（效果更稳）。

结构：{group: [{key, zh, en, desc}]}
- zh   : 下拉里给用户看的中文名
- en   : 真正发给模型的英文提示词（用户可改）
- desc : 中文说明（展示在提示词下方）
"""
from __future__ import annotations

PRESETS: dict[str, list[dict]] = {
    "main": [
        {"key": "white_studio", "zh": "白底棚拍（合规主图）",
         "desc": "纯白背景、柔光、产品居中，不加文字/水印，符合速卖通主图规范",
         "en": "Put the product on a pure white background (#FFFFFF), soft diffused studio lighting, "
               "product centered occupying about 80% of the frame, keep shape/color/texture unchanged. "
               "No text, no watermark, no logo, no props, no border."},
        {"key": "scene", "zh": "换成使用场景",
         "desc": "例如工地/车间/户外，自然光，产品不变形",
         "en": "Place the product in a realistic usage scene matching its purpose (construction site, "
               "workshop or outdoor job site), natural daylight, product remains the focus and unchanged "
               "in shape/color/material, realistic contact shadow. No text, no watermark."},
        {"key": "detail_closeup", "zh": "细节特写",
         "desc": "突出材质/焊缝/轮子等工艺细节，背景虚化",
         "en": "Close-up product detail shot highlighting material, welding seams or wheel quality, "
               "shallow depth of field with blurred background, sharp focus on the product detail, "
               "accurate colors, no text, no watermark."},
        {"key": "multi_hero", "zh": "多图合成主图（用参考图）",
         "desc": "把提供的多张素材合成一张主图，统一背景光线，产品比例正确",
         "en": "Combine the provided reference photos into ONE clean e-commerce hero image: same product, "
               "consistent background and lighting, correct product proportions, no duplicated parts. "
               "No text, no watermark."},
        {"key": "clean_bg", "zh": "清理背景杂物",
         "desc": "去掉背景里的杂物/人影/电线，保留产品",
         "en": "Remove distracting objects, people, wires and clutter from the background, keep the product "
               "exactly unchanged, extend a clean neutral background naturally. No text, no watermark."},
    ],
    "detail": [
        {"key": "translate_en", "zh": "图片文字翻成英文",
         "desc": "保留原排版/字体/配色，只把文字翻译成英文",
         "en": "Translate every visible text element in the image into natural English suitable for "
               "e-commerce, keep the original typography, layout, colors and positions unchanged, "
               "do not translate the product itself, do not add new text."},
        {"key": "spec_sheet", "zh": "参数规格图",
         "desc": "生成英文参数表图（型号/功率/尺寸/重量/材质）",
         "en": "Create a clean English specification graphic: list Model, Power, Size, Weight and Material "
               "on a light background with clear typography, keep the product photo on one side, "
               "no promotional claims."},
        {"key": "steps", "zh": "使用步骤图",
         "desc": "3 步图解，简洁箭头排版",
         "en": "Create a 3-step usage instruction graphic using the product photos: numbered steps 1-2-3, "
               "clean arrows, short English captions, white background, no clutter."},
        {"key": "selling_points", "zh": "卖点图（详情图用）",
         "desc": "3 条英文卖点，留白排版",
         "en": "Design a detail-page selling-points image: product photo with 3 short English benefit lines, "
               "clean layout with generous white space, no absolute claims, no contact info."},
        {"key": "scene_detail", "zh": "场景展示图",
         "desc": "产品在使用场景中，带环境氛围",
         "en": "Show the product in a realistic usage scene for the detail page, natural lighting, "
               "keep product shape/color unchanged, no text, no watermark."},
    ],
    "sku": [
        {"key": "sku_white", "zh": "SKU 白底居中",
         "desc": "白底、居中、1:1，不裁切产品",
         "en": "Put the SKU variant product on a pure white background, centered, 1:1 square, "
               "no cropping of the product, soft even lighting, no text, no watermark."},
        {"key": "sku_label", "zh": "加英文款式标注",
         "desc": "角落小字标注颜色/款式，不遮挡产品",
         "en": "Add a small English variant label (color or model) in the corner of the image, "
               "small clean typography, must not cover the product, white background, no watermark."},
        {"key": "sku_unify", "zh": "统一角度/尺寸",
         "desc": "把同一排 SKU 图的视角与留白统一",
         "en": "Normalize the product framing: same camera angle, same product scale and white margins "
               "as the reference, pure white background, no text."},
    ],
}

TRANSLATE_TARGETS = [("en", "英文"), ("ru", "俄语"), ("es", "西班牙语"), ("pt", "葡语"), ("fr", "法语")]

TRANSLATE_TEMPLATE = (
    "Translate all visible text in this image into {lang_name} ({lang}), keep the original layout, "
    "typography, colors and positions exactly unchanged, do not translate the product itself, "
    "do not add any new text or watermark."
)


def presets_for(group: str) -> list[dict]:
    return PRESETS.get(group, PRESETS["main"])


def labels_for(group: str) -> list[tuple[str, str]]:
    return [(p["key"], p["zh"]) for p in presets_for(group)]


def get_preset(group: str, key: str) -> dict | None:
    for p in presets_for(group):
        if p["key"] == key:
            return p
    return None


def translate_prompt(lang: str = "en") -> str:
    name = dict(TRANSLATE_TARGETS).get(lang, "英文")
    return TRANSLATE_TEMPLATE.format(lang=lang, lang_name=name)

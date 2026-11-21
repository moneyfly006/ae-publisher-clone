"""Alibaba 刊登中 AI 的可用点与提示词（规则来自实测字段，见 docs/ALIBABA_RULES.md）。

原则：
- AI 只产出「文本/建议/分类候选/属性候选」，ID 与枚举值必须再用平台数据校验；
- 参数、认证、型号不得编造；
- 人机比例、白底、水印等图片规则由 AI 视觉检查 + 本地处理共同保证。
"""
from __future__ import annotations

import json

# 字段 → AI 用途（True=适合 AI；False=规则/数据驱动，不要让 AI 决定）
AI_TOUCHPOINTS = {
    "categoryId": "建议（AI 给候选类目，必须用 searchCategory 校验并选叶子）",
    "subject": "生成/优化（≤128 字符，英文）",
    "keywords": "生成（3~5 个，英文，覆盖核心词+长尾）",
    "textDesc": "生成（卖点/参数/场景/FAQ，禁止编造参数与认证）",
    "detailImage": "生成/处理（白底、去水印、翻译、场景图、合规检查）",
    "mainImage": "处理（白底/比例/水印/人机比例检查）",
    "attributes": "推断候选（从标题/详情/图片推断 Voltage/Power/Weight/Model 等，需映射到类目值 ID）",
    "companyDesc": "生成（公司介绍，使用预设信息+真实产能，禁止编造认证）",
    "companyFaqDesc": "生成（FAQ：MOQ、交期、样品、定制、质保）",
    "tariffsHsCode": "建议（按品类给候选 HS，需人工/规则确认）",
    "weight": "估算（图片+品类估重，仅作默认值，可手动改）",
    "package_size": "估算（按同类设备常识，标记为估算）",
    "price": "不建议直接定价；由成本/倍率/阶梯规则计算，AI 可在详情中描述价值",
    "min_order_quantity": "规则（预设），AI 不决定",
    "unit_type": "规则（店铺单位表）",
    "groupId": "规则（店铺产品分组）",
}

# 这些字段绝不允许 AI 编造：只能来自原文/预设/平台接口
NO_FABRICATION = ("Model Number", "Certification", "Place of Origin", "Warranty",
                  "Voltage", "Power", "Weight (kg)", "Machinery Capacity")

TITLE_PROMPT = (
    "你是 Alibaba.com 机械类目运营。基于商品原始信息生成英文产品名称："
    "保留产品名、型号、核心规格（功率/容量/尺寸/材质）与用途；"
    "自然融入 2~4 个搜索词；不超过 128 字符；"
    "禁止编造原文没有的型号、参数、认证；只输出一行标题。"
)

KEYWORDS_PROMPT = (
    "根据产品名称与类目，生成 3~5 个英文关键词（逗号分隔）："
    "1 个核心产品词 + 1~2 个属性/规格词 + 1~2 个场景/行业词；"
    "不要重复、不要品牌词、不要堆砌；只输出关键词。"
)

DETAIL_PROMPT = (
    "你是工业设备外贸文案专家。写 Alibaba 商品详情（英文）："
    "结构：产品用途与场景 → 核心参数（只用原文数据）→ 结构与操作 → 适用行业 → "
    "定制/服务/质保；使用简短段落与参数列表；"
    "禁止联系方式、网址、促销、绝对化与医疗表述；禁止编造型号/认证/参数。"
)

COMPANY_PROMPT = (
    "根据公司预设信息写英文公司介绍（80~150 词）：成立时间/工厂面积/产线/主要市场/"
    "质量与服务；禁止编造认证、客户名与数据。"
)

FAQ_PROMPT = (
    "生成 4~6 条英文 FAQ，覆盖：MOQ、交期、样品、定制(logo/包装)、质保、运输；"
    "回答简短真实，不编造政策与数字。"
)

IMAGE_PROMPT = (
    "生成机械产品营销图：产品置于真实场景；结构颜色标识不变；"
    "如出现人物，人体与设备比例必须真实（人物不得高于设备主体），操作姿势合理；"
    "不加文字/水印/Logo；1:1 或 3:4。"
)

ATTRIBUTE_PROMPT = (
    "从商品标题、详情和图片推断以下类目属性的值：{names}。"
    "只能从给定候选值中选择；无法确定返回空字符串。"
    "严格输出 JSON 对象：{{\"属性名\": \"候选值\"}}，不要解释。"
)


def build_attribute_prompt(attr_defs: list[dict]) -> str:
    names = [str(a.get("nameEn") or a.get("nameZh") or "") for a in attr_defs]
    return ATTRIBUTE_PROMPT.format(names=", ".join(n for n in names if n))


def map_attribute_suggestion(suggestion: dict, attr_defs: list[dict]) -> dict:
    """把 AI 给的属性值映射到平台枚举（value_id/name），无法匹配则丢弃。"""
    out = {}
    for d in attr_defs:
        name = str(d.get("nameEn") or d.get("nameZh") or "").strip()
        if not name:
            continue
        val = (suggestion or {}).get(name)
        if val in (None, ""):
            continue
        try:
            values = d.get("values")
            if isinstance(values, str):
                values = json.loads(values)
            values = values or []
        except Exception:
            values = []
        for v in values:
            vn = str(v.get("name_en") or v.get("name") or "").strip()
            if vn.lower() == str(val).strip().lower():
                out[name] = {"value_id": str(v.get("id")), "value_name": vn}
                break
    return out

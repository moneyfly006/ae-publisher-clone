"""Alibaba 刊登中 AI 的可用点与提示词（规则来自实测字段，见 docs/ALIBABA_RULES.md）。

原则：
- AI 只产出「文本/建议/分类候选/属性候选」，ID 与枚举值必须再用平台数据校验；
- 参数、认证、型号不得编造；
- 人机比例、白底、水印等图片规则由 AI 视觉检查 + 本地处理共同保证。
"""
from __future__ import annotations

import json
import re

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

# AI 兜底建议的系统提示（只在本地规则补不上时使用；结果必须再经平台枚举校验）
ATTRIBUTE_SUGGEST_SYSTEM = (
    "你是 Alibaba.com 类目属性填写助手。只根据用户给出的商品原文信息推断属性值，"
    "不得编造原文没有的型号、认证、产地、功率、重量、质保等参数；"
    "有候选值的属性只能从候选值中选择；原文没有依据时返回空字符串。"
    "只输出一个 JSON 对象，不要解释、不要 Markdown。"
)

ATTRIBUTE_SUGGEST_TEMPLATE = (
    "商品信息：\n{source}\n\n待补属性（只填这些）：\n{fields}\n\n"
    "规则：\n"
    "1) 有候选值的属性只能原样选择其中一个候选值；\n"
    "2) 没有候选值（自由填写）的属性只给商品原文里出现过的数字/型号文本；\n"
    "3) 没有依据就返回空字符串，禁止猜测；\n"
    '4) 严格输出 JSON 对象：{{"属性名": "值"}}，不要输出其它内容。'
)


def _cfg_get(cfg, key: str, default=""):
    """cfg 可能是 Config 对象、dict 或 None。"""
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _json_list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            d = json.loads(v)
            return d if isinstance(d, list) else []
        except Exception:
            return []
    return []


def _parse_ai_json(text: str) -> dict:
    """从 AI 回复里取出 JSON 对象（容忍 ```json 代码块/前后废话）。"""
    s = str(text or "").strip()
    if not s:
        return {}
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else {}
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            d = json.loads(s[i:j + 1])
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    return {}


def ai_suggest_attributes(product: dict, defs: list | None, cfg=None,
                          missing: list | None = None, client=None,
                          max_fields: int = 12) -> dict:
    """AI 兜底：只在本地规则无法补齐属性时，从商品原文推断候选值（不写平台）。

    参数
    ----
    product : edit.json 商品（用标题/关键词/原属性/详情做依据）
    defs    : 类目属性定义（attributeList，含 required/values/inputType）
    cfg     : 配置（api_base/api_key/timeout）；用于构造 ApiClient 与模型名
    missing : 只对这些属性名给建议（通常是 autofill 的 unresolved）；为空则挑所有必填
    client  : 可注入的 ApiClient/假客户端（测试用；必须实现 chat(model, messages)）

    返回
    ----
    ``{"called", "model", "suggestions": {名: {value_id, value_name, needs_confirm,
    source}}, "notes", "unresolved", "error", "fields"}``

    - 只返回**建议**，调用方/UI 展示给人确认，绝不自动写平台；
    - 每个字段独立 try/except：一个字失败不影响其它字段，整体失败只回 error；
    - 枚举属性必须命中平台候选值且有 value_id 才返回（不写空 id）；
    - NO_FABRICATION 里的属性一律标 needs_confirm（禁止自动写入）。
    """
    result = {"called": False, "model": "", "suggestions": {}, "notes": [],
              "unresolved": [], "error": "", "fields": []}
    try:
        from .alibaba_payload import (
            _build_def_index, _candidate_sample, _def_label, _is_free_text,
            _match_def_for_attr, _norm, _value_entries, resolve_attr_value,
        )
    except Exception as e:  # noqa: BLE001
        result["error"] = f"属性解析依赖不可用：{type(e).__name__}: {e}"
        return result

    defs = [d for d in (defs or []) if isinstance(d, dict)]
    attrs = [a for a in _json_list((product or {}).get("attributes"))
             if isinstance(a, dict)]
    by_name, by_id = _build_def_index(defs)

    def _local_ok(d: dict) -> bool:
        """本地（商品原值/预设）是否已经能补齐该属性。"""
        attr = next((a for a in attrs if _match_def_for_attr(a, by_name, by_id) is d), None)
        if not attr:
            return False
        v = str(attr.get("value_name") or "").strip()
        if not v:
            return False
        # Other/None/Unknown 等占位值不算「已补齐」，仍需 AI 建议。
        if v.strip().casefold() in {"other", "others", "none", "unknown", "n/a", "na"}:
            return False
        if not _value_entries(d):
            return _is_free_text(d)
        r = resolve_attr_value(d, v)
        return bool(r.get("matched") and r.get("value_id") not in (None, ""))

    want = {_norm(str(x)) for x in (missing or []) if str(x or "").strip()}
    targets: list[dict] = []
    for d in defs:
        name = _def_label(d)
        if not name:
            continue
        if int(d.get("required") or 0) != 1 and not want:
            continue
        if want and _norm(name) not in want and _norm(d.get("nameZh")) not in want:
            continue
        if _local_ok(d):
            continue
        targets.append(d)
        if len(targets) >= max(1, int(max_fields)):
            break
    result["fields"] = [_def_label(d) for d in targets]
    if not targets:
        result["notes"].append("本地已能补齐（或无待补属性），不需要 AI 建议")
        return result

    # 商品原文（只用真实数据，避免把空内容喂给模型）
    src_bits = [f"标题: {str((product or {}).get('subject') or '').strip()}",
                f"关键词: {str((product or {}).get('keywords') or '').strip()}"]
    raw_attrs = [f"{a.get('attribute_name')}={a.get('value_name')}"
                 for a in attrs if a.get("attribute_name") and a.get("value_name")]
    if raw_attrs:
        src_bits.append("原文属性: " + "；".join(raw_attrs[:30]))
    desc = str((product or {}).get("textDesc") or "").strip()
    if desc:
        src_bits.append("详情摘录: " + re.sub(r"\s+", " ", desc)[:1200])
    field_lines = []
    for d in targets:
        name = _def_label(d)
        zh = str(d.get("nameZh") or "").strip()
        it = str(d.get("inputType") or "").strip() or "text"
        cands = [e["name"] for e in _value_entries(d) if e.get("name")]
        if cands:
            field_lines.append(f"- {name}" + (f"（中文：{zh}）" if zh else "")
                               + f" [候选值: {' | '.join(cands[:60])}]")
        else:
            field_lines.append(f"- {name}" + (f"（中文：{zh}）" if zh else "")
                               + f" [自由填写/{it}]")
    prompt = ATTRIBUTE_SUGGEST_TEMPLATE.format(
        source="\n".join(src_bits), fields="\n".join(field_lines))

    try:
        if client is None:
            from ..api import ApiClient
            from . import model_of
            model = str(model_of(cfg, "text") or "")
            client = ApiClient(_cfg_get(cfg, "api_base", ""),
                               _cfg_get(cfg, "api_key", ""),
                               _cfg_get(cfg, "timeout", 180))
        else:
            from . import model_of
            model = str(model_of(cfg, "text") or "")
        result["model"] = model
        text = client.chat(model, [
            {"role": "system", "content": ATTRIBUTE_SUGGEST_SYSTEM},
            {"role": "user", "content": prompt},
        ], temperature=0.1, max_tokens=1500)
        result["called"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = f"AI 调用失败：{type(e).__name__}: {str(e)[:160]}"
        return result

    data = _parse_ai_json(text)
    if not data:
        result["error"] = "AI 未返回可解析的 JSON"
        return result
    no_fab = {str(x).strip().lower() for x in NO_FABRICATION}
    for d in targets:
        name = _def_label(d)
        try:
            raw = data.get(name)
            if raw in (None, ""):
                raw = data.get(str(d.get("nameZh") or ""))
            if raw in (None, ""):
                result["unresolved"].append(f"{name}：AI 未给出可用值（原文无依据）")
                continue
            val = str(raw).strip()
            if val.startswith("{"):
                # 模型偶尔回 {"value": "x"}：取内部值
                try:
                    inner = json.loads(val)
                    if isinstance(inner, dict):
                        val = str(inner.get("value") or inner.get("value_name") or "").strip()
                except Exception:
                    pass
            if not val:
                result["unresolved"].append(f"{name}：AI 返回空值（原文无依据）")
                continue
            needs_confirm = name.strip().lower() in no_fab
            cands = _value_entries(d)
            if cands:
                res = resolve_attr_value(d, val)
                if not res.get("matched"):
                    result["unresolved"].append(
                        f"{name}：AI 值「{val}」不在平台候选值中（候选：{_candidate_sample(d)}）")
                    continue
                if res.get("value_id") in (None, ""):
                    result["unresolved"].append(
                        f"{name}：平台候选值「{res.get('value_name')}」缺少 value_id，不能采用")
                    continue
                result["suggestions"][name] = {
                    "value_id": str(res["value_id"]),
                    "value_name": str(res["value_name"]),
                    "needs_confirm": needs_confirm,
                    "source": "ai",
                }
            else:
                result["suggestions"][name] = {
                    "value_id": "-1", "value_name": val,
                    "needs_confirm": needs_confirm, "source": "ai",
                }
            if needs_confirm:
                result["notes"].append(
                    f"{name}：属于禁止自动编造的属性，AI 建议仅供参考，必须人工确认（未写平台）")
        except Exception as e:  # noqa: BLE001
            result["notes"].append(f"{name}：建议解析失败（{type(e).__name__}: {str(e)[:80]}）")
    return result


def build_attribute_prompt(attr_defs: list[dict]) -> str:
    names = [str(a.get("nameEn") or a.get("nameZh") or "") for a in attr_defs]
    return ATTRIBUTE_PROMPT.format(names=", ".join(n for n in names if n))


def map_attribute_suggestion(suggestion: dict, attr_defs: list[dict]) -> dict:
    """把 AI 给的属性值映射到平台枚举（value_id/name），无法匹配则丢弃。

    复用 alibaba_payload.resolve_attr_value：支持中文名/别名/大小写，
    匹配不到或平台候选缺 id 一律不返回（不写空 value_id）。
    """
    from .alibaba_payload import _def_label, resolve_attr_value
    out = {}
    for d in attr_defs:
        name = _def_label(d)
        if not name:
            continue
        val = (suggestion or {}).get(name)
        if val in (None, ""):
            continue
        try:
            res = resolve_attr_value(d, val)
            if res.get("matched") and res.get("value_id") not in (None, ""):
                out[name] = {"value_id": str(res["value_id"]),
                             "value_name": str(res["value_name"])}
        except Exception:
            continue
    return out

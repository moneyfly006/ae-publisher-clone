"""Check only settings used by the selected platform automation."""
import re

from .conflicts import Conflict


def check(cfg, platform, *, paid=True):
    from .collect_automation import settings
    issues = []
    prefix = platform + "_collect_"
    def block(title, detail, *keys):
        issues.append(Conflict("error", title, detail, "请在对应功能页修正后启动", tuple(keys)))
    from .collect_runtime import PARAMETERS, parameter_value
    for suffix, spec in PARAMETERS.items():
        try:
            parameter_value(suffix, getattr(cfg, prefix + suffix, spec[1]))
        except ValueError as exc:
            block("运行参数无效", str(exc), prefix + suffix)
    for suffix in ("ai", "images", "ai_sku", "ai_attributes", "auto_repair", "ai_cache"):
        if not isinstance(getattr(cfg, prefix + suffix, False), bool):
            block("开关值无效", "开关必须为 true 或 false，不能填写字符串或数字", prefix + suffix)
    import math
    for suffix in ("ai_max_calls", "ai_budget", "text_call_price", "image_call_price"):
        try:
            raw = getattr(cfg, prefix + suffix, 100 if suffix == "ai_max_calls" else 0)
            value = float(raw)
            if isinstance(raw, bool) or not math.isfinite(value) or value < 0 or (suffix == "ai_max_calls" and (value < 1 or value != int(value))):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            block("AI预算参数无效", "次数应为正整数，金额和预算单价应为非负有效数字", prefix + suffix)
    try:
        ov = settings(cfg, platform)
        if platform == "alibaba" and ov.get("price") and ov.get("price_tiers"):
            block("统一单价与阶梯报价同时填写", "两者只能使用一项，避免价格规则互相覆盖", prefix + "overrides")
        if platform == "amazon" and ov.get("fulfillment") and ov["fulfillment"] not in ("MFN", "AFN", "FBA"):
            block("配送方式无效", "请选择卖家自发货或亚马逊物流", prefix + "overrides")
    except (ValueError, TypeError) as exc:
        block("批量设置格式错误", str(exc), prefix + "overrides")
    for suffix, minimum, maximum in (("limit", 0, None), ("max_attempts", 1, 3)):
        try:
            value = getattr(cfg, prefix + suffix, 10 if suffix == "limit" else 2)
            number = int(value)
            if str(value) != str(number) or number < minimum or (maximum is not None and number > maximum):
                raise ValueError()
        except (ValueError, TypeError):
            block("数量或重试次数无效", "处理数量必须为非负整数，重试次数为1至3", prefix + suffix)
    if str(getattr(cfg, prefix + "op", "1")) not in ("1", "2", "3"):
        block("保存去向无效", "请选择保存草稿、待发布或提交发布", prefix + "op")
    ai = bool(getattr(cfg, prefix + "ai", False))
    images = bool(getattr(cfg, prefix + "images", False))
    extra_ai = bool(getattr(cfg, prefix + "ai_sku", False)) or bool(getattr(cfg, prefix + "ai_attributes", False))
    def amount(suffix):
        try:
            return float(getattr(cfg, prefix + suffix, 0) or 0)
        except (ValueError, TypeError):
            return 0
    if paid and amount("ai_budget") > 0:
        if (ai or extra_ai) and amount("text_call_price") <= 0:
            block("缺少文本预算单价", "设置金额上限时，请填写文本单次调用的预算价", prefix + "text_call_price")
        if images and amount("image_call_price") <= 0:
            block("缺少图片预算单价", "设置金额上限时，请填写图片单次调用的预算价", prefix + "image_call_price")
    if ai:
        from .platform_presets import ai_fields
        import json
        try:
            selected = json.loads(getattr(cfg, prefix + "ai_fields", "[]"))
            if not isinstance(selected, list) or not selected or any(k not in ai_fields(platform) for k in selected):
                raise ValueError()
        except (ValueError, TypeError):
            block("AI处理字段未选择或无效", "请至少选择一项文案字段", prefix + "ai_fields")
        if getattr(cfg, prefix + "ai_mode", "missing") not in ("missing", "rewrite"):
            block("AI处理方式无效", "请选择补齐缺失或重写所选字段", prefix + "ai_mode")
    if paid and (ai or images or extra_ai) and not str(getattr(cfg, "api_key", "") or "").strip():
        block("已启用 AI 但未配置 API Key", "请在设置中配置可用的模型服务", "api_key", prefix + "ai", prefix + "images")
    if images and getattr(cfg, prefix + "image_rules", "") not in ("", None):
        from .platform_media import batch_rules
        try:
            batch_rules(cfg, platform)
        except (TypeError, ValueError) as exc:
            block("图片规则无效", str(exc), prefix + "image_rules")
    elif images:
        raw = str(getattr(cfg, prefix + "image_indices", "") or "").strip()
        maximum = 6 if platform == "alibaba" else 9
        valid = bool(raw)
        for part in raw.split(","):
            if not re.fullmatch(r"[1-9](?:-[1-9])?", part.strip()):
                valid = False
                continue
            numbers = [int(x) for x in part.strip().split("-")]
            if max(numbers) > maximum or numbers[0] > numbers[-1]:
                valid = False
        if not valid:
            block("图片序号范围无效", f"请使用1至{maximum}的序号，例如1或1-3,5", prefix + "image_indices")
    return issues

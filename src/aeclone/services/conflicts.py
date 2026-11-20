"""启动前冲突检查：把所有互斥/依赖/无效的开关组合找出来，引导用户修改。

返回 [(级别, 标题, 说明, 修复建议)]；级别 ERROR 阻断启动，WARN 只提示。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Conflict:
    level: str          # error | warn
    title: str
    detail: str
    fix: str
    keys: tuple = ()


def _b(cfg, k, default=False) -> bool:
    try:
        return bool(getattr(cfg, k, default))
    except Exception:
        return bool(default)


def _s(cfg, k, default="") -> str:
    try:
        v = getattr(cfg, k, default)
        return str(v if v is not None else default).strip()
    except Exception:
        return str(default)


def check(cfg) -> list[Conflict]:
    out: list[Conflict] = []
    add = lambda *a, **kw: out.append(Conflict(*a, **kw))

    # ---------- AI 提示词为空 ----------
    for en, pr, name in (
        ("enable_ai_title", "title_prompt", "AI 标题"),
        ("enable_ai_detail", "detail_prompt", "AI 详情"),
        ("enable_ai_sku", "sku_prompt", "AI SKU"),
        ("enable_ai_weight", "weight_prompt", "AI 估重"),
    ):
        if _b(cfg, en) and not _s(cfg, pr):
            add("warn", f"{name}已开启但提示词为空",
                f"{pr} 是空的，调用会失败或产出质量很差",
                f"在「AI 文案」页填好 {name}的提示词，或关掉该开关", (en, pr))

    # ---------- 白底/去水印：开了但没范围/没图 ----------
    if _b(cfg, "white_bg_enable"):
        r1, r2 = _s(cfg, "white_background_pic_1", "1"), _s(cfg, "white_background_pic_2", "3")
        if not r1 or not r2:
            add("warn", "主图白底已开启但序号范围为空",
                "white_background_pic_1/2 未填", "填好序号范围，或关掉「主图白底」",
                ("white_bg_enable",))
    if _b(cfg, "watermark_enable") and not _s(cfg, "watermark"):
        add("warn", "主图去水印已开启但没有水印文件",
            "watermark 为空（去水印/去 Logo 需要参考图）",
            "在「AI 图片 → 水印设置」选一张水印图，或关掉「主图去水印」",
            ("watermark_enable", "watermark"))

    # ---------- 详情：添加 vs 删除 语义冲突 ----------
    if _b(cfg, "custom_detail_text_enable") and _b(cfg, "delete_detail_text"):
        add("warn", "「添加自定义详情」与「删除详情原有文本」同时开启",
            "两者语义相反：一个追加内容、一个先清空原详情，结果取决于执行顺序",
            "想保留原详情就关掉「删除详情原有文本」；想只留自定义内容就关掉「添加自定义详情」",
            ("custom_detail_text_enable", "delete_detail_text"))

    # ---------- 详情：开了但没有内容 ----------
    if _b(cfg, "custom_detail_text_enable"):
        has_text = bool(_s(cfg, "detail_text_info") or _s(cfg, "detail_text_info_tail"))
        try:
            import json as _j
            imgs = _j.loads(_s(cfg, "detail_local_images", "[]") or "[]")
        except Exception:
            imgs = []
        if not has_text and not imgs:
            add("warn", "「添加自定义详情」已开启但没有任何内容",
                "开头/结尾文字都为空，也没选本地详情图",
                "在「AI 文案 → 详情内容增强」填文字或选图片，或关掉该开关",
                ("custom_detail_text_enable",))

    # ---------- SKU 前缀 ----------
    # 开了「自动生成编码」时，前缀为空也会生成 SKU-001，不算冲突
    if (_b(cfg, "custom_sku_prefix_enable") and not _s(cfg, "custom_sku_prefix")
            and not _b(cfg, "auto_generate_sku_code")):
        add("warn", "「自定义 SKU 前缀」已开启但前缀为空",
            "custom_sku_prefix 为空，生成规则会退化成直接拼分隔符",
            "填一个前缀（如 MY-），或关掉该开关",
            ("custom_sku_prefix_enable", "custom_sku_prefix"))
    if _b(cfg, "only_sku_prefix_checkbox") and not _b(cfg, "custom_sku_prefix_enable"):
        add("warn", "「只用前缀」开启但「自定义前缀」关闭",
            "没有前缀可用", "同时打开「自定义 SKU 前缀」并填好前缀",
            ("only_sku_prefix_checkbox", "custom_sku_prefix_enable"))

    # ---------- 视频来源互斥 ----------
    if _b(cfg, "use_template_video") and _b(cfg, "generate_main_video"):
        add("warn", "模板视频与生成视频同时开启",
            "两者都要产出主图视频，只有一个会生效",
            "只保留一种：用模板就关掉「生成主图视频」，反之亦然",
            ("use_template_video", "generate_main_video"))
    # 模板视频现在会自动取「模板商品」的视频链接，路径只是可选覆盖，不再强制

    # ---------- 审查：拦截开了但没审查项 ----------
    if _b(cfg, "review_block_on_fail") and not (
            _b(cfg, "review_text_enable") or _b(cfg, "review_main_img_enable")
            or _b(cfg, "review_detail_img_enable")):
        add("warn", "「不合规时拦截发布」已开启，但没有任何审查项",
            "没有文本/主图/详情图审查在跑，拦截永远不会触发",
            "至少开一个审查项，或关掉「不合规时拦截发布」",
            ("review_block_on_fail",))

    # 说明：店小秘的相册上传 / 自动清理接口已停用，程序会自动跳过，
    # 图片统一走图床，不再对用户提示（避免每次启动一条无意义 WARN）。

    # ---------- 价格 ----------
    if _b(cfg, "write_price_to_product"):
        try:
            _m = float(_s(cfg, "price_multiplier", "1") or 1)
        except Exception:
            _m = 1.0
        if _m <= 0:
            add("error", "价格倍率必须大于 0",
                f"当前 price_multiplier = {_m}", "把倍率改成大于 0 的数", ("price_multiplier",))
        try:
            _c = float(_s(cfg, "platform_commission", "0") or 0)
        except Exception:
            _c = 0.0
        if _c >= 100:
            add("error", "平台佣金不能 ≥ 100%",
                f"当前 platform_commission = {_c}", "改成 0~99 之间", ("platform_commission",))

    # ---------- 欧盟资质 ----------
    if _s(cfg, "eu_qualification", "否") == "是":
        need = [k for k in ("eu_name", "eu_address", "eu_email", "eu_phone")
                if not _s(cfg, k)]
        if need:
            add("error", "已选「提交欧盟资质」但责任人资料不全",
                "缺失：" + "、".join(need),
                "在「高级设置 → 欧盟资质」补全，或把资质改成「否」",
                ("eu_qualification", *need))

    return out


def blocking(items: list[Conflict]) -> list[Conflict]:
    return [c for c in items if c.level == "error"]

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
    if _b(cfg, "overlay_watermark_enable") and not _s(cfg, "watermark"):
        add("error", "水印叠加已开启但未设置水印",
            "叠加水印需要图片文件或文字；去除原图水印不需要参考水印文件",
            "在「AI 图片 → 图片输出与水印」设置水印图片或文字", ("overlay_watermark_enable", "watermark"))

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

    # ---------- 标题前后缀：没有 AI 标题时不会生效 ----------
    if (_s(cfg, "title_prefix") or _s(cfg, "title_suffix")) and not _b(cfg, "enable_ai_title"):
        add("warn", "标题前后缀已填写，但 AI 标题未开启",
            "当前标题前后缀只在 AI 标题生成时拼接，关闭 AI 标题后不会生效",
            "开启「AI 标题」，或把标题前后缀留空",
            ("title_prefix", "title_suffix", "enable_ai_title"))

    # ---------- 详情本地图/文字：总开关或写回开关关闭时不会生效 ----------
    _has_detail_extra = bool(_s(cfg, "detail_text_info") or _s(cfg, "detail_text_info_tail"))
    try:
        import json as _jd
        _detail_imgs = _jd.loads(_s(cfg, "detail_local_images", "[]") or "[]")
    except Exception:
        _detail_imgs = []
    if (_has_detail_extra or _detail_imgs) and not _b(cfg, "custom_detail_text_enable"):
        add("warn", "详情文字/本地图片已填写，但详情内容增强总开关关闭",
            "总开关关闭时这些内容不会插入商品详情",
            "开启「详情内容增强」，或清空详情文字/本地图片",
            ("custom_detail_text_enable", "detail_text_info", "detail_local_images"))
    if (_has_detail_extra or _detail_imgs or _b(cfg, "custom_detail_text_enable")) \
            and not _b(cfg, "write_detail_html_to_product"):
        add("warn", "详情内容增强已配置，但详情写回商品关闭",
            "write_detail_html_to_product 关闭时，详情文字和本地图不会写入商品",
            "开启「详情写回商品」，或关闭详情内容增强",
            ("write_detail_html_to_product", "custom_detail_text_enable"))
    if _b(cfg, "delete_detail_text") and not _b(cfg, "enable_ai_detail") \
            and not _b(cfg, "custom_detail_text_enable"):
        add("warn", "删除原详情文字已开启，但没有 AI 详情或自定义详情替代",
            "这会删除原采集描述且不生成替换正文，商品详情可能为空",
            "关闭「删除详情原有文本」，或开启 AI 详情/自定义详情",
            ("delete_detail_text", "enable_ai_detail", "custom_detail_text_enable"))

    # ---------- SKU 前缀：不写回商品时不会生效 ----------
    if (_s(cfg, "custom_sku_prefix") or _b(cfg, "only_sku_prefix_checkbox") or _b(cfg, "auto_generate_sku_code")) \
            and not _b(cfg, "write_sku_codes_to_product"):
        add("warn", "SKU 编码规则已配置，但 SKU 编码写回商品关闭",
            "前缀/自动编码/只用前缀都不会写回商品，实际保留原 SKU 编码",
            "开启「SKU 编码写回商品」，或清空 SKU 编码规则",
            ("write_sku_codes_to_product", "custom_sku_prefix", "only_sku_prefix_checkbox"))

    if (_b(cfg, "only_sku_prefix_checkbox") and _b(cfg, "custom_sku_prefix_enable")
            and not _s(cfg, "custom_sku_prefix") and _b(cfg, "auto_generate_sku_code")):
        add("warn", "已开「只用前缀」但前缀为空",
            "当前会退化为生成 SKU-001，而不是只保留前缀",
            "填写 SKU 前缀，或关闭「只用前缀」",
            ("only_sku_prefix_checkbox", "custom_sku_prefix"))

    # ---------- 视频来源互斥 ----------
    if (_s(cfg, "video_source") not in ("collected", "template", "generated", "none")
            and _b(cfg, "use_template_video") and _b(cfg, "generate_main_video")):
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
    mode = _s(cfg, "price_mode", "multiplier")
    if _b(cfg, "write_price_to_product") and mode in ("multiplier", "commission"):
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
        if mode == "commission" and _c >= 100:
            add("error", "平台佣金不能 ≥ 100%",
                f"当前 platform_commission = {_c}", "改成 0~99 之间", ("platform_commission",))

    # ---------- 欧盟资质 ----------
    if _s(cfg, "eu_qualification", "否") == "是":
        need = [k for k in ("eu_responsible_name", "eu_responsible_address", "eu_responsible_email", "eu_responsible_phone")
                if not _s(cfg, k)]
        if need:
            add("error", "已选「提交欧盟资质」但责任人资料不全",
                "缺失：" + "、".join(need),
                "在「类目与属性 → 欧盟资质」补全，或把资质改成「否」",
                ("eu_qualification", *need))

    return out


def blocking(items: list[Conflict]) -> list[Conflict]:
    return [c for c in items if c.level == "error"]

"""启动前自检：把配置翻译成「人话」，并挑出会静默失效的组合。

设计目标：用户看不懂一堆开关时，能一眼看到
  - 这次到底会做什么（按顺序）
  - 哪些设置会导致某一步「什么都不做」（静默失效）

静默失效是最坑的一类问题：
  - 开了「精细化编辑」但三组都没启用 → 一条都不执行
  - 选了「通用编辑」但提示词为空 → 直接跳过
  - 开了「白底」但范围写错（0 或超出图片数）→ 一张都不处理
  - 开了 AI 但没填 Key → 每一步都失败
这些都不报错，只是"什么都没发生"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class Issue:
    level: str      # error | warn | info
    text: str

    def line(self) -> str:
        icon = {"error": "✗", "warn": "!", "info": "·"}[self.level]
        return f"{icon} {self.text}"


def _get(cfg, key, default=None):
    return getattr(cfg, key, default)


def _truthy(cfg, key, default=False) -> bool:
    try:
        return bool(_get(cfg, key, default))
    except Exception:
        return default


def _text(cfg, key, default="") -> str:
    v = _get(cfg, key, default)
    return str(v or "").strip()


def _as_list(raw) -> list:
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(str(raw or "[]"))
        return v if isinstance(v, list) else []
    except Exception:
        return []


def image_plan_lines(cfg) -> list[str]:
    """把图片配置翻译成几条人话。"""
    out: list[str] = []
    if not _truthy(cfg, "ai_image_enable", True):
        return ["AI 图片处理：已关闭（一张图都不会处理）"]

    mode = _text(cfg, "image_edit_mode", "fine").lower()
    general_mode = mode in ("general", "simple")

    # 规则表（迁移来的）先读，用来判断旧开关是否已被它覆盖
    rules = _as_list(_get(cfg, "image_simple_rules", "[]"))
    _rule_groups = {str(r.get("group")) for r in rules if isinstance(r, dict)}

    # 简单编辑（白底/去水印/翻译）
    simple = []
    if _truthy(cfg, "white_bg_enable"):
        a, b = _text(cfg, "white_background_pic_1", ""), _text(cfg, "white_background_pic_2", "")
        simple.append(f"主图白底 {a or '?'}-{b or '?'}")
    if _truthy(cfg, "watermark_enable"):
        a = _text(cfg, "remove_watermark_pic_1", "")
        b = _text(cfg, "remove_watermark_pic_2", "")
        simple.append(f"主图去水印 {a or '?'}-{b or '?'}")
    if _truthy(cfg, "img_detail_white_bg"):
        simple.append("详情图白底")
    if _truthy(cfg, "img_detail_remove_wm"):
        simple.append("详情图去水印")
    if _truthy(cfg, "white_background_sku_pic"):
        simple.append("SKU 图白底")
    if _truthy(cfg, "translate_main_pic_enable"):
        simple.append("主图翻译")
    if simple and "main" not in _rule_groups:
        out.append("图片简单编辑：" + "；".join(simple))
    elif simple:
        out.append("图片简单编辑：旧开关已并入下面的「图片方案」，不会重复处理")

    if rules:
        parts = []
        for r in rules[:6]:
            if not isinstance(r, dict):
                continue
            g = {"main": "主图", "detail": "详情图", "sku": "SKU图"}.get(str(r.get("group")), str(r.get("group")))
            ops = {"white": "白底", "watermark": "去水印", "translate": "翻译"}
            ops_txt = "+".join(ops.get(str(o), str(o)) for o in (r.get("ops") or [])) or "无动作"
            parts.append(f"{g} {r.get('range') or '?'} {ops_txt}")
        if parts:
            out.append("图片方案（按范围）：" + "；".join(parts))

    # 通用编辑
    if general_mode:
        prompt = _text(cfg, "img_general_prompt")
        a, b = _text(cfg, "img_general_main_from", "1"), _text(cfg, "img_general_main_to", "8")
        if prompt:
            out.append(f"通用编辑：主图 {a}-{b} 每张都用同一条提示词（{len(prompt)} 字）")
        else:
            out.append("通用编辑：已选中，但提示词为空 → 不会处理任何图")
        return out

    # 精细化编辑
    jobs = [j for j in _as_list(_get(cfg, "image_ai_jobs", "[]")) if isinstance(j, dict)]
    gates = {
        "main": _truthy(cfg, "img_fine_main_enable", True),
        "detail": _truthy(cfg, "img_fine_detail_enable", False),
        "sku": _truthy(cfg, "img_fine_sku_enable", False),
    }
    label = {"main": "主图", "detail": "详情图", "sku": "SKU图"}
    for g in ("main", "detail", "sku"):
        gj = [j for j in jobs if str(j.get("group") or "main") == g]
        if not gj:
            continue
        if not gates[g]:
            out.append(f"精细化编辑·{label[g]}：有 {len(gj)} 条任务，但该组未启用 → 全部跳过")
            continue
        spots = []
        for j in gj[:5]:
            spots.append(f"第{j.get('index') or '?'}张")
        out.append(f"精细化编辑·{label[g]}：{len(gj)} 条（{'、'.join(spots)}）")
        if _truthy(cfg, "img_fine_detail_replace") and g == "detail":
            out.append("详情图：删除原有详情图，仅保留 AI 结果")
    if not any(j for j in jobs):
        if not general_mode:
            out.append("精细化编辑：没有任何任务 → 不会处理任何图")

    if not _truthy(cfg, "img_min_size_enable", True):
        out.append("图片尺寸校验：已关闭（<800px 的图也会写回商品）")
    else:
        out.append(f"图片尺寸校验：AI 产出 <{_text(cfg, 'img_min_size', '800')}px 不采用"
                   + ("；原图不合规的也会被删" if _truthy(cfg, "img_min_size_drop_invalid") else ""))
    return out


def text_plan_lines(cfg) -> list[str]:
    out = []
    if _truthy(cfg, "enable_ai_title"):
        out.append("AI 标题：开")
    if _truthy(cfg, "enable_ai_detail"):
        out.append("AI 详情：开")
    if _truthy(cfg, "enable_ai_sku"):
        out.append("AI SKU：开")
    if _truthy(cfg, "review_enable"):
        out.append("内容审查：开")
    if _truthy(cfg, "estimate_weight_enable"):
        out.append("AI 估重：开")
    return out or ["AI 文本：全部关闭（标题/详情/SKU 都不改）"]


def check(cfg) -> list[Issue]:
    """挑出会静默失效或一定失败的配置。"""
    issues: list[Issue] = []

    # 配置上次是从哪加载的（损坏/回退时提醒用户，别再以为"设置自己变了"）
    src = getattr(cfg, "_loaded_from", "file")
    if src == "defaults":
        issues.append(Issue("error", "配置文件损坏且备份也不可用，已恢复默认值——"
                                     "请检查 API Key / 店铺 / 模板（原文件已存为 config.corrupt-*.json）"))
    elif src == "bak":
        issues.append(Issue("warn", "配置文件损坏，已从 config.bak 恢复——"
                                    "请确认 API Key / 店铺 / 模板是否还在"))
    ai_on = any(_truthy(cfg, k) for k in (
        "enable_ai_title", "enable_ai_detail", "enable_ai_sku", "review_enable",
        "estimate_weight_enable", "ai_image_enable", "translate_main_pic_enable",
        "translate_detail_pic_enable", "translate_sku_pic"))

    if ai_on and not _text(cfg, "api_key"):
        issues.append(Issue("error", "开了 AI 功能但没填 API Key —— 每一步都会失败"))

    # 图片：静默失效
    if _truthy(cfg, "ai_image_enable", True):
        mode = _text(cfg, "image_edit_mode", "fine").lower()
        if mode in ("general", "simple"):
            if not _text(cfg, "img_general_prompt"):
                issues.append(Issue("error", "选了「通用编辑」但编辑提示词是空的 —— 不会处理任何图"))
            try:
                a = int(_text(cfg, "img_general_main_from", "1") or 1)
                b = int(_text(cfg, "img_general_main_to", "8") or 8)
                if b < a:
                    issues.append(Issue("error", f"通用编辑范围写反了：从第 {a} 张到第 {b} 张"))
            except Exception:
                issues.append(Issue("warn", "通用编辑的主图范围不是数字"))
        else:
            jobs = [j for j in _as_list(_get(cfg, "image_ai_jobs", "[]")) if isinstance(j, dict)]
            gates = (_truthy(cfg, "img_fine_main_enable", True),
                     _truthy(cfg, "img_fine_detail_enable", False),
                     _truthy(cfg, "img_fine_sku_enable", False))
            if jobs and not any(gates):
                issues.append(Issue("error",
                                    f"精细化编辑有 {len(jobs)} 条任务，但主图/详情图/SKU 三组都没启用 —— 全部跳过"))
            empty = [j for j in jobs if not str(j.get("prompt") or "").strip()]
            if empty:
                issues.append(Issue("warn", f"{len(empty)} 条精细化任务提示词为空，会被忽略"))

        # 白底范围合理性
        if _truthy(cfg, "white_bg_enable"):
            try:
                a = int(_text(cfg, "white_background_pic_1", "0") or 0)
                b = int(_text(cfg, "white_background_pic_2", "0") or 0)
                if a <= 0 or b < a:
                    issues.append(Issue("warn", f"主图白底范围不合法：{a}-{b}（应为 1 开始且后者≥前者）"))
            except Exception:
                issues.append(Issue("warn", "主图白底范围不是数字"))

    # 属性去重是"删数据"的操作，开了要明确提醒
    if _truthy(cfg, "auto_clean_dup_attributes", False):
        issues.append(Issue("warn",
                            "已开启「保存时清理与系统属性重名的自定义属性」——"
                            "保存时会删掉这类属性行（默认关）"))

    # 多 SKU 硬约束
    try:
        sku_limit = int(_get(cfg, "max_sku_count", 256) or 256)
        if sku_limit > 1 and _truthy(cfg, "write_sku_codes_to_product", True):
            issues.append(Issue("info",
                                "多 SKU 商品会因「SKU 编码写回」被跳过；"
                                "在「变种设置 → SKU 写回」关掉即可继续"))
    except Exception:
        pass

    # 模板
    if not any((_text(cfg, "product_template_id"), _text(cfg, "product_template_name"),
                _text(cfg, "template_product_id"), _text(cfg, "template_product_title"))):
        snap = _text(cfg, "template_snapshot")
        if snap in ("", "{}"):
            issues.append(Issue("warn", "没选产品模板 —— 必填属性可能不完整，保存会被站点拒绝"))

    if not _truthy(cfg, "auto_save_products", True):
        issues.append(Issue("info", "未开启「自动保存」——只预演，不会写入店小秘"))
    else:
        issues.append(Issue("info", "真实写入模式：会处理图片/文本并保存到店小秘"
                                    "（建议先只跑 1 个商品确认效果）"))
    return issues


def summary(cfg) -> str:
    """一段可直接塞进日志/界面的自检文本。"""
    lines: list[str] = []
    lines.append("【这次会做什么】")
    lines.extend("· " + x for x in image_plan_lines(cfg))
    lines.extend("· " + x for x in text_plan_lines(cfg))
    issues = check(cfg)
    if issues:
        lines.append("【需要注意】")
        lines.extend(i.line() for i in issues)
    return "\n".join(lines)


def blocking(cfg) -> list[str]:
    """只返回会让人「点了开始却什么都没发生」的问题。"""
    return [i.text for i in check(cfg) if i.level == "error"]

"""图片任务计划：按配置生成可执行步骤，并给出阻断原因（对齐老软件 image_task_plan）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .image_index import parse_indices


@dataclass
class ImagePlan:
    steps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        return bool(self.steps) and not self.blockers


def _fmt_positions(expr: str, total: int) -> str:
    idx = parse_indices(expr, total)
    if not idx:
        return "无"
    groups, start = [], idx[0]
    prev = idx[0]
    for i in idx[1:]:
        if i == prev + 1:
            prev = i
            continue
        groups.append(f"{start}-{prev}" if prev > start else f"{start}")
        start = prev = i
    groups.append(f"{start}-{prev}" if prev > start else f"{start}")
    return ",".join(groups)


def build_plan(cfg, product: dict | None = None) -> ImagePlan:
    p = product or {}
    n_main = max(len(p.get("images") or []), 1)
    n_detail = max(len(p.get("detail_images") or []), 1)
    plan = ImagePlan()
    if not getattr(cfg, "ai_image_enable", True):
        return plan

    if getattr(cfg, "main_image_generate_enable", False):
        prompt = (getattr(cfg, "main_image_generate_prompt", "") or "").strip()
        if not prompt:
            # 误开（提示词空）→ 当作未启用：不执行、不提醒、不阻断
            try:
                cfg.set("main_image_generate_enable", False)
            except Exception:
                pass
        else:
            refs = "参考现有主图" if getattr(cfg, "main_image_generate_use_refs", True) else "纯文生图"
            plan.steps.append(f"生成主图 ×{getattr(cfg, 'main_image_generate_count', '1')}（{refs}）")

    # 简单编辑（白底/去水印）：以 image_simple_rules 为准（实际执行也读它）。
    # 规则表为空时才退回旧开关，保证老配置仍能显示计划。
    _rules = []
    try:
        from .image_jobs import rules_from_switches
        _rules = rules_from_switches(cfg)
    except Exception:
        _rules = []
    if _rules:
        _op_names = {"white": "白底", "watermark": "去水印", "edit": "编辑",
                     "translate": "翻译", "upscale": "放大"}
        _grp_names = {"main": "主图", "detail": "描述图", "sku": "SKU 图"}
        for r in _rules:
            grp = str(r.get("group") or "main")
            ops = "/".join(_op_names.get(o, o) for o in (r.get("ops") or []))
            rng = str(r.get("range") or "")
            total = {"main": n_main, "detail": n_detail, "sku": 1}.get(grp, n_main)
            pos = _fmt_positions(rng, total) if rng else f"1-{total}"
            if pos == "无":
                plan.warnings.append(f"{_grp_names.get(grp, grp)}{ops}：序号范围为空，已跳过")
            plan.steps.append(f"{_grp_names.get(grp, grp)} {ops} {pos}")
    else:
        if cfg.white_bg_enable:
            pos = _fmt_positions(f"{cfg.white_background_pic_1}-{cfg.white_background_pic_2}", n_main)
            if pos == "无":
                plan.warnings.append("主图白底：序号范围为空，已跳过")
            plan.steps.append(f"主图白底 {pos}")

        if cfg.watermark_enable:
            pos = _fmt_positions(f"{getattr(cfg, 'remove_watermark_pic_1', '1')}-{getattr(cfg, 'remove_watermark_pic_2', '5')}", n_main)
            if pos == "无":
                plan.warnings.append("主图去水印：序号范围为空，已跳过")
            plan.steps.append(f"主图去水印 {pos}")

    if getattr(cfg, "translate_main_pic_enable", False):
        plan.steps.append("主图文字翻译 " + _fmt_positions(getattr(cfg, "translate_main_pic_indices", "") or "1", n_main))

    if getattr(cfg, "designated_edit_enable", False):
        idx = getattr(cfg, "designated_edit_indices", "") or ""
        prompt = (getattr(cfg, "designated_edit_prompt", "") or "").strip()
        if not idx:
            plan.warnings.append("指定图片编辑：未填序号，已跳过")
        if not prompt:
            plan.warnings.append("指定图片编辑：提示词为空，已跳过")
        if idx and prompt:
            plan.steps.append(f"指定编辑 {_fmt_positions(idx, n_main)}")

    detail_ops = [k for k in ("img_detail_white_bg", "img_detail_remove_wm", "img_detail_edit") if getattr(cfg, k, False)]
    if detail_ops:
        expr = getattr(cfg, "white_background_detail_pic_indices", "") or ""
        pos = _fmt_positions(expr, n_detail) if expr else f"1-{n_detail}"
        if pos == "无":
            plan.warnings.append("描述图处理：序号为空，已跳过")
        names = {"img_detail_white_bg": "白底", "img_detail_remove_wm": "去水印", "img_detail_edit": "精细编辑"}
        plan.steps.append("描述图 " + "/".join(names[k] for k in detail_ops) + f" {pos}")

    if getattr(cfg, "translate_detail_pic_enable", False):
        plan.steps.append("描述图翻译 " + _fmt_positions(getattr(cfg, "translate_detail_pic_indices", "") or "", n_detail))

    sku_ops = [k for k in ("img_sku_white_bg", "img_sku_remove_wm", "img_sku_edit") if getattr(cfg, k, False)]
    if cfg.white_background_sku_pic and sku_ops:
        plan.steps.append("SKU 图 " + "/".join({"img_sku_white_bg": "白底", "img_sku_remove_wm": "去水印", "img_sku_edit": "编辑"}[k] for k in sku_ops))

    if getattr(cfg, "translate_sku_pic", False):
        plan.steps.append("SKU 图翻译")

    if (getattr(cfg, "background_gen_rules", "") or "").strip() not in ("", "[]"):
        plan.steps.append("背景生成")

    if cfg.four_in_one:
        pos = "放主图第 1 张" if str(getattr(cfg, "four_in_one_position", "append")) == "first" else "追加到主图末尾"
        plan.steps.append("四合一（" + pos + "）")

    from .image_edit_plans import parse as _parse_plans, describe as _describe
    _plans = _parse_plans(getattr(cfg, "image_edit_plans", ""))
    if _plans:
        for _line in _describe(_plans)[:6]:
            plan.steps.append("逐图方案：" + _line)

    # 精细化编辑（image_ai_jobs）：逐条主图/详情图/SKU 编辑
    try:
        import json as _j
        _jobs = _j.loads(getattr(cfg, "image_ai_jobs", "[]") or "[]")
        _acts = [x for x in (_jobs or []) if isinstance(x, dict) and (x.get("prompt") or "").strip()]
        if _acts:
            for _j1 in _acts[:6]:
                _g = {"main": "主图", "detail": "详情图", "sku": "SKU图"}.get(str(_j1.get("group")), "图片")
                _pos = _j1.get("index") or _j1.get("position") or "-"
                plan.steps.append(f"精细化编辑·{_g} 第{_pos}")
    except Exception:
        pass

    if getattr(cfg, "overlay_watermark_enable", False):
        if not (getattr(cfg, "watermark", "") or "").strip():
            plan.warnings.append("水印叠加：未配置水印文件/文字，已跳过")
        plan.steps.append("水印叠加")

    return plan


# ---------------- 开关冲突检测 ----------------
# 多个图片开关同时打开时，可能对同一张图做冲突操作；这里集中检测并给出优先顺序。
def detect_conflicts(cfg) -> list[dict]:
    """返回 [{level, title, detail, fix}] 的冲突/风险清单。

    level: high=会导致结果不对或互相覆盖；mid=顺序依赖；low=提示
    """
    out: list[dict] = []
    jobs = []
    try:
        import json as _j
        jobs = _j.loads(getattr(cfg, "image_ai_jobs", "[]") or "[]")
    except Exception:
        jobs = []
    # 只有对应分组总开关勾选时，任务才算「启用」；没勾选 = 不算冲突
    _enabled_group = {
        "main": bool(getattr(cfg, "img_fine_main_enable", False)),
        "detail": bool(getattr(cfg, "img_fine_detail_enable", False)),
        "sku": bool(getattr(cfg, "img_fine_sku_enable", False)),
    }
    _active_jobs = [x for x in jobs
                    if isinstance(x, dict) and _enabled_group.get(str(x.get("group")), False)]
    main_jobs = [x for x in _active_jobs if str(x.get("group")) == "main"]
    gen_prompt = (getattr(cfg, "main_image_generate_prompt", "") or "").strip()
    # 开了但提示词为空 = 误开（会被自动关闭），不算启用
    gen = bool(getattr(cfg, "main_image_generate_enable", False)) and bool(gen_prompt)

    # 1) 生成主图 vs 精细化主图编辑（都改主图第 1 张）
    if gen and main_jobs:
        idx = {str(x.get("index")) for x in main_jobs}
        if "1" in idx or not idx:
            out.append({
                "level": "high",
                "title": "生成主图 + 主图精细化编辑 同时开启",
                "detail": "两者都会改主图第 1 张；执行顺序不确定，可能互相覆盖。",
                "fix": "二选一：要 AI 生成就关掉主图精细化编辑；要按参考图编辑就关掉「AI 生成主图」。",
            })


    # 3) 去水印 + 叠加水印（互相矛盾）
    if bool(getattr(cfg, "watermark_enable", False)) and bool(getattr(cfg, "overlay_watermark_enable", False)):
        out.append({
            "level": "high",
            "title": "去水印 + 叠加水印 同时开启",
            "detail": "先去掉原水印、又叠上你的水印，容易产生双重/残留。",
            "fix": "一般只需要「叠加水印」；确实要先去原水印再叠加，请确认这是你要的效果。",
        })

    # 4) 主图白底 vs 生成主图（生成图已经是白底模板）
    if gen and bool(getattr(cfg, "white_bg_enable", False)):
        out.append({
            "level": "low",
            "title": "生成主图 + 主图白底 同时开启",
            "detail": "生成的主图通常已是白底，再跑一遍白底会多花一次钱。",
            "fix": "可不勾「主图白底」，或把白底范围排除第 1 张。",
        })

    # 5) 通用编辑(img_main_edit) vs 指定编辑
    if bool(getattr(cfg, "img_main_edit", False)) and bool(getattr(cfg, "designated_edit_enable", False)):
        out.append({
            "level": "mid",
            "title": "主图通用编辑 + 指定编辑 同时开启",
            "detail": "同一张图可能被两条提示词先后处理。",
            "fix": "只保留一种编辑方式即可。",
        })

    # 6) 四合一 + 生成主图（都在动主图第 1 张位置）
    if bool(getattr(cfg, "four_in_one", False)) and gen:
        out.append({
            "level": "low",
            "title": "四合一 + 生成主图",
            "detail": "四合一结果会插入主图，可能与生成图的位置冲突。",
            "fix": "确认四合一插入位置（第1张/末尾）符合预期。",
        })

    # 6.5) SKU 图：简单编辑（白底/去水印/编辑）与精细化 SKU 编辑会处理同一批图
    _sku_simple = [k for k in ("img_sku_white_bg", "img_sku_remove_wm", "img_sku_edit")
                   if getattr(cfg, k, False)]
    if _enabled_group.get("sku") and _sku_simple:
        out.append({
            "level": "mid",
            "title": "SKU 图被两处同时处理",
            "detail": ("简单编辑里的 " + "、".join(_sku_simple) +
                       " 与「精细化编辑 → SKU」都会处理同一批 SKU 图，可能互相覆盖。"),
            "fix": "二选一：要么关掉精细化编辑的 SKU 分组，要么关掉简单编辑里的 SKU 开关。",
        })

    # 7) 精细化编辑里有提示词为空的条目
    empty_jobs = [x for x in _active_jobs if not (x.get("prompt") or "").strip()]
    if empty_jobs:
        out.append({
            "level": "low",
            "title": f"{len(empty_jobs)} 条精细化编辑没有提示词",
            "detail": "这些条目会被跳过。",
            "fix": "补上提示词，或删掉这些条目。",
        })

    return out

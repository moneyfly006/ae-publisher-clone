"""图片任务计划：按配置生成可执行步骤，并给出阻断原因（对齐老软件 image_task_plan）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from .image_index import parse_indices


@dataclass
class ImagePlan:
    steps: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

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

    if getattr(cfg, "main_image_generate_enable", False):
        prompt = (getattr(cfg, "main_image_generate_prompt", "") or "").strip()
        if not prompt:
            plan.blockers.append("AI 生成主图：提示词为空")
        plan.steps.append(f"生成主图 ×{getattr(cfg, 'main_image_generate_count', '1')}")

    if cfg.white_bg_enable:
        pos = _fmt_positions(f"{cfg.white_background_pic_1}-{cfg.white_background_pic_2}", n_main)
        if pos == "无":
            plan.blockers.append("主图白底：序号范围为空")
        plan.steps.append(f"主图白底 {pos}")

    if cfg.watermark_enable:
        pos = _fmt_positions(f"{getattr(cfg, 'remove_watermark_pic_1', '1')}-{getattr(cfg, 'remove_watermark_pic_2', '5')}", n_main)
        if pos == "无":
            plan.blockers.append("主图去水印：序号范围为空")
        plan.steps.append(f"主图去水印 {pos}")

    if getattr(cfg, "translate_main_pic_enable", False):
        plan.steps.append("主图文字翻译 " + _fmt_positions(getattr(cfg, "translate_main_pic_indices", "") or "1", n_main))

    if getattr(cfg, "designated_edit_enable", False):
        idx = getattr(cfg, "designated_edit_indices", "") or ""
        prompt = (getattr(cfg, "designated_edit_prompt", "") or "").strip()
        if not idx:
            plan.blockers.append("指定图片编辑：未填序号")
        if not prompt:
            plan.blockers.append("指定图片编辑：提示词为空")
        if idx and prompt:
            plan.steps.append(f"指定编辑 {_fmt_positions(idx, n_main)}")

    detail_ops = [k for k in ("img_detail_white_bg", "img_detail_remove_wm", "img_detail_edit") if getattr(cfg, k, False)]
    if detail_ops:
        expr = getattr(cfg, "white_background_detail_pic_indices", "") or ""
        pos = _fmt_positions(expr, n_detail) if expr else f"1-{n_detail}"
        if pos == "无":
            plan.blockers.append("描述图处理：序号为空")
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
        plan.steps.append("四合一")

    if getattr(cfg, "overlay_watermark_enable", False):
        if not (getattr(cfg, "watermark", "") or "").strip():
            plan.blockers.append("水印叠加：未配置水印文件/文字")
        plan.steps.append("水印叠加")

    return plan

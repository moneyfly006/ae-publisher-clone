"""分类型图片处理：主图 / 描述图 / SKU 图 / 单图，简单或精细（AI）。"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import ImageService
from .image_index import pick
from ..config import AppConfig


@dataclass
class Step:
    name: str
    ok: bool
    url: str = ""
    error: str = ""


@dataclass
class Result:
    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return any(s.ok for s in self.steps)

    def urls(self) -> list[str]:
        return [s.url for s in self.steps if s.ok and s.url]


class ImageWorkflow:
    """按类型分别执行：白底 / 去水印 / AI 精细编辑。"""

    def __init__(self, cfg: AppConfig, svc: ImageService | None = None):
        self.cfg = cfg
        self.svc = svc

    def _ensure(self):
        if self.svc is None:
            from ..api import ApiClient
            self.svc = ImageService(ApiClient(self.cfg.api_base, self.cfg.api_key, self.cfg.timeout), self.cfg)
        return self.svc

    def process_one(self, path, mode: str = "simple", edit_prompt: str = "") -> Result:
        svc = self._ensure()
        res = Result()
        ops = ["white_bg", "remove_wm"] if mode == "simple" else ["edit"]
        for op in ops:
            try:
                if op == "white_bg":
                    u = svc.white_background(path); res.steps.append(Step("白底", True, u))
                elif op == "remove_wm":
                    u = svc.remove_watermark(path); res.steps.append(Step("去水印", True, u))
                else:
                    u = svc.edit(path, edit_prompt or self.cfg.edit_prompt or "clean product photo")
                    res.steps.append(Step("精细编辑", True, u))
            except Exception as e:  # noqa: BLE001
                res.steps.append(Step(op, False, error=str(e)[:120]))
        return res

    # ---- 分类型 ----
    def plan(self) -> dict:
        c = self.cfg
        return {
            "main": {"range": f"{c.white_background_pic_1}-{c.white_background_pic_2}",
                     "white_bg": c.white_bg_enable, "remove_wm": c.watermark_enable,
                     "edit": c.img_main_edit},
            "detail": {"range": c.white_background_detail_pic_indices or "",
                       "white_bg": c.img_detail_white_bg, "remove_wm": c.img_detail_remove_wm,
                       "edit": c.img_detail_edit},
            "sku": {"all": c.white_background_sku_pic,
                    "white_bg": c.img_sku_white_bg, "remove_wm": c.img_sku_remove_wm,
                    "edit": c.img_sku_edit},
        }

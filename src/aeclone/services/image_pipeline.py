"""AI 图片流水线：按配置依次执行 白底 → 去水印 → 通用编辑 → 审查。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import requests

from . import ImageService
from ..api import ApiClient
from ..config import AppConfig


@dataclass
class ImageStep:
    name: str
    ok: bool
    url: str = ""
    error: str = ""


@dataclass
class ImagePipelineResult:
    steps: list[ImageStep] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps) if self.steps else False

    def last_url(self) -> str:
        for s in reversed(self.steps):
            if s.ok and s.url:
                return s.url
        return ""


class ImagePipeline:
    def __init__(self, cfg: AppConfig, client: ApiClient | None = None, workdir: Path | None = None):
        self.cfg = cfg
        self.client = client or ApiClient(cfg.api_base, cfg.api_key, cfg.timeout)
        self.svc = ImageService(self.client)
        self.workdir = workdir or (Path.home() / ".auroralist" / "cache")

    def _download(self, url: str) -> Path:
        self.workdir.mkdir(parents=True, exist_ok=True)
        p = self.workdir / (url.split("/")[-1].split("?")[0] or "img.png")
        if not p.exists():
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            p.write_bytes(r.content)
        return p

    def run(self, image_url: str) -> ImagePipelineResult:
        res = ImagePipelineResult()
        cur = image_url
        try:
            path = self._download(cur)
        except Exception as e:  # noqa: BLE001
            res.steps.append(ImageStep("下载", False, error=str(e)))
            return res
        res.steps.append(ImageStep("下载", True, url=cur))

        if self.cfg.white_bg_enable:
            try:
                u = self.svc.white_background(path)
                res.steps.append(ImageStep("白底", True, u)); cur = u
                path = self._download(u)
            except Exception as e:  # noqa: BLE001
                res.steps.append(ImageStep("白底", False, error=str(e)))

        if self.cfg.watermark_enable:
            try:
                u = self.svc.remove_watermark(path)
                res.steps.append(ImageStep("去水印", True, u)); cur = u
                path = self._download(u)
            except Exception as e:  # noqa: BLE001
                res.steps.append(ImageStep("去水印", False, error=str(e)))

        if self.cfg.edit_enable:
            try:
                u = self.svc.edit(path, self.cfg.edit_prompt)
                res.steps.append(ImageStep("通用编辑", True, u)); cur = u
            except Exception as e:  # noqa: BLE001
                res.steps.append(ImageStep("通用编辑", False, error=str(e)))

        return res

    def review(self, image_url: str, kind: str = "main") -> str:
        prompt = self.cfg.review_main_img_prompt if kind == "main" else self.cfg.review_detail_img_prompt
        return self.client.chat("qwen3-vl-plus", [
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "请审查这张商品图"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]},
        ], max_tokens=400)

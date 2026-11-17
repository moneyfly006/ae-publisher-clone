"""业务服务：文本生成、SKU 优化、图片处理、批量任务。"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..api import ApiClient, ApiError
from ..config import AppConfig

TEXT_MODEL = "qwen-plus"
IMAGE_MODEL = "qwen-image-2.1-turbo"
VISION_MODEL = "qwen3-vl-plus"
IMAGE_GEN_MODEL = "z-image-turbo"


def model_of(cfg, kind: str) -> str:
    """按配置取模型名（kind: text/vision/image_edit/image_gen）。"""
    defaults = {"text": TEXT_MODEL, "vision": VISION_MODEL,
                "image_edit": IMAGE_MODEL, "image_gen": IMAGE_GEN_MODEL}
    key = {"text": "model_text", "vision": "model_vision",
           "image_edit": "model_image_edit", "image_gen": "model_image_gen"}[kind]
    try:
        v = getattr(cfg, key, "") if cfg is not None else ""
    except Exception:
        v = ""
    return (v or "").strip() or defaults[kind]

PROMPT_WHITE_BG = (
    "Remove the background completely and place the product on a pure white background (#FFFFFF). "
    "Keep the product's original shape, color, texture and details unchanged. "
    "Do not add any text, shadow or decoration. Output a clean e-commerce product photo."
)
PROMPT_COMBINED = (
    "Remove the background and place the product on a pure white background (#FFFFFF), "
    "and at the same time remove all watermarks, logos, QR codes, promotional text and labels. "
    "Keep the product's original shape, color and details unchanged. Output a clean e-commerce product photo."
)
PROMPT_WATERMARK = (
    "Remove all watermarks, logos, QR codes, promotional text and labels from this product image. "
    "Keep the product itself unchanged, preserve its original details and colors. "
    "Output a clean product photo without any overlay."
)


@dataclass
class JobResult:
    index: int
    ok: bool
    value: str = ""
    error: str = ""


class TextService:
    def __init__(self, client: ApiClient, cfg: AppConfig):
        self.client = client
        self.cfg = cfg

    def translate_title(self, title: str) -> str:
        return self.client.chat(model_of(getattr(self, "cfg", None), "text"), [
            {"role": "system", "content": self.cfg.title_prompt},
            {"role": "user", "content": title},
        ])

    def write_detail(self, title: str) -> str:
        return self.client.chat(model_of(getattr(self, "cfg", None), "text"), [
            {"role": "system", "content": self.cfg.detail_prompt},
            {"role": "user", "content": title},
        ], max_tokens=1200)

    def optimize_skus(self, names: list[str]) -> list[str]:
        text = "\n".join(names)
        out = self.client.chat(model_of(getattr(self, "cfg", None), "text"), [
            {"role": "system", "content": self.cfg.sku_prompt},
            {"role": "user", "content": text},
        ], max_tokens=800)
        lines = [l.strip(" -•\t") for l in out.splitlines() if l.strip()]
        return lines[: len(names)] if lines else names


class ImageService:
    def __init__(self, client: ApiClient, cfg=None):
        self.client = client
        self.cfg = cfg

    def white_background(self, image: Path) -> str:
        return self.client.images_edit(model_of(self.cfg, "image_edit"), PROMPT_WHITE_BG, [image])

    def remove_watermark(self, image: Path) -> str:
        return self.client.images_edit(model_of(self.cfg, "image_edit"), PROMPT_WATERMARK, [image])

    def combined(self, image: Path) -> str:
        """白底 + 去水印 合并成一次 AI 调用（省一半成本）。"""
        return self.client.images_edit(model_of(self.cfg, "image_edit"), PROMPT_COMBINED, [image])

    def edit(self, image: Path, prompt: str) -> str:
        return self.client.images_edit(model_of(self.cfg, "image_edit"), prompt, [image])

    def generate(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        return self.client.images_generate(model_of(self.cfg, "image_gen"), prompt, size, n)

    def merge(self, images: Iterable[Path], prompt: str = "Merge these product photos into one clean product image on a white background.") -> str:
        return self.client.images_edit(model_of(self.cfg, "image_edit"), prompt, list(images))


class BatchRunner:
    """并发执行任务：支持暂停/恢复/取消、失败重试。"""

    def __init__(self, concurrency: int = 3, retries: int = 1, retry_delay: float = 1.0):
        self.concurrency = max(1, concurrency)
        self.retries = max(0, retries)
        self.retry_delay = retry_delay
        self._paused = threading.Event()
        self._paused.set()
        self._cancelled = threading.Event()

    # ---- 控制 ----
    def pause(self):
        self._paused.clear()

    def resume(self):
        self._paused.set()

    def cancel(self):
        self._cancelled.set()
        self._paused.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _wait(self):
        while not self._paused.wait(0.2):
            if self._cancelled.is_set():
                return
        return

    def run(
        self,
        items: list,
        fn: Callable[[int, object], str],
        on_done: Callable[[JobResult], None] | None = None,
    ) -> list[JobResult]:
        results: list[JobResult] = []
        lock = threading.Lock()

        def work(idx: int, item):
            self._wait()
            if self._cancelled.is_set():
                return JobResult(idx, False, "", "已取消")
            last = ""
            for attempt in range(self.retries + 1):
                try:
                    value = fn(idx, item)
                    res = JobResult(idx, True, value)
                    break
                except Exception as e:  # noqa: BLE001
                    last = str(e)[:300]
                    if attempt < self.retries:
                        time.sleep(self.retry_delay * (attempt + 1))
            else:
                res = JobResult(idx, False, "", last)
            with lock:
                results.append(res)
                if on_done:
                    on_done(res)
            return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = [pool.submit(work, i, it) for i, it in enumerate(items)]
            for f in concurrent.futures.as_completed(futures):
                f.result()
        results.sort(key=lambda r: r.index)
        return results


def client_from_config(cfg, timeout: int | None = None) -> "ApiClient":
    """按配置创建 API 客户端（自动带上代理设置）。"""
    from ..api import ApiClient
    proxy = cfg.proxy_url if getattr(cfg, "proxy_enabled", False) and getattr(cfg, "proxy_url", "") else ""
    return ApiClient(cfg.api_base, cfg.api_key, timeout or cfg.timeout, proxy=proxy)

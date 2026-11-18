"""业务服务：文本生成、SKU 优化、图片处理、批量任务。"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..api import ApiClient, ApiError
from .progress import BUS, track
from ..config import AppConfig

TEXT_MODEL = "qwen-plus"
IMAGE_MODEL = "qwen-image-3.0"
VISION_MODEL = "qwen3-vl-plus"
IMAGE_GEN_MODEL = "z-image-turbo"

# 模型已固定（真机实测）：文本 qwen-plus、视觉 qwen3-vl-plus、图片生成 z-image-turbo。
#
# 图片编辑从 qwen-image-2.1-turbo 换成 qwen-image-3.0（2026-11-18 实测）：
#   同一张输入图、两条完全不同的提示词（纯白背景 vs 深蓝车间火花），
#   2.1-turbo 返回的是**同一个 URL**（说明提示词根本没进推理），
#   3.0 返回两张内容明显不同的图（346KB vs 969KB）。
#   用户反馈的「AI 不按提示词改图」根因就在这里。
#
# 代价：3.0 约 ¥0.30/次（2.1 是 ¥0.20），但它至少会照提示词做。
# 另外 3.0 返回的是阿里云临时签名 URL（约 1 天过期），
# 所以 ImageService 拿到结果后会立刻转存到我们自己的图床（见 _stabilize）。
# 不再让客户自选模型，避免选到不可用/超慢/超贵的。
FIXED_MODELS = {
    "text": TEXT_MODEL,
    "vision": VISION_MODEL,
    "image_edit": IMAGE_MODEL,
    "image_gen": IMAGE_GEN_MODEL,
}

FIXED_MODEL_LABELS = {
    "text": "文本（标题/描述/SKU）",
    "vision": "视觉（图片审查/估重）",
    "image_edit": "图片编辑（白底/去水印/翻译/多图合成）",
    "image_gen": "图片生成（文生图主图）",
}


def model_of(cfg=None, kind: str = "text") -> str:
    """返回固定模型名（kind: text/vision/image_edit/image_gen）。

    故意忽略配置里的 model_*：客户不需要也不允许自己选模型。
    """
    return FIXED_MODELS.get(kind, TEXT_MODEL)

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

    def translate_title(self, title: str) -> str:  # noqa: D401
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

    # 已经在稳定图床上的域名，不用再转存
    _STABLE_HOSTS = ("132.226.1.44:8099", "moneyfly.club", "alicdn.com")

    def _stabilize(self, url: str) -> str:
        """把模型返回的临时 URL 转存到我们自己的图床。

        qwen-image-3.0 返回的是带 Expires 的临时签名地址（约 1 天过期），
        直接写进商品会在审核期间失效。这里立刻下载并转存，换成长久地址。
        """
        u = str(url or "")
        if not u.startswith("http"):
            return u
        # 开了「出图直传店小秘相册」时，这里不转图床：
        # 图马上会被传进相册，先去图床等于白传一次。
        try:
            if getattr(self.cfg, "prefer_dianxiaomi_album", False):
                return u
        except Exception:
            pass
        if any(h in u for h in self._STABLE_HOSTS):
            return u
        try:
            import tempfile as _tf
            from pathlib import Path as _P
            import requests as _rq
            from .hosting import upload as _up
            data = _rq.get(u, timeout=120).content
            if not data:
                return u
            fp = _P(_tf.mkdtemp()) / "out.png"
            fp.write_bytes(data)
            return str(_up(fp))
        except Exception:
            return u

    def white_background(self, image: Path) -> str:
        m = model_of(self.cfg, "image_edit")
        with track("白底", "调用图像编辑", model=m, product=Path(image).name) as t:
            url = self.client.images_edit(m, PROMPT_WHITE_BG, [image])
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def remove_watermark(self, image: Path) -> str:
        m = model_of(self.cfg, "image_edit")
        with track("去水印", "调用图像编辑", model=m, product=Path(image).name) as t:
            url = self.client.images_edit(m, PROMPT_WATERMARK, [image])
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def combined(self, image: Path) -> str:
        """白底 + 去水印 合并成一次 AI 调用（省一半成本）。"""
        m = model_of(self.cfg, "image_edit")
        with track("白底+去水印", "合并调用", model=m, product=Path(image).name) as t:
            url = self.client.images_edit(m, PROMPT_COMBINED, [image])
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def edit(self, image: Path, prompt: str) -> str:
        m = model_of(self.cfg, "image_edit")
        with track("精细编辑", (prompt or "")[:24], model=m, product=Path(image).name) as t:
            url = self.client.images_edit(m, prompt, [image])
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def generate(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        m = model_of(self.cfg, "image_gen")
        with track("文生图", (prompt or "")[:24], model=m, cost=BUS.price_of(m) * max(1, n)) as t:
            url = self.client.images_generate(m, prompt, size, n)
            if isinstance(url, list):
                url = [self._stabilize(u) for u in url]
            else:
                url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def marketing(self, image: Path, prompt: str, n: int = 1) -> list[str]:
        """基于主图生成营销场景图（走编辑模型，保留商品本体）。"""
        m = model_of(self.cfg, "image_edit")
        with track("营销图", (prompt or "")[:24], model=m, cost=BUS.price_of(m) * max(1, n)) as t:
            url = self.client.images_edit(m, prompt, [image])
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return [url]

    # qwen-image-3.0 的服务端限制：单次最多 3 张输入图（第 4 张报
    # "too many input images for this model"）。超了就在这里截断，
    # 避免把服务端错误抛给上层。
    MAX_INPUT_IMAGES = 3

    def edit_many(self, images: Iterable[Path], prompt: str, tag: str = "多图素材编辑") -> str:
        """多图素材编辑：把 target + 参考图一起交给图像编辑模型（图生图）。"""
        m = model_of(self.cfg, "image_edit")
        imgs = list(images)[: self.MAX_INPUT_IMAGES]
        with track(tag, f"{len(imgs)} 张素材 · {(prompt or '')[:16]}", model=m) as t:
            url = self.client.images_edit(m, prompt, imgs)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def merge(self, images: Iterable[Path], prompt: str = "Merge these product photos into one clean product image on a white background.") -> str:
        m = model_of(self.cfg, "image_edit")
        imgs = list(images)
        with track("多图融合", f"{len(imgs)} 张", model=m) as t:
            url = self.client.images_edit(m, prompt, imgs)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url


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

"""业务服务：文本生成、SKU 优化、图片处理、批量任务。"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..api import ApiClient
from .progress import BUS, track
from ..config import AppConfig

TEXT_MODEL = "qwen-plus"
# 图像编辑默认用 turbo：qwen-image-3.0 虽然质量稍好，但要 50~130 秒，
# 还经常被网关判 520/524（实测 48s 失败、132s 才成功各一次）。
# turbo 版约 14 秒且稳定，图上细节足够电商使用。
IMAGE_MODEL = "qwen-image-2.1-turbo"
VISION_MODEL = "qwen3-vl-plus"
IMAGE_GEN_MODEL = "z-image-turbo"
# 营销图是可选增强，优先速度：qwen-image-3.0 常要 60~130s，
# turbo 版实测 ~15s 就能出图，质量足够做营销位。
MARKETING_MODEL = "qwen-image-2.1-turbo"
# 主模型超时/524 时的降级模型（快且稳定）
FALLBACK_IMAGE_MODEL = "qwen-image-2.1-turbo"
# 带自定义提示词的图生图（精细化编辑 / 指定编辑 / 通用编辑）。
# 按客户要求统一使用 qwen-image-2.1-turbo（快、约 ¥0.20/次）。
# 注意：早期实测 turbo 有时不严格遵循提示词；如果以后要换回按提示词改图，
# 只改这一行即可（推荐值 "qwen-image-3.0"），其他代码无需调整。
IMAGE_MODEL_PROMPT = "qwen-image-2.1-turbo"

# 模型已固定（真机实测）：文本 qwen-plus、视觉 qwen3-vl-plus、图片生成 z-image-turbo。
#
# 图片模型一律使用 qwen-image-2.1-turbo（客户指定）：
#   · 白底/去水印/翻译/多图融合 —— 固定提示词，turbo 足够
#   · 精细化编辑/指定编辑/通用编辑 —— 也走 turbo（快、便宜）
# 模型名集中在这里，不再让客户自选，避免选到不可用/超慢/超贵的。
FIXED_MODELS = {
    "text": TEXT_MODEL,
    "vision": VISION_MODEL,
    "image_edit": IMAGE_MODEL,
    "image_prompt": IMAGE_MODEL_PROMPT,
    "image_gen": IMAGE_GEN_MODEL,
    "marketing": MARKETING_MODEL,
}

# 只支持 /images/generations（文生图），不能用于 /images/edits（图片编辑）。
TEXT_TO_IMAGE_ONLY = {"z-image-turbo"}

# 每个任务槽位允许的模型白名单。下拉框只展示这些模型，避免把文生图/
# 文本模型误选到图片编辑等不兼容任务。
MODEL_CAPABILITIES = {
    "qwen-plus": {"text"},
    "qwen-turbo": {"text"},
    "deepseek-chat": {"text"},
    "deepseek-reasoner": {"text"},
    "codex-auto-review": {"text"},
    "qwen3-vl-plus": {"vision"},
    "qwen-image-2.1-turbo": {"image_edit", "image_prompt", "marketing", "image_gen"},
    "qwen-image-3.0": {"image_edit", "image_prompt", "marketing", "image_gen"},
    "z-image-turbo": {"image_gen"},
    "gpt-image-1": {"image_edit", "image_prompt", "marketing", "image_gen"},
    "gpt-image-1.5": {"image_edit", "image_prompt", "marketing", "image_gen"},
    "gpt-image-2": {"image_edit", "image_prompt", "marketing", "image_gen"},
    "gpt-image-2.5-sunburst": {"image_edit", "image_prompt", "marketing", "image_gen"},
}
for _gpt in ("gpt-5.2", "gpt-5.4", "gpt-5.4-mini", "gpt-5.5", "gpt-5.6",
             "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6",
             "gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6.1-sol",
             "gpt-6.1-terra", "gpt-6.2-astra", "gpt-6.2-sol"):
    MODEL_CAPABILITIES[_gpt] = {"text"}


def model_supported(kind: str, name: str) -> bool:
    """该模型是否允许用于指定任务槽位。"""
    return str(kind or "") in MODEL_CAPABILITIES.get(str(name or "").strip(), set())


def models_for(kind: str, available=()) -> list[str]:
    """返回某任务可选的模型；推荐模型固定排第一，其余按已知/API 列表排序。"""
    rec = FIXED_MODELS.get(kind, "")
    names: list[str] = [rec] if rec else []
    for name in list(MODEL_CAPABILITIES) + [str(x or "").strip() for x in available]:
        if name and name not in names and model_supported(kind, name):
            names.append(name)
    return names

FIXED_MODEL_LABELS = {
    "text": "文本（标题/描述/SKU）",
    "vision": "视觉（图片审查/估重）",
    "image_edit": "图片编辑（白底/去水印/翻译/多图合成）",
    "image_prompt": "按提示词改图（精细化/指定/通用编辑）",
    "image_gen": "图片生成（文生图主图）",
    "marketing": "营销图生成",
}

# 配置键 -> 模型槽位。留空时使用上面的固定默认模型。
AI_MODEL_KEYS = {
    "text": "ai_model_text",
    "vision": "ai_model_vision",
    "image_edit": "ai_model_image_edit",
    "image_prompt": "ai_model_image_prompt",
    "image_gen": "ai_model_image_gen",
    "marketing": "ai_model_marketing",
}


def model_of(cfg=None, kind: str = "text") -> str:
    """返回当前生效模型。

    未配置时使用固定默认模型；客户显式选择后严格使用所选模型。
    image_prompt / marketing 未单独选择时跟随图片编辑选择，避免客户只选一处
    却仍有任务暗中使用旧模型。
    """
    default = FIXED_MODELS.get(kind, TEXT_MODEL)
    if cfg is None:
        return default
    value = str(getattr(cfg, AI_MODEL_KEYS.get(kind, ""), "") or "").strip()
    if not value and kind == "image_prompt":
        value = str(getattr(cfg, AI_MODEL_KEYS["image_edit"], "") or "").strip()
    if not value and kind == "marketing":
        value = (str(getattr(cfg, AI_MODEL_KEYS["image_prompt"], "") or "").strip()
                 or str(getattr(cfg, AI_MODEL_KEYS["image_edit"], "") or "").strip())
    return value or default

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


# 文本类 AI 调用的进程内缓存：同一段输入（标题/详情/SKU 列表）重复跑直接命中。
_TEXT_CACHE: dict[str, tuple[float, str]] = {}
_TEXT_CACHE_TTL = 3600 * 6      # 6 小时


def _text_cached(kind: str, key: str, fn):
    import hashlib as _hl
    import time as _t
    h = _hl.sha1(f"{kind}|{key}".encode("utf-8", "ignore")).hexdigest()
    hit = _TEXT_CACHE.get(h)
    if hit and (_t.time() - hit[0]) < _TEXT_CACHE_TTL:
        return hit[1]
    val = fn()
    try:
        _TEXT_CACHE[h] = (_t.time(), val)
        if len(_TEXT_CACHE) > 4000:
            for _k in list(_TEXT_CACHE)[:1000]:
                _TEXT_CACHE.pop(_k, None)
    except Exception:
        pass
    return val


class TextService:
    def __init__(self, client: ApiClient, cfg: AppConfig):
        self.client = client
        self.cfg = cfg

    def translate_title(self, title: str) -> str:  # noqa: D401
        _p = str(getattr(self.cfg, "title_prompt", "") or "")
        return _text_cached("title", f"{_p}|{title}", lambda: self.client.chat(
            model_of(getattr(self, "cfg", None), "text"), [
                {"role": "system", "content": self.cfg.title_prompt},
                {"role": "user", "content": title},
            ]))

    def write_detail(self, title: str, source_text: str = "") -> str:
        _p = str(getattr(self.cfg, "detail_prompt", "") or "")
        _src = str(source_text or "").strip()[:6000]
        user = title if not _src else (
            f"Product title: {title}\n\n"
            f"Original description (facts only, do not copy verbatim):\n{_src}\n\n"
            f"请基于上面的原始信息，重写成专业英文详情（保留真实参数，不要编造）。")
        return _text_cached("detail", f"{_p}|{title}|{_src[:500]}", lambda: self.client.chat(
            model_of(getattr(self, "cfg", None), "text"), [
                {"role": "system", "content": self.cfg.detail_prompt},
                {"role": "user", "content": user},
            ], max_tokens=1600))

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

    def _img_edit(self, prompt: str, files, model: str | None = None):
        """统一的图像编辑调用：网关 5xx 自动重试，超时/失败再降级到 turbo。"""
        m = model or model_of(self.cfg, "image_edit")
        if m in TEXT_TO_IMAGE_ONLY:
            raise RuntimeError(
                f"{m} 只支持文生图，不能用于图片编辑；"
                "请在「AI 模型」里给图片编辑/提示词改图/营销图选择可编辑模型")
        _slow_keys = ("524", "520", "521", "522", "502", "503", "500",
                      "timeout", "Timeout", "timed out", "Read timed out",
                      "Connection aborted", "Connection reset")
        _gw_keys = ("500", "502", "503", "520", "521", "522", "524")
        _default_image_models = {FIXED_MODELS.get(k) for k in
                                 ("image_edit", "image_prompt", "image_gen", "marketing")}
        _allow_fallback = m in _default_image_models
        last = None
        # 网关 5xx/524 多半是排队超时；默认只重试 1 次，避免极端情况下重复计费。
        # 想完全不重试：在 config.json 里把 image_retry_times 设为 0。
        try:
            _retries = int(getattr(self.cfg, "image_retry_times", 1) or 0)
        except Exception:
            _retries = 1
        _retries = max(0, min(2, _retries))
        for attempt in range(_retries + 1):
            try:
                return self.client.images_edit(m, prompt, files,
                                               allow_fallback=_allow_fallback)
            except Exception as e:  # noqa: BLE001
                last = e
                _msg = str(e)
                _gw = any(k in _msg for k in _gw_keys)
                if attempt < _retries and _gw:
                    try:
                        import time as _t
                        _t.sleep(5 * (attempt + 1))
                    except Exception:
                        pass
                    continue
                break
        self.last_attempts = attempt + 1
        _msg = str(last or "")
        # 客户选择了模型就必须按选择运行；只有仍在使用默认模型时才允许降级，
        # 避免静默换成别的模型导致结果/费用与客户预期不一致。
        _default_image_models = {FIXED_MODELS.get(k) for k in
                                 ("image_edit", "image_prompt", "image_gen", "marketing")}
        _allow_fallback = m in _default_image_models
        if _allow_fallback and m != FALLBACK_IMAGE_MODEL and any(k in _msg for k in _slow_keys):
            try:
                BUS.emit("图片重试", f"{m} 失败", f"改用 {FALLBACK_IMAGE_MODEL} 重试", "WARN")
            except Exception:
                pass
            return self.client.images_edit(FALLBACK_IMAGE_MODEL, prompt, files,
                                           allow_fallback=True)
        raise last

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
            url = self._img_edit(PROMPT_WHITE_BG, [image], m)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def remove_watermark(self, image: Path) -> str:
        m = model_of(self.cfg, "image_edit")
        with track("去水印", "调用图像编辑", model=m, product=Path(image).name) as t:
            url = self._img_edit(PROMPT_WATERMARK, [image], m)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def combined(self, image: Path) -> str:
        """白底 + 去水印 合并成一次 AI 调用（省一半成本）。"""
        m = model_of(self.cfg, "image_edit")
        with track("白底+去水印", "合并调用", model=m, product=Path(image).name) as t:
            url = self._img_edit(PROMPT_COMBINED, [image], m)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def edit(self, image: Path, prompt: str, model: str | None = None) -> str:
        m = model or model_of(self.cfg, "image_edit")
        with track("精细编辑", (prompt or "")[:24], model=m, product=Path(image).name) as t:
            url = self._img_edit(prompt, [image], m)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def generate(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        m = model_of(self.cfg, "image_gen")
        with track("文生图", (prompt or "")[:24], model=m, cost=BUS.price_of(m) * max(1, n)) as t:
            url = self.client.images_generate(m, prompt, size, n,
                allow_fallback=m == FIXED_MODELS.get("image_gen"))
            if isinstance(url, list):
                url = [self._stabilize(u) for u in url]
            else:
                url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def marketing(self, image: Path, prompt: str, n: int = 1) -> list[str]:
        """基于主图生成营销场景图（默认用 turbo，支持客户选择模型）。"""
        m = model_of(self.cfg, "marketing")
        with track("营销图", (prompt or "")[:24], model=m, cost=BUS.price_of(m) * max(1, n)) as t:
            url = self._img_edit(prompt, [image], m)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return [url]

    # qwen-image-3.0 的服务端限制：单次最多 3 张输入图（第 4 张报
    # "too many input images for this model"）。超了就在这里截断，
    # 避免把服务端错误抛给上层。
    MAX_INPUT_IMAGES = 3

    def edit_many(self, images: Iterable[Path], prompt: str, tag: str = "多图素材编辑",
                  model: str | None = None) -> str:
        """多图素材编辑：把 target + 参考图一起交给图像编辑模型（图生图）。"""
        m = model or model_of(self.cfg, "image_edit")
        imgs = list(images)[: self.MAX_INPUT_IMAGES]
        with track(tag, f"{len(imgs)} 张素材 · {(prompt or '')[:16]}", model=m) as t:
            url = self._img_edit(prompt, imgs, m)
            url = self._stabilize(url)
            t.detail = str(url)[-40:]
            return url

    def merge(self, images: Iterable[Path], prompt: str = "Merge these product photos into one clean product image on a white background.") -> str:
        m = model_of(self.cfg, "image_edit")
        imgs = list(images)
        with track("多图融合", f"{len(imgs)} 张", model=m) as t:
            url = self._img_edit(prompt, imgs, m)
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

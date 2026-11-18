"""OpenAI 兼容 API 客户端：文本 / 图片。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

import requests


# ---- 轻量缓存：模型/价格几分钟内复用，避免每次切页面都打网络 ----
_CACHE: dict = {}
_CACHE_LOCK = __import__("threading").Lock()
_CACHE_TTL = 300.0


def _cache_get(key: str):
    with _CACHE_LOCK:
        item = _CACHE.get(key)
    if not item:
        return None
    ts, value = item
    if __import__("time").time() - ts > _CACHE_TTL:
        return None
    return value


def _cache_put(key: str, value) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (__import__("time").time(), value)


def _pe(stage, action="", detail="", level="INFO", model="", cost=0.0, elapsed=0.0, product=""):
    """懒加载进度上报（避免与 services 包循环导入）。"""
    try:
        from .services.progress import emit as _emit
        _emit(stage, action, detail, level, model, cost, elapsed, product)
    except Exception:
        pass


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def classify_api_error(ex: Exception) -> str:
    """把 API 异常归成三类，供启动前预检决定是否阻断。

    - "auth"  : Key 无效/无权限 —— 一定跑不通，必须阻断
    - "quota" : 额度不足/限流   —— 一定跑不通，必须阻断
    - "other" : 网络抖动/服务端 5xx —— 可能瞬时，只告警不阻断
    """
    status = int(getattr(ex, "status", 0) or 0)
    msg = str(ex)
    low = msg.lower()
    if status in (401, 403) or "invalid token" in low or "unauthorized" in low:
        return "auth"
    if status in (402, 429) or "quota" in low or "额度" in msg or "balance" in low:
        return "quota"
    return "other"


class ApiClient:
    def __init__(self, base: str, key: str, timeout: int = 180, session: requests.Session | None = None,
                 proxy: str = ""):
        self.base = base.rstrip("/")
        self.key = key.strip()
        try:
            self.timeout = float(timeout)
        except Exception:
            self.timeout = 180.0
        self.session = session or requests.Session()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        # 模型不可用时的自动回退（主模型 -> 备用模型）
        self.fallback: dict[str, str] = {
            "qwen-image-3.0": "qwen-image-2.1-turbo",
            "qwen-image-2.1-turbo": "qwen-image-2.1-turbo",
            "z-image-turbo": "z-image-turbo",
            "deepseek-reasoner": "deepseek-chat",
            "deepseek-chat": "qwen-plus",
            "qwen3-vl-plus": "qwen-plus",
        }

    # ---------- 基础 ----------
    def _headers(self, extra: dict | None = None) -> dict:
        # Key 为空时不要发 "Bearer " 出去：服务端只会回 "Invalid token"，
        # 用户看不出是「没填 Key」还是「Key 过期」。这里直接给可操作的错误。
        if not self.key:
            raise ApiError("未配置 API Key：请在「设置 → API 配置」填写后再运行", 401, "")
        h = {"Authorization": f"Bearer {self.key}"}
        if extra:
            h.update(extra)
        return h

    def _url(self, path: str) -> str:
        return f"{self.base}{path if path.startswith('/') else '/' + path}"

    def models(self, force: bool = False) -> list[str]:
        key = f"models:{self.base}:{self.key[:8]}"
        cached = None if force else _cache_get(key)
        if cached is not None:
            return list(cached)
        r = self.session.get(self._url("/v1/models"), headers=self._headers(), timeout=30)
        if r.status_code >= 400:
            # 带上服务端原文，否则只剩 "获取模型失败: 401"，看不出是 Key 无效还是权限问题
            raise ApiError(f"获取模型失败({r.status_code}): {self._extract_error(r)}",
                           r.status_code, r.text[:300])
        out = [m.get("id", "") for m in r.json().get("data", [])]
        _cache_put(key, out)
        return out

    # ---------- 账务 ----------
    def billing_subscription(self) -> dict:
        r = self.session.get(self._url("/v1/dashboard/billing/subscription"),
                             headers=self._headers(), timeout=30)
        return r.json() if r.ok else {}

    def billing_usage(self, start: str = "2024-01-01", end: str = "2030-12-31") -> dict:
        r = self.session.get(self._url("/v1/dashboard/billing/usage"),
                             headers=self._headers(),
                             params={"start_date": start, "end_date": end}, timeout=30)
        return r.json() if r.ok else {}

    def balance(self) -> dict:
        sub = self.billing_subscription()
        use = self.billing_usage()
        limit = float(sub.get("hard_limit_usd") or 0)
        used = float(use.get("total_usage") or 0) / 100.0
        return {"limit": limit, "used": used, "remain": max(0.0, limit - used)}

    # ---------- 文本 ----------
    def chat(self, model: str, messages: list[dict], temperature: float = 0.6, max_tokens: int = 2048) -> str:
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        r = self.session.post(
            self._url("/v1/chat/completions"),
            headers=self._headers({"Content-Type": "application/json"}),
            data=json.dumps(payload).encode(),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ApiError(self._extract_error(r), r.status_code, r.text[:500])
        data = r.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

    def chat_stream(self, model: str, messages: list[dict]) -> Iterator[str]:
        payload = {"model": model, "messages": messages, "stream": True}
        with self.session.post(
            self._url("/v1/chat/completions"),
            headers=self._headers({"Content-Type": "application/json"}),
            data=json.dumps(payload).encode(),
            stream=True,
            timeout=self.timeout,
        ) as r:
            if r.status_code >= 400:
                raise ApiError(self._extract_error(r), r.status_code, r.text[:500])
            for line in r.iter_lines():
                if not line or not line.startswith(b"data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == b"[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except Exception:
                    continue
                delta = (obj.get("choices") or [{}])[0].get("delta", {})
                if delta.get("content"):
                    yield delta["content"]

    # ---------- 价格 ----------
    def pricing(self, force: bool = False) -> list[dict]:
        """公开价格表（无需鉴权，缓存 5 分钟）。"""
        key = f"pricing:{self.base}"
        cached = None if force else _cache_get(key)
        if cached is not None:
            return list(cached)
        try:
            r = self.session.get(self._url("/api/pricing"), timeout=20)
            data = (r.json() or {}).get("data") or []
            out = []
            for m in data:
                name = m.get("model_name") or ""
                if not name:
                    continue
                per_call = float(m.get("model_price") or 0)
                ratio = float(m.get("model_ratio") or 0)
                out.append({"model": name, "per_call": per_call, "ratio": ratio})
            _cache_put(key, out)
            return out
        except Exception:
            return []

    # ---------- 图片 ----------
    def images_edit(self, model: str, prompt: str, images: Iterable[Path | bytes], size: str = "1024x1024") -> str:
        files = []
        for i, img in enumerate(images):
            if isinstance(img, Path):
                content = img.read_bytes()
                name = img.name
            else:
                content, name = img, f"image_{i}.png"
            files.append(("image", (name, content, "image/png")))
        data = {"model": model, "prompt": prompt, "size": size, "n": "1"}
        _t0 = __import__("time").time()
        r = self.session.post(
            self._url("/v1/images/edits"),
            headers=self._headers(),
            data=data,
            files=files,
            timeout=self.timeout,
        )
        _pe("API", "POST /v1/images/edits", f"HTTP {r.status_code} · {len(files)} 张图",
            "OK" if r.status_code < 400 else "ERROR", model, 0.0,
            __import__("time").time() - _t0)
        if r.status_code >= 400:
            msg = self._extract_error(r)
            alt = self._fallback_for(model, "image")
            if alt and self._is_model_missing(msg):
                data["model"] = alt
                r2 = self.session.post(self._url("/v1/images/edits"), headers=self._headers(),
                                       data=data, files=files, timeout=self.timeout)
                if r2.status_code < 400:
                    arr2 = r2.json().get("data") or []
                    if arr2:
                        return arr2[0].get("url", "")
            raise ApiError(msg, r.status_code, r.text[:500])
        arr = r.json().get("data") or []
        if not arr:
            raise ApiError("图片接口未返回结果", r.status_code, r.text[:300])
        return arr[0].get("url", "")

    def images_generate(self, model: str, prompt: str, size: str = "1024x1024", n: int = 1) -> str:
        payload = {"model": model, "prompt": prompt, "size": size, "n": max(1, int(n))}
        _t0 = __import__("time").time()
        r = self.session.post(
            self._url("/v1/images/generations"),
            headers=self._headers({"Content-Type": "application/json"}),
            data=json.dumps(payload).encode(),
            timeout=self.timeout,
        )
        _pe("API", "POST /v1/images/generations", f"HTTP {r.status_code} · n={n}",
            "OK" if r.status_code < 400 else "ERROR", model, 0.0,
            __import__("time").time() - _t0)
        if r.status_code >= 400:
            msg = self._extract_error(r)
            alt = self._fallback_for(model, "image")
            if alt and self._is_model_missing(msg):
                payload["model"] = alt
                r2 = self.session.post(self._url("/v1/images/generations"),
                                       headers=self._headers({"Content-Type": "application/json"}),
                                       data=json.dumps(payload).encode(), timeout=self.timeout)
                if r2.status_code < 400:
                    arr2 = r2.json().get("data") or []
                    if arr2:
                        return arr2[0].get("url", "")
            raise ApiError(msg, r.status_code, r.text[:500])
        arr = r.json().get("data") or []
        if not arr:
            raise ApiError("图片接口未返回结果", r.status_code, r.text[:300])
        return arr[0].get("url", "")

    @staticmethod
    def _is_model_missing(msg: str) -> bool:
        low = (msg or "").lower()
        return any(k in low for k in ("model_not_found", "no available channel", "model not found",
                                      "does not exist", "无可用渠道", "模型不存在", "invalid model"))

    def _fallback_for(self, model: str, kind: str = "image") -> str | None:
        alt = self.fallback.get(model)
        if not alt or alt == model:
            alt = "qwen-image-2.1-turbo" if kind == "image" else "qwen-plus"
        return alt if alt != model else None

    @staticmethod
    def _extract_error(r: requests.Response) -> str:
        try:
            j = r.json()
            err = j.get("error")
            if isinstance(err, dict):
                return err.get("message") or str(err)
            if isinstance(err, str):
                return err
            return j.get("message") or f"HTTP {r.status_code}"
        except Exception:
            return f"HTTP {r.status_code}"

"""OpenAI 兼容 API 客户端：文本 / 图片。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Iterator

import requests


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class ApiClient:
    def __init__(self, base: str, key: str, timeout: int = 180, session: requests.Session | None = None):
        self.base = base.rstrip("/")
        self.key = key.strip()
        self.timeout = timeout
        self.session = session or requests.Session()

    # ---------- 基础 ----------
    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.key}"}
        if extra:
            h.update(extra)
        return h

    def _url(self, path: str) -> str:
        return f"{self.base}{path if path.startswith('/') else '/' + path}"

    def models(self) -> list[str]:
        r = self.session.get(self._url("/v1/models"), headers=self._headers(), timeout=30)
        if r.status_code >= 400:
            raise ApiError(f"获取模型失败: {r.status_code}", r.status_code, r.text[:300])
        return [m.get("id", "") for m in r.json().get("data", [])]

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
        r = self.session.post(
            self._url("/v1/images/edits"),
            headers=self._headers(),
            data=data,
            files=files,
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ApiError(self._extract_error(r), r.status_code, r.text[:500])
        arr = r.json().get("data") or []
        if not arr:
            raise ApiError("图片接口未返回结果", r.status_code, r.text[:300])
        return arr[0].get("url", "")

    def images_generate(self, model: str, prompt: str, size: str = "1024x1024") -> str:
        payload = {"model": model, "prompt": prompt, "size": size, "n": 1}
        r = self.session.post(
            self._url("/v1/images/generations"),
            headers=self._headers({"Content-Type": "application/json"}),
            data=json.dumps(payload).encode(),
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ApiError(self._extract_error(r), r.status_code, r.text[:500])
        arr = r.json().get("data") or []
        if not arr:
            raise ApiError("图片接口未返回结果", r.status_code, r.text[:300])
        return arr[0].get("url", "")

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

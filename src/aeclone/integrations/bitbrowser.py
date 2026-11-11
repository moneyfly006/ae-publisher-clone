"""比特浏览器本地 API 客户端（默认 127.0.0.1:54345）。"""
from __future__ import annotations

import json

import requests


class BitBrowser:
    def __init__(self, base: str = "http://127.0.0.1:54345", timeout: int = 10):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict | None = None):
        r = requests.post(f"{self.base}{path}", json=payload or {}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base}/health", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def list_browsers(self) -> list[dict]:
        data = self._post("/browser/list", {"page": 0, "pageSize": 100})
        return (data.get("data") or {}).get("list", [])

    def find_by_name(self, name: str) -> dict | None:
        for b in self.list_browsers():
            if b.get("name") == name:
                return b
        return None

    def open(self, browser_id: str) -> dict:
        data = self._post("/browser/open", {"id": browser_id})
        return data.get("data") or {}

    def close(self, browser_id: str) -> None:
        try:
            self._post("/browser/close", {"id": browser_id})
        except Exception:
            pass

    def ws_endpoint(self, browser_id: str) -> str:
        info = self.open(browser_id)
        ws = info.get("ws") or info.get("http")
        if not ws:
            raise RuntimeError(f"比特浏览器未返回 CDP 端点: {json.dumps(info)[:200]}")
        return ws

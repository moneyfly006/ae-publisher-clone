"""站斧浏览器本地 API 客户端。"""
from __future__ import annotations

import json

import requests


class ZhanfuBrowser:
    def __init__(self, base: str = "http://127.0.0.1", timeout: int = 10):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _get(self, port: int, path: str):
        r = requests.get(f"{self.base}:{port}{path}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def version(self, port: int) -> dict:
        return self._get(port, "/json/version")

    def list_pages(self, port: int) -> list[dict]:
        return self._get(port, "/json/list")

    def open_url(self, port: int, url: str) -> None:
        try:
            requests.get(f"{self.base}:{port}/json/new?{url}", timeout=self.timeout)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"站斧打开页面失败: {e}") from e

    def ws_endpoint(self, port: int) -> str:
        info = self.version(port)
        ws = info.get("webSocketDebuggerUrl")
        if not ws:
            raise RuntimeError("站斧未返回 CDP 端点")
        return ws

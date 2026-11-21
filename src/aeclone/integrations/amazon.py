"""Amazon 适配器骨架（店小秘 amazonProduct 接口）。

已实测可读：POST /api/amazonProduct/pageList.json（无 Amazon 店铺时返回空列表）。
刊登/保存接口需接入 Amazon SP-API 或店小秘 Amazon 编辑器，标注为待接入。
"""
from __future__ import annotations

import requests

BASE = "https://www.dianxiaomi.com/api"
UA = "Mozilla/5.0 Chrome/120"


class AmazonNotReady(RuntimeError):
    pass


class AmazonClient:
    def __init__(self, cookies: dict | None = None, timeout: int = 30):
        self.timeout = timeout
        self.s = requests.Session()
        if cookies:
            self.s.cookies.update(cookies)
        self.s.headers.update({"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"})

    def _post(self, path: str, data: dict | None = None) -> dict:
        r = self.s.post(BASE + path, data=data or {}, timeout=self.timeout)
        r.raise_for_status()
        d = r.json()
        if d.get("code") not in (0, "0"):
            raise AmazonNotReady(d.get("msg") or f"接口错误 {d.get('code')}")
        return d

    def page_list(self, page: int = 1, page_size: int = 20, state: str = "draft",
                  shop_id: str = "-1", keyword: str = "") -> dict:
        """采集箱/待发布/在线列表。state: draft/offline/online。"""
        d = self._post("/amazonProduct/pageList.json", {
            "page": page, "pageSize": page_size, "shopId": shop_id,
            "dxmState": state, "searchValue": keyword,
        })
        return d.get("data") or {}

    def save_product(self, *args, **kwargs):
        raise AmazonNotReady("Amazon 刊登适配器尚未接入：需要 Amazon SP-API 或店小秘 Amazon 编辑器接口")

    def publish(self, *args, **kwargs):
        raise AmazonNotReady("Amazon 发布适配器尚未接入")

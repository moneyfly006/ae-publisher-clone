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

    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self.s.get(BASE + path, params=params, timeout=self.timeout)
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
        data = d.get("data") or {}
        data["products"] = extract_list(data)
        return data

    def detail(self, product_id: str) -> dict:
        """Amazon 详情：优先店小秘编辑器接口，未接入时明确报错。"""
        try:
            d = self._get("/amazonProduct/edit.json", {"id": str(product_id)})
            return (d.get("data") or {}).get("product") or {}
        except Exception as ex:
            raise AmazonNotReady(f"Amazon 详情接口未接入：{str(ex)[:80]}")

    def save_product(self, *args, **kwargs):
        raise AmazonNotReady("Amazon 刊登适配器尚未接入：需要 Amazon SP-API 或店小秘 Amazon 编辑器接口")

    def publish(self, *args, **kwargs):
        raise AmazonNotReady("Amazon 发布适配器尚未接入")

def normalize_product(row: dict) -> dict:
    """把店小秘 amazon 列表行整理成统一结构。"""
    if not isinstance(row, dict):
        return {}
    return {
        "id": str(row.get("id") or row.get("productId") or row.get("idStr") or ""),
        "title": str(row.get("title") or row.get("subject") or row.get("productName") or ""),
        "sku": str(row.get("sku") or row.get("sellerSku") or ""),
        "asin": str(row.get("asin") or row.get("productId") or ""),
        "price": str(row.get("price") or row.get("salePrice") or ""),
        "stock": row.get("stock") or row.get("quantity") or 0,
        "status": str(row.get("dxmState") or row.get("status") or ""),
        "marketplace": str(row.get("marketPlace") or row.get("marketplace") or ""),
        "shop_id": str(row.get("shopId") or ""),
        "update_time": row.get("updateTime") or row.get("gmtModified") or "",
    }


def extract_list(data: dict) -> list[dict]:
    """兼容 data.page.list / data.list 两种返回。"""
    page = (data or {}).get("page") or {}
    rows = page.get("list") or (data or {}).get("list") or []
    return [normalize_product(r) for r in rows if isinstance(r, dict)]

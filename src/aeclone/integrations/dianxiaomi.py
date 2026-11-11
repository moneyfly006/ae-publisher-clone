"""店小秘（dianxiaomi）客户端。

说明：店小秘的接口是私有 Web API，必需 Cookie（dxm_i / dxm_t）才能访问。
本模块按逆向得到的已知端点实现，端点集中在 ENDPOINTS，便于后续用抓包校正。
"""
from __future__ import annotations

import json
from typing import Any

import requests

BASE = "https://www.dianxiaomi.com"

ENDPOINTS = {
    # ---- 已实测（2026-11-11）----
    "user_info": "/api/userIn.json",
    "vip": "/api/getVipActiveTime.json",
    "album": "/api/album/index.json",
    "product_list": "/api/smtProduct/list.json",
    # ---- 逆向推断，待校正 ----
    "comment": "/api/comm/comment.json",
    "banned_word": "/api/bannedWord/smtCheckB.json",
    "upload_sign": "/api/cos/getSign.json",
    "upload_callback": "/api/cos_dxm_call_back.json",
    "video_callback": "/api/smtmedia/callback.json",
    "product_save": "",
    "product_detail": "",
    "category": "",
}


class DianxiaomiError(RuntimeError):
    pass


class DianxiaomiClient:
    def __init__(self, cookies: dict[str, str], base: str = BASE, timeout: int = 20,
                 overrides: dict | None = None):
        self.base = base.rstrip("/")
        self.endpoints = dict(ENDPOINTS)
        if overrides:
            self.endpoints.update({k: v for k, v in overrides.items() if v})
        self.timeout = timeout
        self.session = requests.Session()
        self.session.cookies.update(cookies)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120 Safari/537.36",
            "Referer": f"{self.base}/web/smt/smtProductList/offline",
            "X-Requested-With": "XMLHttpRequest",
        })

    # ---------- 基础 ----------
    def _url(self, key: str) -> str:
        return self.base + self.endpoints.get(key, ENDPOINTS.get(key, "/"))

    def _get(self, key: str, params: dict | None = None) -> Any:
        r = self.session.get(self._url(key), params=params, timeout=self.timeout)
        data = self._parse(r)
        if isinstance(data, dict) and str(data.get("code")) == "2005":
            import time as _t
            _t.sleep(2.5)
            r = self.session.get(self._url(key), params=params, timeout=self.timeout)
            data = self._parse(r)
        return data

    def _post(self, key: str, payload: dict | None = None) -> Any:
        r = self.session.post(self._url(key), data=payload or {}, timeout=self.timeout)
        data = self._parse(r)
        if isinstance(data, dict) and str(data.get("code")) == "2005":
            import time as _t
            _t.sleep(2.5)
            r = self.session.post(self._url(key), data=payload or {}, timeout=self.timeout)
            data = self._parse(r)
        return data

    @staticmethod
    def _parse(r: requests.Response) -> Any:
        if r.status_code in (401, 403):
            raise DianxiaomiError("登录已失效，请重新登录店小秘")
        if r.status_code >= 400:
            raise DianxiaomiError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except Exception:
            if "login" in r.url or "登录" in r.text[:200]:
                raise DianxiaomiError("登录已失效，请重新登录店小秘")
            raise DianxiaomiError(f"返回非 JSON：{r.text[:200]}")
        if isinstance(data, dict) and data.get("code") not in (None, 0, 200, "0", "200"):
            msg = data.get("msg") or data.get("message") or str(data)[:200]
            raise DianxiaomiError(msg)
        return data

    # ---------- 业务 ----------
    def check_login(self) -> bool:
        try:
            info = self.user_info()
            return bool(info.get("id") or info.get("account"))
        except DianxiaomiError as e:
            if "登录" in str(e):
                return False
            return True
        except Exception:
            return False

    def _check_login_legacy(self) -> bool:
        try:
            self._get("album")
            return True
        except DianxiaomiError as e:
            if "登录" in str(e):
                return False
            return True
        except Exception:
            return False

    def album_index(self) -> Any:
        return self._get("album")

    def user_info(self) -> dict:
        data = self._get("user_info")
        return data.get("data") or {}

    def vip_info(self) -> dict:
        data = self._get("vip")
        return data.get("data") or {}

    def product_list(self, page: int = 1, size: int = 100, keyword: str = "") -> list[dict]:
        """返回产品列表（已实测结构：data.page.list）。"""
        params = {"page": page, "pageSize": size}
        if keyword:
            params["searchContent"] = keyword
        data = self._get("product_list", params)
        inner = data.get("data") or {}
        page_obj = inner.get("page") or {}
        items = page_obj.get("list") or []
        out = []
        for it in items:
            imgs = it.get("imageURLs")
            if isinstance(imgs, str):
                try:
                    import json as _j
                    imgs = _j.loads(imgs)
                except Exception:
                    imgs = [imgs] if imgs.startswith("http") else []
            imgs = imgs if isinstance(imgs, list) else []
            out.append({
                "id": str(it.get("id") or it.get("productId") or ""),
                "title": it.get("subject") or it.get("title") or "",
                "product_id": it.get("productId"),
                "category_id": it.get("categoryId"),
                "shop_id": it.get("shopId"),
                "price": it.get("productPrice"),
                "status": it.get("productStatusType"),
                "images": [str(x) for x in imgs if x],
                "image": (str(imgs[0]) if imgs else "") or "",
                "raw": it,
            })
        return out

    def product_detail(self, product_id: str) -> Any:
        return self._get("product_detail", {"id": product_id})

    def category_tree(self) -> Any:
        return self._get("category")

    def comment(self, product_id: str, text: str) -> Any:
        return self._post("comment", {"id": product_id, "remark": text})

    def check_banned_word(self, text: str) -> Any:
        return self._post("banned_word", {"content": text})

    def upload_callback(self, file_id: str, file_name: str, file_size: int, full_cid: str = "", bucket: str = "wxalbum") -> Any:
        return self._post("upload_callback", {
            "fileId": file_id, "fileName": file_name, "fileSize": file_size,
            "fullCid": full_cid, "bucket": bucket,
        })

    def save_product(self, product: dict) -> Any:
        return self._post("product_save", product)

    def upload_sign(self, file_name: str, file_size: int) -> dict:
        data = self._post("upload_sign", {"fileName": file_name, "fileSize": file_size})
        return (data.get("data") or data) if isinstance(data, dict) else {}

    def search_price_templates(self, keyword: str = "") -> Any:
        return self._get("template_search", {"keyword": keyword})

    def video_callback(self, file_id: str, file_name: str, file_size: int) -> Any:
        return self._post("video_callback", {"fileId": file_id, "fileName": file_name, "fileSize": file_size})

    def cookies_dict(self) -> dict:
        return dict(self.session.cookies)


def from_browser_cookies(cookies: dict[str, str], overrides: dict | None = None) -> DianxiaomiClient:
    return DianxiaomiClient({k: v for k, v in cookies.items() if k in ("dxm_i", "dxm_t")} or cookies,
                            overrides=overrides)

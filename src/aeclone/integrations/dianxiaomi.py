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
    "upload_callback": "/api/cos/cosDxmCallBack.json",
    "album_upload_url": "/api/album/toUploadPicFromNet.json",   # 实测：按 URL 上传图片
    "video_callback": "/api/smtmedia/callback.json",
    "product_save": "/api/smtProduct/add.json",   # 实测：编辑器保存
    "product_detail": "/api/smtProduct/edit.json",  # 实测：编辑器加载
    "product_detail": "",
    "category": "",
    "template_search": "/api/smtAdjustPrice/list.json",   # 实测：区域调价模板列表
    "attribute_template": "/api/smtAttributeTemplate/list.json",
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
        headers = None
        if key == "product_detail" and params and params.get("id"):
            headers = {"Referer": f"https://www.dianxiaomi.com/web/smt/edit?id={params['id']}"}
        r = self.session.get(self._url(key), params=params, timeout=self.timeout, headers=headers)
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

    def shops(self) -> list[dict]:
        info = self.user_info()
        sm = info.get("shopMap") or {}
        out = []
        for k, v in sm.items():
            try:
                sid = int(v.get("id") or k)
            except Exception:
                sid = k
            if sid == 0:
                continue
            out.append({"id": str(sid), "name": v.get("name") or str(sid)})
        return out

    def vip_info(self) -> dict:
        data = self._get("vip")
        return data.get("data") or {}

    EDITABLE_STATUS = {"editingRequired", "offline", "publishFail", "draft", "waitPublish"}
    BLOCKED_STATUS = {"auditing", "onSelling", "published", "online"}

    def _map_item(self, it: dict) -> dict:
        imgs = it.get("imageURLs")
        if isinstance(imgs, str):
            raw = imgs.strip()
            if raw.startswith("["):
                try:
                    import json as _j
                    imgs = _j.loads(raw)
                except Exception:
                    imgs = []
            else:
                imgs = [x.strip() for x in raw.split(";") if x.strip().startswith("http")]
        imgs = imgs if isinstance(imgs, list) else []
        return {
            "id": str(it.get("id") or it.get("productId") or ""),
            "title": it.get("subject") or it.get("title") or "",
            "product_id": it.get("productId"),
            "category_id": it.get("categoryId"),
            "shop_id": it.get("shopId"),
            "price": it.get("productPrice"),
            "status": it.get("productStatusType"),
            "status_type": it.get("productStatusType"),
            "images": [str(x) for x in imgs if x],
            "image": (str(imgs[0]) if imgs else ""),
            "raw": it,
        }

    def product_page(self, page: int = 1, size: int = 100, shop_id=None,
                     status: str | None = None, keyword: str | None = None) -> tuple[list[dict], dict]:
        params: dict = {"page": page, "pageSize": size}
        if shop_id:
            params["shopId"] = shop_id
        if status:
            params["productStatusType"] = status
        if keyword:
            params["productName"] = keyword
            params["searchType"] = "productName"
        data = self._get("product_list", params)
        inner = data.get("data") or {}
        po = inner.get("page") or {}
        items = [self._map_item(x) for x in (po.get("list") or [])]
        meta = {"total": po.get("totalSize"), "total_pages": po.get("totalPage"),
                "page": po.get("pageNo") or page, "size": po.get("pageSize") or size}
        return items, meta

    def iter_products(self, shop_id=None, status: str | None = None,
                      max_items: int = 0, max_pages: int = 20) -> list[dict]:
        out: list[dict] = []
        page = 1
        while page <= max_pages:
            items, meta = self.product_page(page, 100, shop_id, status)
            out.extend(items)
            if max_items and len(out) >= max_items:
                return out[:max_items]
            if page >= int(meta.get("total_pages") or 1):
                break
            page += 1
        return out

    def counts(self, shop_id=None) -> dict:
        """各状态数量（只取 1 条，速度快）。"""
        out = {}
        for st in ("onSelling", "editingRequired", "auditing", "offline", "publishFail"):
            try:
                _, meta = self.product_page(1, 1, shop_id, st)
                out[st] = int(meta.get("total") or 0)
            except Exception:
                out[st] = 0
        return out

    def drafts(self, limit: int = 0, shop_id=None) -> list[dict]:
        return self.iter_products(shop_id, "editingRequired", limit)

    def online(self, limit: int = 0, shop_id=None) -> list[dict]:
        return self.iter_products(shop_id, "onSelling", limit)

    def pending_products(self, limit: int = 0, shop_id=None) -> list[dict]:
        return self.iter_products(shop_id, "editingRequired", limit)

    def album_index(self) -> Any:
        return self._get("album")

    def user_info(self) -> dict:
        data = self._get("user_info")
        return data.get("data") or {}

    def shops(self) -> list[dict]:
        info = self.user_info()
        sm = info.get("shopMap") or {}
        out = []
        for k, v in sm.items():
            try:
                sid = int(v.get("id") or k)
            except Exception:
                sid = k
            if sid == 0:
                continue
            out.append({"id": str(sid), "name": v.get("name") or str(sid)})
        return out

    def vip_info(self) -> dict:
        data = self._get("vip")
        return data.get("data") or {}

    EDITABLE_STATUS = {"editingRequired", "offline", "publishFail", "draft", "waitPublish"}
    BLOCKED_STATUS = {"auditing", "onSelling", "published", "online"}

    def upload_image_from_url(self, image_url: str, full_cid: str = "") -> dict:
        """按 URL 上传图片到店小秘相册（实测可用）。"""
        payload = {"urls": image_url}
        if full_cid:
            payload["fullCid"] = full_cid
        data = self._post("album_upload_url", payload)
        return data.get("data") or {}

    def upload_sign(self, file_name: str, file_size: int) -> dict:
        data = self._post("upload_sign", {"fileName": file_name, "fileSize": file_size})
        return (data.get("data") or data) if isinstance(data, dict) else {}

    def search_price_templates(self, keyword: str = "", page: int = 1, size: int = 50) -> list[dict]:
        """区域调价模板列表（实测接口 /api/smtAdjustPrice/list.json）。"""
        params = {"pageNo": page, "pageSize": size}
        if keyword:
            params["searchValue"] = keyword
            params["searchType"] = 1
        data = self._get("template_search", params)
        page_obj = ((data.get("data") or {}).get("page") or {})
        return page_obj.get("list") or []

    def video_callback(self, file_id: str, file_name: str, file_size: int) -> Any:
        return self._post("video_callback", {"fileId": file_id, "fileName": file_name, "fileSize": file_size})

    def cookies_dict(self) -> dict:
        return dict(self.session.cookies)


def from_browser_cookies(cookies: dict[str, str], overrides: dict | None = None) -> DianxiaomiClient:
    return DianxiaomiClient({k: v for k, v in cookies.items() if k in ("dxm_i", "dxm_t")} or cookies,
                            overrides=overrides)

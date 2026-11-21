"""店小秘（dianxiaomi）客户端。

说明：店小秘的接口是私有 Web API，必需 Cookie（dxm_i / dxm_t）才能访问。
本模块按逆向得到的已知端点实现，端点集中在 ENDPOINTS，便于后续用抓包校正。
"""
from __future__ import annotations

from typing import Any

import requests

BASE = "https://www.dianxiaomi.com"

# 打包版 certifi 路径在 app 被移动/重建后可能失效（"Could not find a suitable
# TLS CA certificate bundle"），此时退回系统 CA 再试，避免整批商品读取失败。
_CA_FALLBACKS = (
    "/etc/ssl/cert.pem",
    "/usr/local/etc/openssl/cert.pem",
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/opt/homebrew/etc/ca-certificates/cert.pem",
    r"C:\Windows\system32\certmgr.msc",
)


def _find_ca() -> str:
    import os
    for _p in _CA_FALLBACKS:
        if os.path.exists(_p):
            return _p
    return ""

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
    "product_detail": "/api/smtProduct/edit.json",  # 编辑器加载（接口返回可能为 HTML，需兜底）
    "template_search": "/api/smtAdjustPrice/list.json",   # 实测：区域调价模板列表
    "attribute_template": "/api/smtAttributeTemplate/list.json",
    # ---- 图片空间清理（从相册页 chunk 逆向得到，ISSUES #5）----
    "album_list": "/api/album/list.json",                    # 图片/文件分页列表
    "album_file_counts": "/api/album/getFileTypeCounts.json",  # 图片/PDF 数量与占用
    "album_delete_ids": "/api/album/delPic.json",            # 按 ids（逗号分隔）删除
    "album_delete_by_filter": "/api/album/deleteBySearchCondition.json",  # 按筛选条件批量删除
    "album_auto_delete": "/api/album/createAutoDeleteTask.json",  # 定时清理未引用图片
    "album_auto_delete_latest": "/api/album/getLatestAutoDeleteTask.json",
    "album_auto_delete_cancel": "/api/album/cancelAutoDeleteTask.json",
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

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        """发请求；certifi 路径失效（TLS CA 报错）时退回系统 CA 再试一次。"""
        try:
            return getattr(self.session, method)(url, **kw)
        except Exception as ex:
            _m = str(ex).lower()
            if "tls ca certificate" not in _m and "certificate bundle" not in _m \
                    and "ssl" not in _m:
                raise
            _ca = _find_ca()
            if not _ca:
                raise
            self.session.verify = _ca
            return getattr(self.session, method)(url, **kw)

    def _get(self, key: str, params: dict | None = None) -> Any:
        headers = None
        if key == "product_detail" and params and params.get("id"):
            headers = {"Referer": f"https://www.dianxiaomi.com/web/smt/edit?id={params['id']}"}
        r = self._request("get", self._url(key), params=params, timeout=self.timeout, headers=headers)
        data = self._parse(r)
        if isinstance(data, dict) and str(data.get("code")) == "2005":
            import time as _t
            _t.sleep(2.5)
            r = self._request("get", self._url(key), params=params, timeout=self.timeout)
            data = self._parse(r)
        return data

    def _post(self, key: str, payload: dict | None = None) -> Any:
        r = self._request("post", self._url(key), data=payload or {}, timeout=self.timeout)
        data = self._parse(r)
        if isinstance(data, dict) and str(data.get("code")) == "2005":
            import time as _t
            _t.sleep(2.5)
            r = self._request("post", self._url(key), data=payload or {}, timeout=self.timeout)
            data = self._parse(r)
        return data

    def _post_json(self, key: str, payload: dict | None = None) -> Any:
        r = self._request("post", self._url(key), json=payload or {}, timeout=self.timeout)
        data = self._parse(r)
        if isinstance(data, dict) and str(data.get("code")) == "2005":
            import time as _t
            _t.sleep(2.5)
            r = self._request("post", self._url(key), json=payload or {}, timeout=self.timeout)
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
    def query_customs_metadata(self, product_id: str, shop_id: str, selected: list[dict]) -> dict:
        """Read current customs schema for an existing product. Does not save or publish."""
        if not product_id or not shop_id:
            raise ValueError("查询海关监管属性缺少商品或店铺标识")
        response = self._request("post", self.base + "/api/smtCommProduct/getGlobalHsCodeById.json",
            params={"shopId": str(shop_id), "id": str(product_id)},
            json={"selected_pv_list": selected}, timeout=self.timeout)
        parsed = self._parse(response)
        data = parsed.get("data") if isinstance(parsed, dict) and "data" in parsed else parsed
        if not isinstance(data, dict):
            raise DianxiaomiError("平台未返回海关监管属性数据")
        return data

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

    # ---------- 图片空间清理（ISSUES #5，接口自相册页 JS 逆向）----------
    def album_counts(self, name: str = "", start_time: str = "", end_time: str = "",
                     full_cid: str = "") -> dict:
        """图片/PDF 数量统计：{images, pdfs}（/api/album/getFileTypeCounts.json）。"""
        data = self._get("album_file_counts", {"name": name, "startTime": start_time,
                                               "endTime": end_time, "fullCid": full_cid})
        inner = data.get("data") if isinstance(data, dict) else None
        inner = inner if isinstance(inner, dict) else (data if isinstance(data, dict) else {})

        def _n(key):
            try:
                return int(inner.get(key) or 0)
            except Exception:
                return 0

        return {"images": _n("FILE_TYPE_IMAGE"), "pdfs": _n("FILE_TYPE_PDF")}

    def album_files(self, page: int = 1, size: int = 50, name: str = "",
                    start_time: str = "", end_time: str = "", full_cid: str = "",
                    file_type: int = 0) -> tuple[list[dict], dict]:
        """图片空间文件列表 → (items, meta)。字段：id/name/url/size/locked/created。"""
        params = {"name": name, "startTime": start_time, "endTime": end_time,
                  "fileType": file_type, "fullCid": full_cid,
                  "pageNo": page, "pageSize": size}
        data = self._get("album_list", params)
        inner = data.get("picListPage") or (data.get("data") or {}).get("picListPage") or {}
        items = []
        for it in inner.get("list") or []:
            if not isinstance(it, dict):
                continue
            items.append({
                "id": str(it.get("idStr") or it.get("id") or ""),
                "name": it.get("name") or "",
                "url": it.get("imgUrl") or it.get("url") or "",
                "size": it.get("imgCompressSize") or it.get("fileSize") or 0,
                "width": it.get("imgWidth") or 0,
                "height": it.get("imgHeight") or 0,
                "locked": str(it.get("lockState")) == "1",
                "created": it.get("createTime") or it.get("imgCreateTime") or "",
            })
        meta = {"total": int(inner.get("totalSize") or 0),
                "page": inner.get("pageNo") or page,
                "size": inner.get("pageSize") or size,
                "locked": int(data.get("lockedCount") or inner.get("lockedCount") or 0)}
        return items, meta

    def product_state(self, product_id: str) -> dict:
        """读取产品当前状态（用于编辑前预检，防止审核中产品被再次编辑）。"""
        d = self._get("product_detail", {"id": str(product_id)})
        p = (d.get("data") or {}).get("product") or {}
        return {"id": str(p.get("id") or product_id),
                "status": str(p.get("productStatusType") or ""),
                "err": str(p.get("errMsg") or ""),
                "audit_reason": str(p.get("auditFailureReason") or "")}

    def publish_failed_products(self, shop_id=None, limit: int = 0,
                                max_check: int = 250, workers: int = 6) -> list[dict]:
        """扫描「发布失败」商品。

        关键：列表接口的 dxmOfflineState 是过期的（永远 publishSuccess），
        不能直接用它判断。做法：
          1) 列表初筛：status 为空 / offline / 带 errMsg 的作为候选；
          2) 逐个查 product_detail，按 productStatusType / errMsg 判定；
          3) 只返回真正"发布失败"的。
        候选很多时只查前 max_check 个（默认 250），并用线程池并发，避免太慢。
        """
        # 快速路径：能直接按 publishFail 过滤到，就不用逐个查详情
        try:
            _it, _m = self.product_page(1, 100, shop_id, "publishFail")
            if _it:
                out = [{
                    "id": str(pr.get("id")),
                    "title": str(pr.get("title") or ""),
                    "error": str((pr.get("raw") or {}).get("errMsg") or "发布失败"),
                    "images": pr.get("images") or [],
                } for pr in _it]
                return out[:limit] if limit else out
        except Exception:
            pass

        # 1) 列表初筛
        candidates: list[dict] = []
        seen: set[str] = set()
        page = 1
        while page <= 10:
            items, meta = self.product_page(page, 100, shop_id, None)
            if not items:
                break
            for pr in items:
                pid = str(pr.get("id") or "")
                if not pid or pid in seen:
                    continue
                st = str(pr.get("status_type") or "")
                if st in ("onSelling", "auditing", "published", "online"):
                    continue
                raw = pr.get("raw") or {}
                err = str(raw.get("errMsg") or "")
                # 待发布草稿通常是 offline；有 errMsg 的一定要看
                if st in ("offline", "") or err:
                    seen.add(pid)
                    candidates.append(pr)
            total = int((meta or {}).get("total") or 0)
            if total and page * 100 >= total:
                break
            page += 1

        if not candidates:
            return []

        todo = candidates[:max_check]

        # 2) 逐个查详情确认
        def _check(pr: dict):
            pid = str(pr.get("id") or "")
            try:
                d = self._get("product_detail", {"id": pid})
                p = (d.get("data") or {}).get("product") or {}
                st = str(p.get("productStatusType") or "")
                err = str(p.get("errMsg") or p.get("auditFailureReason") or "")
                if st in ("onSelling", "auditing", "published", "online"):
                    return None
                if st == "publishFail" or err:
                    return {
                        "id": pid,
                        "title": str(pr.get("title") or p.get("subject") or ""),
                        "error": err or "发布失败",
                        "images": pr.get("images") or [],
                    }
            except Exception:
                return None
            return None

        rows: list[dict] = []
        try:
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=max(1, min(workers, 10))) as ex:
                for r in ex.map(_check, todo):
                    if r:
                        rows.append(r)
        except Exception:
            for pr in todo:
                r = _check(pr)
                if r:
                    rows.append(r)

        if limit:
            rows = rows[:limit]
        return rows

    def template_snapshot(self, product_id: str) -> dict:
        """从店小秘商品详情接口生成模板快照（不需要打开浏览器）。"""
        import json as _j
        d = self._get("product_detail", {"id": str(product_id)})
        p = (d.get("data") or {}).get("product") or {}
        if not p:
            raise DianxiaomiError("没取到商品详情")
        attrs = []
        try:
            raw = p.get("aeopAeProductPropertys") or "[]"
            for a in (_j.loads(raw) if isinstance(raw, str) else raw) or []:
                if not isinstance(a, dict):
                    continue
                name = str(a.get("attrName") or "").strip()
                val = str(a.get("attrValue") or "").strip()
                if name and val and val.upper() not in ("NONE", "NULL"):
                    attrs.append(f"{name}={val}")
        except Exception:
            attrs = []
        amap = {}
        for pair in attrs:
            if "=" in pair:
                k, v = pair.split("=", 1)
                amap[k.strip().lower()] = v.strip()
        fields = {
            "category": str(p.get("categoryId") or ""),
            "freight": str(p.get("freightTemplateId") or ""),
            "brand": amap.get("brand name") or amap.get("brand") or "",
            "origin": amap.get("origin") or amap.get("place of origin") or "",
            "weight": str(p.get("grossWeight") or p.get("addWeight") or ""),
            "size": "x".join(str(p.get(k) or "") for k in ("packageLength", "packageWidth", "packageHeight")),
        }
        values = {}
        if fields["brand"]:
            values["品牌"] = {"t": "text", "v": fields["brand"]}
        if fields["origin"]:
            values["产地"] = {"t": "text", "v": fields["origin"]}
        if amap.get("model number"):
            values["型号"] = {"t": "text", "v": amap["model number"]}
        if fields["weight"]:
            values["包装后重量"] = {"t": "text", "v": fields["weight"]}
        _sz = "x".join(str(p.get(k) or "") for k in ("packageLength", "packageWidth", "packageHeight"))
        if _sz.strip("x"):
            values["包装后尺寸"] = {"t": "text", "v": _sz}
        if p.get("productManual"):
            values["商品说明书"] = {"t": "file", "v": str(p.get("productManual"))}
        imgs = [x for x in str(p.get("imageURLs") or "").split(";") if x.strip().startswith("http")]
        return {"title": str(p.get("subject") or ""), "id": str(p.get("id") or product_id),
                "fields": fields, "attributes": attrs, "values": values, "images": imgs,
                "skus": p.get("smtProductSkuList") or p.get("aeopAeProductSKUs") or []}

    def album_delete_ids(self, ids: list[str] | str) -> Any:
        """按图片 id 批量删除（ids 逗号拼接；已锁定图片站点会跳过）。"""
        raw = ids if isinstance(ids, str) else ",".join(str(x) for x in ids if str(x).strip())
        if not raw:
            raise DianxiaomiError("没有要删除的图片")
        return self._post("album_delete_ids", {"ids": raw})

    def album_delete_before(self, days: int = 30, full_cid: str = "", name: str = "",
                            file_type: int = 0) -> Any:
        """批量删除「N 天前」上传的图片/文件（按时间筛选，已锁定不受影响）。"""
        import datetime as _dt
        days = max(0, int(days))
        end = (_dt.datetime.now() - _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        payload = {"name": name, "startTime": "2000-01-01 00:00:00", "endTime": end,
                   "fileType": int(file_type), "fullCid": full_cid}
        return self._post("album_delete_by_filter", payload)

    def album_auto_delete(self, days: int = 30) -> Any:
        """创建「删除未引用图片」任务：清理 N 天前上传且没有被商品引用的图片（最安全）。"""
        import datetime as _dt
        days = max(0, int(days))
        end = _dt.datetime.now() - _dt.timedelta(days=days)
        start = _dt.datetime(2000, 1, 1)
        return self._post_json("album_auto_delete", {"startTime": int(start.timestamp() * 1000),
                                                     "endTime": int(end.timestamp() * 1000)})

    def album_auto_delete_latest(self) -> dict:
        data = self._get("album_auto_delete_latest")
        inner = data.get("data") if isinstance(data, dict) else None
        return inner if isinstance(inner, dict) else {}

    def album_auto_delete_cancel(self, task_id) -> Any:
        return self._post_json("album_auto_delete_cancel", {"taskId": task_id})

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
        _st = it.get("productStatusType")
        if not _st:
            _dxm = str(it.get("dxmState") or "")
            if _dxm == "draft":
                _st = "draft"
            elif _dxm == "online":
                _st = "editingRequired"
            else:
                _st = ""
        return {
            "id": str(it.get("id") or it.get("productId") or ""),
            "title": it.get("subject") or it.get("title") or "",
            "product_id": it.get("productId"),
            "category_id": it.get("categoryId"),
            "shop_id": it.get("shopId"),
            "price": it.get("productPrice"),
            # 草稿商品的 productStatusType 是 None，但 dxmState=draft。
            # 不补这一条，run() 会把它判成"状态未知"直接跳过 —— 而草稿恰好是
            # 唯一不会被"审核中"拦住、真正能保存的那一类商品。
            "status": _st,
            "status_type": _st,
            "images": [str(x) for x in imgs if x],
            "image": (str(imgs[0]) if imgs else ""),
            "raw": it,
        }

    # 店小秘列表接口用 dxmState + dxmOfflineState 两个参数区分页面：
    #   draft                                  → 采集箱
    #   offline + dxmOfflineState=waitPublish  → 待发布
    #   offline + dxmOfflineState=offline      → 已下线
    #   offline + dxmOfflineState=publishFail  → 发布失败
    #   online                                 → 在线产品
    _STATE_PARAMS = {
        "draft": {"dxmState": "draft"},
        "editingRequired": {"dxmState": "offline", "dxmOfflineState": "waitPublish"},
        "waitPublish": {"dxmState": "offline", "dxmOfflineState": "waitPublish"},
        # 注意：offline + dxmOfflineState=offline 取不到数据（0 条），
        # 不带子状态才是「已下架」这一桶（实测 165 条）。
        "offline": {"dxmState": "offline"},
        "publishFail": {"dxmState": "offline", "dxmOfflineState": "publishFail"},
        "onSelling": {"dxmState": "online", "dxmOfflineState": "",
                      "productStatusType": "onSelling"},
        "online": {"dxmState": "online", "dxmOfflineState": ""},
        "auditing": {"dxmState": "online", "dxmOfflineState": "",
                     "productStatusType": "auditing"},
    }

    def product_page(self, page: int = 1, size: int = 100, shop_id=None,
                     status: str | None = None, keyword: str | None = None) -> tuple[list[dict], dict]:
        params: dict = {"pageNo": page, "pageSize": size, "total": 0,
                        "searchType": 0, "searchValue": ""}
        # 店小秘的 product_list 在带 dxmState 时必须给 shopId；不传时草稿/待发布
        # 返回 0 条（会表现为「列表里没有商品」）。-1 表示全店。
        if shop_id:
            params["shopId"] = shop_id
        else:
            params["shopId"] = -1
        if status:
            params.update(self._STATE_PARAMS.get(status, {"dxmState": status}))
        if keyword:
            params["searchValue"] = keyword
            params["searchType"] = 0
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

    def search_products(self, keyword: str, shop_id=None, status: str = "draft",
                        limit: int = 200) -> list[dict]:
        """按关键词找产品。

        店小秘的 searchType=0 只对部分字段生效（实测 "Concrete" 能命中、
        "Kerb" 命中不了），所以先走服务端搜索，命中为空再本地扫标题兜底。
        """
        kw = str(keyword or "").strip()
        if not kw:
            return self.pending_products(limit=limit, shop_id=shop_id)
        # 1) 服务端搜索
        try:
            params = {"pageNo": 1, "pageSize": min(limit, 100), "total": 0,
                      "searchType": 0, "searchValue": kw,
                      "shopId": shop_id or -1}
            params.update(self._STATE_PARAMS.get(status, {"dxmState": status}))
            data = self._post("product_list", params)
            po = ((data.get("data") or {}).get("page") or {})
            hits = [self._map_item(x) for x in (po.get("list") or [])]
            if hits:
                return hits[:limit]
        except Exception:
            pass
        # 2) 本地兜底：扫采集箱标题
        low = kw.lower()
        out = []
        for page in range(1, 6):
            items, meta = self.product_page(page, 100, shop_id, status)
            if not items:
                break
            for it in items:
                if low in str(it.get("title") or "").lower():
                    out.append(it)
                    if len(out) >= limit:
                        return out
            if len(items) < 100:
                break
        return out

    def batch_publish(self, ids: list[str]) -> dict:
        """批量发布（店小秘「批量发布」按钮用的接口）。

        只接受处于 采集箱/待发布/发布失败 的商品，其它会出现在 failList 里。
        返回 {success: [...], failed: [{id, msg}]}
        """
        ids = [str(x) for x in (ids or []) if str(x).strip()]
        if not ids:
            return {"success": [], "failed": []}
        try:
            r = self._request("post", self.base + "/api/smtProduct/batchPublish.json",
                              params={"ids": ",".join(ids)}, timeout=60)
            d = (r.json() or {}).get("data") or {}
            ok = [str((x or {}).get("id") or "") for x in (d.get("successList") or [])]
            bad = [{"id": str((x or {}).get("id") or ""),
                    "msg": str((x or {}).get("msg") or "")}
                   for x in (d.get("failList") or [])]
            return {"success": [x for x in ok if x], "failed": bad}
        except Exception as ex:  # noqa: BLE001
            return {"success": [], "failed": [{"id": "", "msg": str(ex)[:200]}]}

    def get_offline_counts(self, shop_id=None) -> dict:
        """一次请求拿全部状态数量（店小秘商品页顶部角标用的就是它）。

        返回 {draftNum, waitPublish, publishFail, publishing, publishingTiming}
        """
        try:
            r = self._request("post", self.base + "/api/smtProduct/getOfflineCounts.json",
                              params={"shopId": shop_id or -1}, timeout=15)
            d = (r.json() or {}).get("data") or {}
            return {k: int(v or 0) for k, v in d.items() if isinstance(v, (int, float, str))}
        except Exception:
            return {}

    def counts(self, shop_id=None, ttl: float = 0) -> dict:
        """各状态数量：优先用一次请求的 getOfflineCounts，失败才逐页探。

        ttl > 0 时结果缓存 ttl 秒，避免每次切页都打 3 个接口。
        """
        key = str(shop_id or "")
        cache = getattr(self, "_counts_cache", None)
        if cache is None:
            cache = {}
            self._counts_cache = cache
        if ttl and key in cache:
            import time as _t
            ts, val = cache[key]
            if _t.time() - ts < ttl:
                return dict(val)
        fast = self.get_offline_counts(shop_id)
        # 在线/审核中不在 getOfflineCounts 里：用 productStatusType 精确计数
        def _cnt(st: str) -> int:
            try:
                _, meta = self.product_page(1, 1, shop_id, st)
                return int(meta.get("total") or 0)
            except Exception:
                return 0
        if fast:
            out = {
                "draft": fast.get("draftNum", 0),
                "editingRequired": fast.get("waitPublish", 0),
                "publishFail": fast.get("publishFail", 0),
                "publishing": fast.get("publishing", 0),
                "publishingTiming": fast.get("publishingTiming", 0),
                "onSelling": _cnt("onSelling"),
                "auditing": _cnt("auditing"),
            }
            if ttl:
                import time as _t
                cache[key] = (_t.time(), dict(out))
            return out
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
        """可编辑产品：采集箱（offline）+ 草稿（draft）。

        店小秘的 add.json 只接受「采集箱」商品：
        - offline  = 采集箱（未上架）
        - draft    = 草稿（productStatusType 为 None）
        - editingRequired = 待发布（已移出采集箱，add.json 会拒）
        """
        items = self.iter_products(shop_id, "draft",
                                   max_items=(max(limit * 4, 80) if limit else 0))
        # 草稿优先：草稿没发布过，不会被速卖通的"审核中"挡住，是唯一能真正保存的一类。
        try:
            more = self.iter_products(shop_id, None,
                                      max_items=(max(limit * 6, 120) if limit else 120))
            drafts = [x for x in more if str(x.get("status_type")) == "draft"]
            if drafts:
                seen = {str(x.get("id")) for x in drafts}
                items = drafts + [x for x in items if str(x.get("id")) not in seen]
        except Exception:
            pass
        out = []
        for p in items:
            st = str(p.get("status_type") or p.get("status") or "")
            if st in self.BLOCKED_STATUS:
                continue
            if "PRODUCT_IN_AUDIT_PENDING" in str(p.get("raw", {}).get("errMsg") or ""):
                continue
            out.append(p)
        return out[:limit] if limit else out

    EDITABLE_STATUS = {"editingRequired", "offline", "publishFail", "draft", "waitPublish"}
    BLOCKED_STATUS = {"auditing", "onSelling", "published", "online"}

    def save_product_via_api(self, payload: dict) -> dict:
        """直接 API 保存商品（绕开浏览器 DOM）。

        真机逆向（2026-11-18）：add.json 的真实格式是
        multipart/form-data，字段 `file` 是一个 ZIP 压缩包，
        里面装 `smtEditSave.txt`，其内容为 JSON.stringify(payload)。
        """
        import io as _io
        import json as _j
        import zipfile as _zf

        txt = _j.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as zf:
            zf.writestr("smtEditSave.txt", txt)
        files = {"file": ("blob", buf.getvalue(), "application/zip")}
        r = self._request("post", self._url("product_save"), files=files,
                          timeout=self.timeout * 3)
        return self._parse(r)

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

    def upload_callback(self, file_id: str, file_name: str = "", file_size: int = 0,
                        full_cid: str = "", bucket: str = "wxalbum") -> dict:
        """COS 上传完成后的登记回调。

        cos_upload.upload_image() 会调它；以前只有端点没有方法，
        导致「直传相册」必然抛 AttributeError —— 现在补上。
        """
        payload = {"fileId": file_id, "fileName": file_name,
                   "fileSize": int(file_size or 0), "bucket": bucket or "wxalbum"}
        if full_cid:
            payload["fullCid"] = full_cid
        data = self._post("upload_callback", payload)
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

    def attribute_templates(self, shop_id=None, keyword: str = "", page: int = 1, size: int = 50) -> list[dict]:
        """速卖通属性模板列表（实测 /api/smtAttributeTemplate/list.json）。"""
        params = {"pageNo": page, "pageSize": size}
        if shop_id:
            params["shopId"] = shop_id
        if keyword:
            params["searchValue"] = keyword
        data = self._get("attribute_template", params)
        page_obj = ((data.get("data") or {}).get("page") or {})
        return page_obj.get("list") or []

    def video_callback(self, file_id: str, file_name: str, file_size: int) -> Any:
        return self._post("video_callback", {"fileId": file_id, "fileName": file_name, "fileSize": file_size})

    def cookies_dict(self) -> dict:
        return dict(self.session.cookies)


def from_browser_cookies(cookies: dict[str, str], overrides: dict | None = None) -> DianxiaomiClient:
    return DianxiaomiClient({k: v for k, v in cookies.items() if k in ("dxm_i", "dxm_t")} or cookies,
                            overrides=overrides)

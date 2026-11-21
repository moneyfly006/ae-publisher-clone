"""Amazon 适配器：对接店小秘（dianxiaomi.com）Amazon 私有 API。

已实测（2026-11-22，只读抓包 + 真实保存一次）：

- 登录：GET  ``/api/userIn.json``
- 店铺站点：POST ``/api/amazonProduct/getCountryByShopId.json``
- 列表：POST ``/api/amazonProduct/pageList.json``（``pageNo/pageSize/shopId/dxmState/searchValue``）
- 详情：GET  ``/api/amazonProductSp/editSp.json?id=``
- 类目属性：POST ``/api/amazonProductSp/getProductAttrById.json``（``id`` + ``site``=marketPlaceId）
- 类目树：POST ``/api/amazonProductSp/list.json``（``site``=站点代码, ``parentId``=内部 id，顶层 0）
- 按路径取类目：POST ``/api/amazonProductSp/getCategoryBySiteNodePath.json``
- 最近类目：POST ``/api/amazonProductSp/listHistory.json``（``shopId`` + ``site``=站点代码）
- 产品类型：POST ``/api/amazonProductSp/gerProductTypes.json``（``site``=站点代码）
- 预测类目：POST ``/api/amazonProductSp/productTypePredictCatesByItemName.json``（best-effort）
- 币种/物流/品牌：``/api/amazon/currency/list.json`` 等（``shopId`` + ``marketPlaceId``）
- 违禁词：POST ``/api/bannedWord/amazonCheckBannedWord.json``（仅提示）
- **保存（唯一写入口）**：POST ``/api/amazonProductSp/add.json``，
  multipart 字段 ``file`` = ZIP（内含 ``amazonSave.txt`` JSON UTF-8，filename=blob，
  Content-Type: application/zip）+ ``op``（1 保存 / 3 移入待发布 / 2 发布）。

参数陷阱（实测）：``gerProductTypes`` / ``listHistory`` / 类目 ``list`` /
``getCategoryBySiteNodePath`` / ``predict`` 用**站点代码**（``CA``），
而 ``getProductAttrById`` / ``currency`` / ``logistics`` / ``brand`` 用
**marketPlaceId**（``A2EUQ1WTGCTBG2``）；抓包里传错会返回空数组或 -1。
"""
from __future__ import annotations

import io
import json
import re
import zipfile

import requests

from ..services.amazon_prompts import SITE_META, site_meta as _site_meta

BASE = "https://www.dianxiaomi.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SAVE_NAME = "amazonSave.txt"

_LANG_NAMES = {
    "en": "英语", "es": "西班牙语", "pt": "葡萄牙语", "de": "德语", "fr": "法语",
    "it": "意大利语", "ja": "日语", "nl": "荷兰语", "sv": "瑞典语", "pl": "波兰语",
    "tr": "土耳其语", "ar": "阿拉伯语", "zh": "中文",
}
_LOGIN_HINTS = ("登录", "未登录", "login", "session", "重新登录", "会话")


def parse_cookie(text: str) -> dict:
    """``a=1; b=2`` → dict（忽略没有 ``=`` 的片段）。"""
    out: dict[str, str] = {}
    for part in str(text or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key, value = key.strip(), value.strip()
        if key:
            out[key] = value
    return out


class AmazonError(RuntimeError):
    """Amazon/店小秘接口错误（中文可读消息）。"""


class AmazonNotReady(AmazonError):
    """旧兼容：接口未接入/不可用（保留旧异常名给既有测试）。"""


def _num(value, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return default


def _lang_name(lang: str) -> str:
    return _LANG_NAMES.get(str(lang or "").lower(), "英语")


class AmazonClient:
    def __init__(self, cookies: str | dict, timeout: int = 30,
                 base: str = BASE):
        if isinstance(cookies, str):
            cookies = parse_cookie(cookies)
        elif cookies is None:
            cookies = {}
        self.timeout = timeout
        self.base = str(base or BASE).rstrip("/")
        self.s = requests.Session()
        for key, value in dict(cookies or {}).items():
            try:
                self.s.cookies.set(str(key), str(value), domain=".dianxiaomi.com")
            except Exception:
                self.s.cookies.set(str(key), str(value))
        self.s.headers.update({
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base}/web/amazon/pageList/draft",
        })

    # ---------------- 基础请求 ----------------
    def _url(self, path: str) -> str:
        path = str(path or "")
        if path.startswith("/api/") and self.base.endswith("/api"):
            return self.base + path[4:]
        return self.base + path

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 data: dict | None = None, files: dict | None = None,
                 headers: dict | None = None) -> dict:
        """发请求并把 HTTP/JSON/code 错误统一转成中文 ``AmazonError``。"""
        try:
            r = self.s.request(method, self._url(path), params=params, data=data,
                               files=files, headers=headers, timeout=self.timeout)
        except requests.Timeout as exc:
            raise AmazonError(f"请求超时（{self.timeout}s）：{path}，请检查网络或稍后重试") from exc
        except requests.RequestException as exc:
            raise AmazonError(f"网络请求失败：{str(exc)[:160]}") from exc

        if r.status_code in (401, 403):
            raise AmazonError("登录已失效（HTTP %s）：请重新粘贴店小秘 Cookie" % r.status_code)
        if r.status_code >= 400:
            raise AmazonError(f"接口 HTTP {r.status_code}：{r.text[:160]}")

        text = r.text or ""
        try:
            payload = r.json()
        except Exception as exc:
            low = text.lower()
            if "<html" in low or any(h in text for h in _LOGIN_HINTS):
                raise AmazonError("登录已失效或未登录：请重新粘贴店小秘 Cookie") from exc
            raise AmazonError(f"接口返回非 JSON（HTTP {r.status_code}）：{text[:160]}") from exc

        if not isinstance(payload, dict):
            raise AmazonError(f"接口返回格式异常：{str(payload)[:160]}")

        code = payload.get("code")
        if str(code) in ("2001", "401", "403"):
            raise AmazonError(str(payload.get("msg") or "登录已失效：请重新粘贴店小秘 Cookie"))
        if code not in (0, "0"):
            msg = str(payload.get("msg") or "").strip()
            low = (msg + " " + str(code)).lower()
            if any(h in msg for h in _LOGIN_HINTS) or "unauthorized" in low:
                raise AmazonError(msg or "登录已失效：请重新粘贴店小秘 Cookie")
            raise AmazonError(msg or f"接口错误 code={code}")
        return payload

    def _get(self, path: str, params: dict | None = None, **kw) -> dict:
        return self._request("GET", path, params=params, **kw)

    def _post(self, path: str, data: dict | None = None, **kw) -> dict:
        return self._request("POST", path, data=data or {}, **kw)

    # ---------------- 登录 / 店铺 / 站点 ----------------
    def user_in(self) -> dict:
        """GET /api/userIn.json 的 data（登录失效抛 AmazonError）。"""
        d = self._get("/api/userIn.json")
        data = d.get("data")
        return data if isinstance(data, dict) else {}

    def check_login(self) -> bool:
        try:
            d = self._get("/api/userIn.json")
            return bool(d.get("code") in (0, "0") and isinstance(d.get("data"), (dict, list)))
        except Exception:
            return False

    def shops(self) -> list[dict]:
        data = self.user_in()
        shop_map = data.get("shopMap") or {}
        out: list[dict] = []
        if isinstance(shop_map, dict):
            rows = list(shop_map.values())
        elif isinstance(shop_map, list):
            rows = shop_map
        else:
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            sid = row.get("id") or row.get("shopId")
            if sid in (None, ""):
                continue
            out.append({
                "id": str(sid),
                "name": str(row.get("name") or row.get("shopName") or ""),
                "merchant_id": str(row.get("merchantId") or ""),
                "default_marketplace": str(row.get("marketPlaceId") or ""),
                "platform": str(row.get("platform") or ""),
            })
        out.sort(key=lambda r: _num(r.get("id"), 0))
        return out

    def sites(self, shop_id: str) -> list[dict]:
        """店铺可用站点（strCountry 解析 + SITE_META 补全语言）。"""
        d = self._post("/api/amazonProduct/getCountryByShopId.json",
                       {"shopId": str(shop_id)})
        data = d.get("data") or {}
        if not isinstance(data, dict):
            return []
        str_country = str(data.get("strCountry") or "")
        lang_map = data.get("markeyPlaceLang") or {}
        out: list[dict] = []
        for seg in str_country.split("#"):
            parts = [p.strip() for p in seg.split(",")]
            if len(parts) < 5 or not parts[4]:
                continue
            country, _region, currency, code, mp = parts[:5]
            meta = SITE_META.get(mp) or {}
            lang_raw = str((lang_map or {}).get(mp) or "")
            lang = lang_raw.split("_")[0].lower() if lang_raw else str(meta.get("lang") or "en")
            out.append({
                "marketplace_id": mp,
                "code": code or str(meta.get("code") or ""),
                "country": country or str(meta.get("country") or ""),
                "currency": currency or str(meta.get("currency") or ""),
                "language": lang,
                "language_name": str(meta.get("lang_name") or _lang_name(lang)),
            })
        return out

    # ---------------- 商品 ----------------
    def list_products(self, state="draft", page=1, page_size=20,
                      shop_id="-1", keyword="") -> dict:
        """分页列表；state ∈ draft/offline/online。"""
        state = str(state or "draft").strip().lower()
        if state not in ("draft", "offline", "online"):
            raise AmazonError(f"未知列表状态：{state}（可选 draft/offline/online）")
        d = self._post("/api/amazonProduct/pageList.json", {
            "pageNo": int(page or 1),
            "pageSize": int(page_size or 20),
            "shopId": str(shop_id if shop_id is not None else "-1") or "-1",
            "dxmState": state,
            "searchValue": str(keyword or ""),
        })
        data = d.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        page_info = data.get("page") or {}
        if not isinstance(page_info, dict):
            page_info = {}
        rows = page_info.get("list") or data.get("list") or []
        left = data.get("leftStat") or {}
        if not isinstance(left, dict):
            left = {}
        markets = data.get("marketPids") or data.get("countryMap") or {}
        if not isinstance(markets, dict):
            markets = {}
        return {
            "items": [normalize_product(r) for r in rows if isinstance(r, dict)],
            "total": _num(page_info.get("totalSize") or data.get("totalSize"), 0),
            "page_no": _num(page_info.get("pageNo") or page, 1),
            "total_page": _num(page_info.get("totalPage"), 0),
            "drafts": _num(data.get("draftNum"), 0),
            "wait_publish": _num(left.get("waitPublish"), 0),
            "publish_fail": _num(left.get("publishFail"), 0),
            "markets": {str(k): str(v) for k, v in markets.items()},
        }

    def page_list(self, page: int = 1, page_size: int = 20, state: str = "draft",
                  shop_id: str = "-1", keyword: str = "") -> dict:
        """旧兼容：返回原 data（附 ``products`` 归一化列表）。"""
        d = self._post("/api/amazonProduct/pageList.json", {
            "pageNo": int(page or 1), "pageSize": int(page_size or 20),
            "shopId": str(shop_id), "dxmState": str(state), "searchValue": str(keyword or ""),
        })
        data = d.get("data") or {}
        if isinstance(data, dict):
            data["products"] = extract_list(data)
        return data if isinstance(data, dict) else {}

    def product_detail(self, product_id: str) -> dict:
        """editSp 的 data 原样（含 site/product/marketPids/...）；失败抛 AmazonError。"""
        d = self._get("/api/amazonProductSp/editSp.json", {"id": str(product_id)})
        data = d.get("data")
        if not isinstance(data, dict) or not data:
            raise AmazonError(f"读不到商品 {product_id}（接口返回空数据）")
        return data

    def product_attributes(self, product_id: str, marketplace_id: str) -> dict:
        """getProductAttrById：data 可能是 JSON 字符串；失败返回 {}。"""
        try:
            d = self._post("/api/amazonProductSp/getProductAttrById.json",
                           {"id": str(product_id), "site": str(marketplace_id)})
        except Exception:
            return {}
        data = d.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return {}
        return data if isinstance(data, dict) else {}

    # ---------------- 类目 ----------------
    def category_roots(self, site_code: str) -> list[dict]:
        return self.category_children(site_code, 0)

    def category_children(self, site_code: str, parent_id) -> list[dict]:
        """逐级类目；parentId 用内部 id（节点 id），顶层为 0。"""
        pid = parent_id if parent_id not in (None, "") else 0
        d = self._post("/api/amazonProductSp/list.json",
                       {"site": str(site_code), "parentId": str(pid)})
        data = d.get("data")
        if isinstance(data, dict):
            data = data.get("list") or data.get("categoryList") or []
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

    def category_by_path(self, site_code: str, node_path: str) -> dict:
        """按 nodePath 取类目；路径不属于该站点时抛 AmazonError。"""
        d = self._post("/api/amazonProductSp/getCategoryBySiteNodePath.json",
                       {"site": str(site_code), "nodePath": str(node_path)})
        data = d.get("data")
        return data if isinstance(data, dict) else {}

    def category_history(self, shop_id: str, site_code: str) -> list[dict]:
        d = self._post("/api/amazonProductSp/listHistory.json",
                       {"shopId": str(shop_id), "site": str(site_code)})
        data = d.get("data")
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

    def product_types(self, site_code: str) -> list[str]:
        d = self._post("/api/amazonProductSp/gerProductTypes.json",
                       {"site": str(site_code)})
        data = d.get("data")
        return [str(x) for x in data if str(x).strip()] if isinstance(data, list) else []

    def predict_category(self, shop_id: str, site_code: str, item_name: str) -> dict:
        """产品类型/类目预测（best-effort：失败返回 {}）。"""
        try:
            d = self._post("/api/amazonProductSp/productTypePredictCatesByItemName.json",
                           {"shopId": str(shop_id), "site": str(site_code),
                            "itemName": str(item_name or "")})
        except Exception:
            return {}
        data = d.get("data")
        return data if isinstance(data, dict) else {}

    # ---------------- 站点辅助（marketPlaceId 参数） ----------------
    def currency(self, shop_id: str, marketplace_id: str) -> str:
        try:
            d = self._post("/api/amazon/currency/list.json",
                           {"shopId": str(shop_id), "marketPlaceId": str(marketplace_id)})
        except Exception:
            return ""
        data = d.get("data")
        return str((data or {}).get("currency") or "") if isinstance(data, dict) else ""

    def logistics(self, shop_id: str, marketplace_id: str) -> list[dict]:
        try:
            d = self._post("/api/amazon/logistics/getLogisticsTemplates.json",
                           {"shopId": str(shop_id), "marketPlaceId": str(marketplace_id)})
        except Exception:
            return []
        data = d.get("data")
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

    def brands(self, shop_id: str, marketplace_id: str) -> list[dict]:
        try:
            d = self._post("/api/amazon/brand/getBrands.json",
                           {"shopId": str(shop_id), "marketPlaceId": str(marketplace_id)})
        except Exception:
            return []
        data = d.get("data")
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

    # ---------------- 违禁词（只读，失败不抛） ----------------
    def check_banned_words(self, title="", description="", bullet_point="",
                           search_terms="") -> list[str]:
        try:
            d = self._post("/api/bannedWord/amazonCheckBannedWord.json", {
                "title": str(title or ""),
                "description": str(description or ""),
                "bulletPoint": str(bullet_point or ""),
                "searchTerms": str(search_terms or ""),
            })
        except Exception:
            return []
        return _banned_hits(d.get("data"))

    # ---------------- 唯一写入口：ZIP + multipart ----------------
    @staticmethod
    def build_save_zip(payload: dict) -> bytes:
        """构造 ZIP（单条目 amazonSave.txt，UTF-8 紧凑 JSON）。"""
        raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(SAVE_NAME, raw.encode("utf-8"))
        return buf.getvalue()

    # 旧命名兼容
    zip_payload = build_save_zip

    def save_product(self, payload: dict, op: str = "1") -> dict:
        """POST /api/amazonProductSp/add.json（file=ZIP, op）；失败抛 AmazonError。"""
        if not isinstance(payload, dict) or not payload:
            raise AmazonError("保存包为空：拒绝调用保存接口")
        op = str(op or "1")
        if op not in ("1", "2", "3"):
            raise AmazonError(f"未知操作 op={op}（仅支持 1/2/3；v1 不做定时/变种特殊操作）")
        blob = self.build_save_zip(payload)
        headers = {"Referer": f"{self.base}/web/amazon/edit?id={payload.get('id') or ''}"}
        try:
            d = self._request(
                "POST", "/api/amazonProductSp/add.json",
                data={"op": op},
                files={"file": ("blob", blob, "application/zip")},
                headers=headers,
            )
        except AmazonError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AmazonError(f"保存请求失败：{str(exc)[:160]}") from exc
        # code=0 但 msg 是失败语义（店小秘老毛病）：按失败处理
        msg = str(((d.get("data") or {}) if isinstance(d.get("data"), dict) else {}).get("msg")
                  or d.get("msg") or "")
        if msg:
            try:
                from ..services.publisher import is_failure_msg
                if is_failure_msg(msg):
                    raise AmazonError(msg[:300])
            except AmazonError:
                raise
            except Exception:
                pass
        return d

    # 旧命名兼容
    def save(self, payload: dict, op: str = "1") -> dict:
        return self.save_product(payload, op)


def _banned_hits(raw) -> list[str]:
    """兼容违禁词接口多种返回形状，提取命中的词（失败/空 → []）。"""
    if raw in (None, "", 0, "0", {}, []):
        return []
    hits: list[str] = []

    def walk(node, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(node, str):
            s = node.strip()
            if s and s.lower() not in ("none", "null", "false", "true", "ok", "success"):
                hits.append(s)
        elif isinstance(node, dict):
            for key in ("word", "words", "bannedWord", "bannedWords", "banned_word",
                        "bannedWordList", "list", "result", "data", "name"):
                if key in node:
                    walk(node[key], depth + 1)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, depth + 1)

    walk(raw)
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:20]


def normalize_product(row: dict) -> dict:
    """把店小秘 amazon 列表行整理成统一结构（保留旧键：id/title/sku/asin/price/...）。"""
    if not isinstance(row, dict):
        return {}
    sale = row.get("saleSalePrice")
    std = row.get("standardPrice")
    price = ""
    for candidate in (sale, std, row.get("price"), row.get("salePrice")):
        try:
            if candidate not in (None, "") and float(candidate) > 0:
                price = str(candidate)
                break
        except Exception:
            continue
    if not price:
        price = "0"
    return {
        "id": str(row.get("id") or row.get("productId") or row.get("idStr") or ""),
        "title": str(row.get("title") or row.get("subject") or row.get("productName") or ""),
        "sku": str(row.get("sku") or row.get("sellerSku") or ""),
        "asin": str(row.get("asin") or row.get("productId") or ""),
        "price": price,
        "stock": row.get("quantity") if row.get("quantity") is not None
                 else (row.get("stock") or 0),
        "status": str(row.get("dxmState") or row.get("status") or ""),
        "marketplace": str(row.get("marketPlaceId") or row.get("marketPlace")
                           or row.get("marketplace") or ""),
        "marketplace_id": str(row.get("marketPlaceId") or ""),
        "currency": str(row.get("standardPriceCurrency") or ""),
        "category_id": str(row.get("categoryId") or ""),
        "category_path": str(row.get("categoryBrowsePath") or ""),
        "main_image": str(row.get("mainImage") or ""),
        "extra_images": row.get("extraImages") if isinstance(row.get("extraImages"), list) else [],
        "err_msg": str(row.get("errMsg") or ""),
        "warning_msg": str(row.get("warningMsg") or ""),
        "import_warning_msg": str(row.get("importWarningMsg") or ""),
        "shop_id": str(row.get("shopId") or ""),
        "is_variation": _num(row.get("isVariation"), 0),
        "update_time": row.get("updateTime") or row.get("gmtModified") or "",
    }


def extract_list(data: dict) -> list[dict]:
    """兼容 data.page.list / data.list 两种返回。"""
    page = (data or {}).get("page") or {}
    rows = page.get("list") or (data or {}).get("list") or []
    return [normalize_product(r) for r in rows if isinstance(r, dict)]


__all__ = [
    "AmazonClient", "AmazonError", "AmazonNotReady", "BASE", "UA", "SAVE_NAME",
    "parse_cookie", "normalize_product", "extract_list",
]

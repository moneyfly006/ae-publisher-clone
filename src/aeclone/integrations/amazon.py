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
import time
import zipfile

import requests

from ..services.amazon_prompts import SITE_META, site_meta as _site_meta

BASE = "https://www.dianxiaomi.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SAVE_NAME = "amazonSave.txt"

# 列表状态：draft/offline/online 走 pageList.json；待发布三态走 offline.json
_OFFLINE_SUBSTATES = {
    "waitpublish": "waitPublish",
    "publishing": "publishing",
    "publishfail": "publishFail",
}
_LIST_STATE_ALIASES = {
    "draft": ("pageList", "draft", None),
    "offline": ("pageList", "offline", None),
    "online": ("pageList", "online", None),
    "waitpublish": ("offline", "offline", "waitPublish"),
    "publishing": ("offline", "offline", "publishing"),
    "publishfail": ("offline", "offline", "publishFail"),
}
# errMsg「…,属性:model_number;…」提取必填属性名
_REQUIRED_ATTR_RE = re.compile(r"(?:属性|attribute)\s*[:：]\s*([A-Za-z0-9_]+)", re.I)

_LANG_NAMES = {
    "en": "英语", "es": "西班牙语", "pt": "葡萄牙语", "de": "德语", "fr": "法语",
    "it": "意大利语", "ja": "日语", "nl": "荷兰语", "sv": "瑞典语", "pl": "波兰语",
    "tr": "土耳其语", "ar": "阿拉伯语", "zh": "中文",
}
_LOGIN_HINTS = ("登录", "未登录", "login", "session", "重新登录", "会话")

# 店小秘「瞬时繁忙」重试（只读接口专用；写接口绝不自动重试，避免重复写入）
RETRY_MAX_ATTEMPTS = 3                 # 最多 3 次尝试（首次 + 2 次重试）
RETRY_BACKOFF = (1.5, 3.0)             # 第 1 次重试等 1.5s，第 2 次等 3s
RETRY_BUSY_CODE = 2005                 # 实测「系统繁忙,请稍后重试!!!」返回 code=2005
RETRY_BUSY_HINTS = ("系统繁忙", "稍后重试", "请稍后再试", "请求过于频繁", "too many")
# 写接口路径：命中时强制 retry=False（保存重试可能造成重复写入）
WRITE_PATH_HINTS = ("/add.json",)


class _Retryable(Exception):
    """内部信号：本次失败可重试（最终对外仍抛 AmazonError）。"""


def parse_cookie(text) -> dict:
    """``a=1; b=2`` → dict；兼容 ``Cookie:`` 前缀/换行/JSON（见 cookie_parse）。"""
    from .cookie_parse import parse_cookie_text
    return parse_cookie_text(text)


def parse_required_attrs(err_msg) -> list[str]:
    """从发布失败 errMsg 提取 ``属性:xxx`` 的必填属性名（去重保序）。

    实测样例：``发布失败:code:90220,“型号” 为必填项，但缺失。,属性:model_number;...``
    """
    text = str(err_msg or "")
    out: list[str] = []
    for match in _REQUIRED_ATTR_RE.finditer(text):
        name = str(match.group(1) or "").strip()
        if name and name not in out:
            out.append(name)
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
        self._attr_template_cache: dict = {}
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
                 headers: dict | None = None, retry: bool = True) -> dict:
        """发请求并把 HTTP/JSON/code 错误统一转成中文 ``AmazonError``。

        只读接口对「瞬时繁忙/网络抖动」做有界重试（最多 3 次尝试，backoff 1.5s/3s）；
        登录失效（401/403/2001）、其它业务错误、HTTP 4xx 不重试。
        **写接口（含 ``/add.json``）永不自动重试**（保存重试可能重复写入）。
        """
        if any(hint in str(path or "") for hint in WRITE_PATH_HINTS):
            retry = False
        attempts = (min(3, max(0, int(getattr(self, "read_retries", RETRY_MAX_ATTEMPTS - 1)))) + 1) if retry else 1
        last_error = ""
        for attempt in range(1, attempts + 1):
            try:
                return self._request_once(method, path, params=params, data=data,
                                          files=files, headers=headers)
            except _Retryable as exc:
                last_error = str(exc)
                if attempt >= attempts:
                    break
                delay = RETRY_BACKOFF[min(attempt - 1, len(RETRY_BACKOFF) - 1)]
                time.sleep(max(0.0, float(delay)))
        raise AmazonError(last_error or "请求失败：请稍后重试")

    def _request_once(self, method: str, path: str, *, params: dict | None = None,
                      data: dict | None = None, files: dict | None = None,
                      headers: dict | None = None) -> dict:
        try:
            r = self.s.request(method, self._url(path), params=params, data=data,
                               files=files, headers=headers, timeout=self.timeout)
        except requests.Timeout as exc:
            raise _Retryable(
                f"请求超时（{self.timeout}s）：{path}，请检查网络或稍后重试") from exc
        except requests.ConnectionError as exc:
            raise _Retryable(f"网络连接失败：{str(exc)[:160]}") from exc
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
            # 店小秘「系统繁忙」类瞬时错误：读接口可重试，写接口由外层 retry=False 拦住
            busy = (str(code) == str(RETRY_BUSY_CODE)
                    or any(h in msg or h in low for h in RETRY_BUSY_HINTS))
            if busy:
                raise _Retryable(msg or f"系统繁忙，请稍后重试（code={code}）")
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
        """分页列表。

        state ∈ ``draft`` / ``offline`` / ``online``（pageList.json）或
        ``waitPublish`` / ``publishing`` / ``publishFail``（offline.json，
        ``dxmState=offline`` + ``dxmOfflineState=<state>``）。
        """
        raw_state = str(state or "draft").strip().lower().replace("_", "").replace("-", "")
        parsed = _LIST_STATE_ALIASES.get(raw_state)
        if parsed is None:
            raise AmazonError(
                f"未知列表状态：{state}（可选 draft/offline/online/"
                f"waitPublish/publishing/publishFail）")
        api, dxm_state, offline_state = parsed
        payload = {
            "pageNo": int(page or 1),
            "pageSize": int(page_size or 20),
            "shopId": str(shop_id if shop_id is not None else "-1") or "-1",
            "dxmState": dxm_state,
            "searchValue": str(keyword or ""),
        }
        if offline_state:
            payload["dxmOfflineState"] = offline_state
            d = self._post("/api/amazonProduct/offline.json", payload)
        else:
            d = self._post("/api/amazonProduct/pageList.json", payload)
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
            "publishing": _num(left.get("publishing"), 0),
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

    def publish_state(self, product_id: str, shop_id: str = "-1") -> dict:
        """只读核实商品当前所在列表状态（draft/online/待发布三态）。

        返回 ``{"state","err_msg","asin","title","counts"}``；``state`` 取值：
        ``draft|online|waitPublish|publishing|publishFail|unknown``。
        找不到/接口失败返回 ``unknown``（``counts`` 尽量填、``error`` 记原因）。
        """
        pid = str(product_id or "").strip()
        counts = {"draft": 0, "online": 0, "waitPublish": 0, "publishing": 0,
                  "publishFail": 0}
        errors: list[str] = []

        def _find(items):
            for row in items or []:
                if str(row.get("id") or "") == pid:
                    return row
            return None

        def _left(data: dict) -> None:
            for key in ("waitPublish", "publishFail", "publishing"):
                if data.get(key) is not None:
                    counts[key] = _num(data.get(key), counts.get(key, 0))

        def _hit(state: str, row: dict) -> dict:
            return {
                "state": state,
                "err_msg": str(row.get("err_msg") or ""),
                "asin": str(row.get("asin") or ""),
                "title": str(row.get("title") or ""),
                "counts": dict(counts),
            }

        for state in ("draft", "online"):
            try:
                data = self.list_products(state, page=1, page_size=200,
                                          shop_id=shop_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{state}: {str(exc)[:120]}")
                continue
            counts[state] = _num(data.get("total"), 0)
            _left({"waitPublish": data.get("wait_publish"),
                   "publishFail": data.get("publish_fail"),
                   "publishing": data.get("publishing")})
            row = _find(data.get("items"))
            if row:
                return _hit(state, row)

        for state in ("waitPublish", "publishing", "publishFail"):
            try:
                data = self.list_products(state, page=1, page_size=200,
                                          shop_id=shop_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{state}: {str(exc)[:120]}")
                continue
            counts[state] = _num(data.get("total"), 0)
            _left({"waitPublish": data.get("wait_publish"),
                   "publishFail": data.get("publish_fail"),
                   "publishing": data.get("publishing")})
            row = _find(data.get("items"))
            if row:
                return _hit(state, row)

        out = {"state": "unknown", "err_msg": "", "asin": "", "title": "",
               "counts": dict(counts)}
        if errors:
            out["error"] = "；".join(errors)[:400]
        return out

    def online_rows(self, shop_id: str = "-1", max_pages: int = 5,
                    page_size: int = 200) -> list[dict]:
        """只读：拉在线商品列表（默认最多 5 页 ×200，够覆盖常见店铺）。"""
        rows: list[dict] = []
        for page in range(1, max(1, int(max_pages)) + 1):
            data = self.list_products("online", page=page, page_size=page_size,
                                      shop_id=shop_id)
            items = data.get("items") or []
            rows.extend(items)
            total_page = _num(data.get("total_page"), 1)
            if not items or page >= total_page:
                break
        return rows

    def attribute_template(self, category_id: str, marketplace_id: str,
                           shop_id: str = "-1") -> dict:
        """取「同站点、同类目」在线商品的 productAttr 作为属性模板（只读）。

        找不到返回 {}；同 (shop, site, category) 进程内只查一次（命中/未命中都缓存）。
        """
        key = (str(shop_id), str(marketplace_id), str(category_id))
        cache = self._attr_template_cache
        if key in cache:
            return dict(cache[key])
        result: dict = {}
        try:
            for row in self.online_rows(shop_id):
                if str(row.get("category_id") or "") != str(category_id):
                    continue
                row_mp = str(row.get("marketplace_id") or "")
                if marketplace_id and row_mp and row_mp != str(marketplace_id):
                    continue
                try:
                    detail = self.product_detail(str(row.get("id") or ""))
                except Exception:
                    continue
                product = detail.get("product") if isinstance(detail.get("product"), dict) else detail
                raw_attr = (product or {}).get("productAttr")
                data = raw_attr
                if isinstance(raw_attr, str):
                    try:
                        data = json.loads(raw_attr)
                    except Exception:
                        data = None
                if isinstance(data, dict) and data:
                    result = data
                    break
        except Exception:
            result = {}
        cache[key] = dict(result)
        return dict(result)

    def attr_js_url(self, site_code: str, product_type: str,
                    category_id: str) -> str:
        """只读：getAttrJsUrl（类目内部 id + productType + 站点代码）→ schema JS URL。"""
        d = self._post("/api/amazonProductSp/getAttrJsUrl.json", {
            "site": str(site_code), "productType": str(product_type),
            "id": str(category_id)})
        data = d.get("data")
        return data if isinstance(data, str) else ""

    def required_attributes_from_schema(self, category_id: str, product_type: str,
                                        site_code: str) -> list[str]:
        """best-effort：下载类目 schema JS，正则提取 ``required:!0`` 的顶层属性名。

        失败（接口/下载/正则）一律返回 []，不阻断保存/发布。
        """
        try:
            url = self.attr_js_url(site_code, product_type, category_id)
            if not url:
                return []
            r = requests.get(url, timeout=self.timeout)
            if r.status_code >= 400:
                return []
            text = r.text or ""
        except Exception:
            return []
        out: list[str] = []
        pattern = re.compile(
            r'name\s*:\s*["\']([a-zA-Z0-9_]+)["\'][\s\S]{0,300}?'
            r'required\s*:\s*(!0|!1|true|false)')
        for match in pattern.finditer(text):
            name, flag = match.group(1), match.group(2)
            if flag in ("!0", "true") and name not in out:
                out.append(name)
        return out[:200]

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

    def search_categories(self, site_code: str, keyword: str) -> list[dict]:
        """按关键词搜索 Amazon 类目；返回包含 nodePathId 的候选项。"""
        query = str(keyword or "").strip()
        if not query:
            return []
        d = self._post("/api/amazonProductSp/searchCategory.json",
                       {"site": str(site_code), "category": query})
        data = d.get("data") if isinstance(d, dict) else d
        if isinstance(data, dict):
            data = data.get("list") or data.get("categoryList") or []
        return [n for n in data if isinstance(n, dict)] if isinstance(data, list) else []

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
            # 写接口禁止自动重试：保存重试可能造成重复写入（path 命中也会强制 retry=False）
            d = self._request(
                "POST", "/api/amazonProductSp/add.json",
                data={"op": op},
                files={"file": ("blob", blob, "application/zip")},
                headers=headers,
                retry=False,
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
                    if "产品不存在" in msg or "商品不存在" in msg:
                        raise AmazonError(
                            f"商品已不在采集箱（可能被删除、合并或移出当前店铺），"
                            f"请重新加载商品后再试：{payload.get('id') or ''}；{msg[:200]}")
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


def is_node_path(value: str) -> bool:
    """Amazon nodePath 必须是数字节点 ID 路径，不能是展示用类目名称。"""
    text = str(value or "").strip()
    if not text:
        return False
    parts = [x.strip() for x in text.replace(">", "/").split("/") if x.strip()]
    return bool(parts) and all(x.isdigit() for x in parts)


def category_node_path(node: dict) -> str:
    """从类目节点取可校验的 nodePath；取不到返回空串。"""
    node = node or {}
    for key in ("nodePathId", "nodePath", "node_path", "path"):
        value = str(node.get(key) or "").strip()
        if is_node_path(value):
            return value
    return ""


def is_leaf_category(node: dict) -> bool:
    flag = str(node.get("leafCategory", "")).strip().casefold()
    return bool(node) and flag not in ("false", "0") and not node.get("childIds")


def product_scope_issue(product: dict, shop_id: str, marketplace_id: str) -> str:
    shop = str(product.get("shopIdStr") or product.get("shopId") or product.get("shop_id") or "")
    market = str(product.get("marketPlaceId") or product.get("marketplace_id") or "")
    if shop_id not in ("", "0", "-1") and shop not in ("", "0", "-1", shop_id):
        return "商品属于另一家 Amazon 店铺"
    if marketplace_id and market not in ("", "0", "-1", marketplace_id):
        return "商品属于另一 Amazon 站点"
    return ""


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
        "id": str(row.get("idStr") or row.get("id") or row.get("productId") or ""),
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
        "shop_id": str(row.get("shopIdStr") or row.get("shopId") or ""),
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
    "parse_cookie", "normalize_product", "extract_list", "parse_required_attrs",
]

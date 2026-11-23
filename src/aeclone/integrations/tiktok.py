"""TikTok Shop 适配器：对接店小秘（dianxiaomi.com）TikTok 私有 API。

已实测（2026-11-22，只读抓包 + 空 payload 传输自检）：

- 登录：GET  ``/api/userIn.json``
- 站点：GET  ``/api/tiktokProduct/getL7ShopIdAndSite.json`` → 23 个 L7 站点 + ``ALL``
- 列表：POST ``/api/tiktokProduct/pageList.json``
  （``pageNo/pageSize/shopId/dxmState/searchValue``，可选 ``site/categoryId``）
- 计数：POST ``/api/tiktokProduct/getOfflineCounts.json`` / ``getOnlineCounts.json``
- 详情：POST ``/api/tiktokProduct/gettiktokProduct.json``（``idStr``），
  失败回退 ``getByProductId.json``（``productId``）；无商品时 data=null
- 变种：POST ``/api/tiktokProduct/getVariantByProductId.json``（``id``）
- 类目树：POST ``/api/tiktokCategory/list.json``
  （``shopId``、``site``、``crossBorder=1``、``categoryParentId``；顶层 ``"0"``，
  下钻用父节点 **categoryId**，不是内部 id）
- 类目搜索：POST ``/api/tiktokCategory/searchCategory.json``（关键词字段名 **category**）
- 类目属性：POST ``/api/tiktokCategory/attributeList.json``（必须带 ``type=3``，
  否则只返回 1 条基础属性）
- 最近类目：POST ``/api/tiktokCategory/historyList.json``（``getHistoryCategory``
  实测返回空 body，故只作为兜底）
- 品牌：``listCategoryBrands.json``（best-effort，实测 500 → 返回 []）
- 站点限额：POST ``/api/tiktokProduct/getLimitBySite.json``（``site``）
- 仓库：POST ``/api/tiktokProduct/listWarehouse.json``（``shopId``，无店铺返回 []）
- 违禁词：POST ``/api/bannedWord/singleCheckForBannedWord.json``（仅提示，失败 []）
- **保存（唯一写入口）**：POST multipart ``/api/tiktokProduct/add.json``，
  ``file`` = ZIP（内含 ``tiktokSave.txt`` UTF-8 JSON）+ ``op``（+ 可选 ``saveMode``）。
  空 payload 实测返回 ``code=-1``「产品未选择店铺或店铺无效！」：传输格式正确，
  但本账号未绑定 TikTok 店铺，任何真实保存都会被服务端拒绝。

安全边界：
- 只有 :meth:`TikTokClient.save_product` / :meth:`publish_products` /
  :meth:`delete_products` 会调用写接口；写接口**永不自动重试**（避免重复写入）。
- 未绑定店铺的失败统一转成 :class:`TikTokNotReady`（中文可操作提示）。
- Cookie 只进内存，日志里不得打印全文。
"""
from __future__ import annotations

import copy
import io
import json
import time
import zipfile

import requests

BASE = "https://www.dianxiaomi.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SAVE_NAME = "tiktokSave.txt"

# op 语义（前端按钮逆向，契约 §1.5）
OP_SAVE = "1"                 # 保存（留在采集箱）
OP_PUBLISH = "2"              # 发布（在线商品为更新）
OP_MOVE_PENDING = "3"         # 保存并移入待发布
OP_SCHEDULE = "5"             # 定时发布（v1 不做，保留常量）
SAVE_MODE_AS_DRAFT = "AS_DRAFT"
VALID_OPS = (OP_SAVE, OP_PUBLISH, OP_MOVE_PENDING, OP_SCHEDULE)

# L7 站点（契约 §0/§1.1 实测顺序；端点不可用时回退）
L7_SITES = [
    "TH", "VN", "MY", "PH", "SG", "ID", "GB", "DE", "ES", "IT", "FR", "IE",
    "PL", "NL", "BE", "CZ", "AT", "GR", "PT", "HU", "JP", "BR", "MX",
]
L7_SHOP_ID = "ALL"

# 站点 → 国家 / 币种 / 语言（契约 §2；与 services/tiktok_prompts.SITE_META 对齐）
SITE_META: dict[str, dict] = {
    "US": {"country": "美国", "currency": "USD", "language": "en", "language_name": "英语"},
    "GB": {"country": "英国", "currency": "GBP", "language": "en", "language_name": "英语"},
    "IE": {"country": "爱尔兰", "currency": "EUR", "language": "en", "language_name": "英语"},
    "DE": {"country": "德国", "currency": "EUR", "language": "de", "language_name": "德语"},
    "AT": {"country": "奥地利", "currency": "EUR", "language": "de", "language_name": "德语"},
    "FR": {"country": "法国", "currency": "EUR", "language": "fr", "language_name": "法语"},
    "BE": {"country": "比利时", "currency": "EUR", "language": "nl", "language_name": "荷兰语"},
    "NL": {"country": "荷兰", "currency": "EUR", "language": "nl", "language_name": "荷兰语"},
    "ES": {"country": "西班牙", "currency": "EUR", "language": "es", "language_name": "西班牙语"},
    "IT": {"country": "意大利", "currency": "EUR", "language": "it", "language_name": "意大利语"},
    "PT": {"country": "葡萄牙", "currency": "EUR", "language": "pt", "language_name": "葡萄牙语"},
    "PL": {"country": "波兰", "currency": "EUR", "language": "pl", "language_name": "波兰语"},
    "CZ": {"country": "捷克", "currency": "CZK", "language": "cs", "language_name": "捷克语"},
    "GR": {"country": "希腊", "currency": "EUR", "language": "el", "language_name": "希腊语"},
    "HU": {"country": "匈牙利", "currency": "EUR", "language": "hu", "language_name": "匈牙利语"},
    "JP": {"country": "日本", "currency": "JPY", "language": "ja", "language_name": "日语"},
    "TH": {"country": "泰国", "currency": "THB", "language": "th", "language_name": "泰语"},
    "VN": {"country": "越南", "currency": "VND", "language": "vi", "language_name": "越南语"},
    "MY": {"country": "马来西亚", "currency": "MYR", "language": "ms", "language_name": "马来语"},
    "SG": {"country": "新加坡", "currency": "SGD", "language": "en", "language_name": "英语"},
    "PH": {"country": "菲律宾", "currency": "PHP", "language": "en", "language_name": "英语"},
    "ID": {"country": "印尼", "currency": "IDR", "language": "id", "language_name": "印尼语"},
    "BR": {"country": "巴西", "currency": "BRL", "language": "pt", "language_name": "葡萄牙语"},
    "MX": {"country": "墨西哥", "currency": "MXN", "language": "es", "language_name": "西班牙语"},
}


def _merge_prompt_site_meta() -> None:
    """与 ``services/tiktok_prompts.SITE_META`` 对齐（存在时），避免两处漂移。

    prompts 的键名是契约 §2 的 ``lang``/``lang_name``，客户端内部统一用
    ``language``/``language_name``（契约 §3.1 的 sites() 字段）。导入失败时
    保留上面的内置值（保证客户端可独立导入/单测）。
    """
    try:
        from ..services.tiktok_prompts import SITE_META as prompt_meta
    except Exception:
        return
    if not isinstance(prompt_meta, dict):
        return
    for code, row in prompt_meta.items():
        if code not in SITE_META or not isinstance(row, dict):
            continue
        target = SITE_META[code]
        target["country"] = _str(row.get("country")) or target["country"]
        target["currency"] = _str(row.get("currency")) or target["currency"]
        lang = _str(row.get("language") or row.get("lang"))
        lang_name = _str(row.get("language_name") or row.get("lang_name"))
        target["language"] = lang or target["language"]
        target["language_name"] = lang_name or target["language_name"]


# 站点限额内置默认（契约 §1.6 实测值；接口失败时使用）
DEFAULT_LIMITS: dict = {
    "priceMin": 0.01,
    "priceMax": 180000000.0,
    "stockMax": 99999,
    "nameMax": 255,
    "nameMin": 25,
    "descMax": 10000,
    "descImgMax": 30,
    "imgCountMax": 9,
    "imgCountMin": 1,
    "packageMin": 1000,
    "packageMid": 1000,
    "packageMax": 1000,
    "variationSizeMax": 100,
    "variationNameMax": 20,
    "variationOptionMax": 50,
}

_LOGIN_HINTS = ("登录", "未登录", "login", "session", "重新登录", "会话", "unauthorized")

# 店小秘「瞬时繁忙」重试（只读接口专用；写接口绝不自动重试，避免重复写入）
RETRY_MAX_ATTEMPTS = 3                 # 最多 3 次尝试（首次 + 2 次重试）
RETRY_BACKOFF = (1.5, 3.0)             # 第 1 次重试等 1.5s，第 2 次等 3s
RETRY_BUSY_CODE = 2005                 # 实测「系统繁忙,请稍后重试!!!」返回 code=2005
RETRY_BUSY_HINTS = ("系统繁忙", "稍后重试", "请稍后再试", "请求过于频繁", "too many")

# 写接口路径（命中时强制 retry=False；保存重试可能造成重复写入）
WRITE_PATH_HINTS = (
    "/add.json",
    "/batchpublish",
    "/batchsetpublish",
    "/batchproductdelete",
    "/updatepriceorstock",
    "/syncproductbyid",
)


class TikTokError(RuntimeError):
    """TikTok/店小秘接口错误（中文可读消息）。"""


class TikTokNotReady(TikTokError):
    """未绑定/未选择 TikTok 店铺（需要用户先在店小秘授权 TikTok Shop）。"""


class _Retryable(Exception):
    """内部信号：本次失败可重试（最终对外仍抛 TikTokError）。"""


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


def zip_payload(payload: dict) -> bytes:
    """构造保存 ZIP：单条目 ``tiktokSave.txt``，UTF-8 紧凑 JSON。"""
    raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(SAVE_NAME, raw.encode("utf-8"))
    return buf.getvalue()


def _num(value, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return default


def _str(value) -> str:
    if value is None:
        return ""
    return str(value)


# 与 prompts 的 SITE_META 对齐（必须在 _str 定义之后执行）
_merge_prompt_site_meta()


def _first(*values) -> str:
    """返回第一个非空值（转字符串）。"""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value.strip()
            continue
        if value in ("", [], {}):
            continue
        return str(value)
    return ""


def _as_dict(node) -> dict:
    return node if isinstance(node, dict) else {}


def _as_list(node) -> list:
    if isinstance(node, list):
        return node
    if isinstance(node, tuple):
        return list(node)
    return []


def _json_maybe(node):
    """把可能是 JSON 字符串的字段解析成对象；失败原样返回。"""
    if isinstance(node, str):
        text = node.strip()
        if text[:1] in ("[", "{"):
            try:
                return json.loads(text)
            except Exception:
                return node
    return node


def _image_list(images) -> list[str]:
    """兼容 images 为字符串列表或 dict 列表。"""
    out: list[str] = []
    for item in _as_list(images):
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            url = _first(item.get("url"), item.get("imgUrl"), item.get("imageUrl"),
                         item.get("src"))
            if url:
                out.append(url)
    return out


def normalize_product(row: dict) -> dict:
    """把 TikTok 列表行整理成统一结构（防御式：未知/缺失字段不崩）。"""
    if not isinstance(row, dict):
        return {}
    images = _image_list(row.get("images"))
    image = _first(row.get("imgUrl"), row.get("mainImage"), row.get("image"),
                   images[0] if images else "")
    if not images and image:
        images = [image]
    price = _first(row.get("price"), row.get("salePrice"), row.get("standardPrice"),
                   row.get("sourcePrice"), "0")
    stock = row.get("quantity")
    if stock is None:
        stock = row.get("stock")
    if stock is None:
        stock = row.get("availableStock")
    state = _first(row.get("dxmState"), row.get("state"), row.get("status"))
    stock_value = stock if stock is not None else 0
    return {
        "id": _first(row.get("id"), row.get("idStr"), row.get("productId"),
                     row.get("dxmProductId")),
        "product_id": _first(row.get("productId"), row.get("productIdStr")),
        "title": _first(row.get("productName"), row.get("title"), row.get("name")),
        "image": image,
        "images": images,
        "shop_id": _first(row.get("shopId"), row.get("shopIdStr")),
        "site": _first(row.get("site"), row.get("siteCode")),
        "category_id": _first(row.get("categoryId"), row.get("platformCategoryId")),
        "category_name": _first(row.get("categoryName"), row.get("transName")),
        "state": state,
        "update_time": _first(row.get("updateTime"), row.get("gmtModified"),
                              row.get("modifyTime")),
        "price": price,
        "stock": stock_value,
        "sku": _first(row.get("sellerSku"), row.get("sku")),
        "currency": _first(row.get("currency"), row.get("sourceCurrency")),
        "err_msg": _first(row.get("errMsg"), row.get("errorMsg")),
        # 兼容旧命名（Amazon 归一化风格 / 通用列表渲染）
        "status": state,
        "main_image": image,
        "extra_images": images[1:] if len(images) > 1 else [],
        "quantity": stock_value,
    }


def extract_product(detail: dict) -> dict:
    """从详情返回中取出商品对象（``data.product`` 优先，其次 ``data``/本身）。

    深拷贝，保证调用方改 overrides 时不会污染详情缓存。
    """
    if not isinstance(detail, dict):
        return {}
    node = detail
    data = node.get("data")
    if isinstance(data, dict):
        node = data
    product = node.get("product")
    if isinstance(product, dict):
        return copy.deepcopy(product)
    return copy.deepcopy(node)


def _banned_hits(raw) -> list[str]:
    """兼容违禁词接口多种返回形状，提取命中词（失败/空 → []）。"""
    if raw in (None, "", 0, "0", {}, []):
        return []
    hits: list[str] = []

    def walk(node, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(node, str):
            text = node.strip()
            if text and text.lower() not in ("none", "null", "false", "true", "ok", "success"):
                hits.append(text)
        elif isinstance(node, dict):
            for key in ("word", "words", "bannedWord", "bannedWords", "banned_word",
                        "bannedWordList", "list", "result", "data", "name", "content"):
                if key in node:
                    walk(node[key], depth + 1)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item, depth + 1)

    walk(raw)
    seen: set[str] = set()
    out: list[str] = []
    for hit in hits:
        if hit not in seen:
            seen.add(hit)
            out.append(hit)
    return out[:20]


class TikTokClient:
    """TikTok 店小秘 API 客户端。

    ``cookies`` 支持 Cookie 头字符串或 dict；``base`` 可指向测试网关。
    """

    def __init__(self, cookies: str | dict, timeout: int = 30,
                 base: str = BASE):
        if isinstance(cookies, str):
            cookies = parse_cookie(cookies)
        elif cookies is None:
            cookies = {}
        self.timeout = timeout
        self.base = str(base or BASE).rstrip("/")
        self.sleep = time.sleep
        self.s = requests.Session()
        for key, value in dict(cookies or {}).items():
            try:
                self.s.cookies.set(str(key), str(value), domain=".dianxiaomi.com")
            except Exception:
                self.s.cookies.set(str(key), str(value))
        self.s.headers.update({
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.base}/web/tiktokProduct/add",
        })

    # ---------------- 基础请求 ----------------
    def _url(self, path: str) -> str:
        path = str(path or "")
        if path.startswith("/api/") and self.base.endswith("/api"):
            return self.base + path[4:]
        return self.base + path

    @staticmethod
    def _is_write_path(path: str) -> bool:
        low = str(path or "").lower()
        return any(hint in low for hint in WRITE_PATH_HINTS)

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 data: dict | None = None, files: dict | None = None,
                 headers: dict | None = None, retry: bool = True) -> dict:
        """发请求并把 HTTP/JSON/code 错误统一转成中文 ``TikTokError``。

        只读接口对「瞬时繁忙/网络抖动」做有界重试（最多 3 次尝试，backoff 1.5s/3s）；
        登录失效、其它业务错误、HTTP 4xx 不重试。
        **写接口（含 ``/add.json``）永不自动重试**，避免重复写入。
        """
        if self._is_write_path(path):
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
                try:
                    self.sleep(max(0.0, float(delay)))
                except Exception:
                    pass
        raise TikTokError(last_error or "请求失败：请稍后重试")

    def _request_once(self, method: str, path: str, *, params: dict | None = None,
                      data: dict | None = None, files: dict | None = None,
                      headers: dict | None = None) -> dict:
        try:
            response = self.s.request(method, self._url(path), params=params,
                                      data=data, files=files, headers=headers,
                                      timeout=self.timeout)
        except requests.Timeout as exc:
            raise _Retryable(
                f"请求超时（{self.timeout}s）：{path}，请检查网络或稍后重试") from exc
        except requests.ConnectionError as exc:
            raise _Retryable(f"网络连接失败：{str(exc)[:160]}") from exc
        except requests.RequestException as exc:
            raise TikTokError(f"网络请求失败：{str(exc)[:160]}") from exc

        if response.status_code in (401, 403):
            raise TikTokError(
                f"登录已失效（HTTP {response.status_code}）：请重新粘贴店小秘 Cookie")
        if response.status_code >= 400:
            raise TikTokError(
                f"接口 HTTP {response.status_code}：{str(response.text or '')[:160]}")

        text = str(response.text or "")
        try:
            payload = response.json()
        except Exception as exc:
            low = text.lower()
            if "<html" in low or any(hint in text for hint in _LOGIN_HINTS):
                raise TikTokError("登录已失效或未登录：请重新粘贴店小秘 Cookie") from exc
            raise TikTokError(
                f"接口返回非 JSON（HTTP {response.status_code}）：{text[:160]}") from exc

        if not isinstance(payload, dict):
            raise TikTokError(f"接口返回格式异常：{str(payload)[:160]}")

        code = payload.get("code")
        if str(code) in ("2001", "401", "403"):
            raise TikTokError(str(payload.get("msg") or "登录已失效：请重新粘贴店小秘 Cookie"))
        if code not in (0, "0"):
            msg = str(payload.get("msg") or "").strip()
            low = (msg + " " + str(code)).lower()
            if any(hint in msg for hint in _LOGIN_HINTS) or "unauthorized" in low:
                raise TikTokError(msg or "登录已失效：请重新粘贴店小秘 Cookie")
            busy = (str(code) == str(RETRY_BUSY_CODE)
                    or any(hint in msg or hint in low for hint in RETRY_BUSY_HINTS))
            if busy:
                raise _Retryable(msg or f"系统繁忙，请稍后重试（code={code}）")
            raise TikTokError(msg or f"接口错误 code={code}")
        return payload

    def _get(self, path: str, params: dict | None = None, **kw) -> dict:
        return self._request("GET", path, params=params, **kw)

    def _post(self, path: str, data: dict | None = None, **kw) -> dict:
        return self._request("POST", path, data=data or {}, **kw)

    # ---------------- 登录 / 店铺 / 站点 ----------------
    def user_in(self) -> dict:
        """GET /api/userIn.json 的 data（登录失效抛 TikTokError）。"""
        payload = self._get("/api/userIn.json")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def check_login(self) -> bool:
        try:
            payload = self._get("/api/userIn.json")
            return bool(payload.get("code") in (0, "0")
                        and isinstance(payload.get("data"), (dict, list)))
        except Exception:
            return False

    def shops(self) -> list[dict]:
        """TikTok 店铺列表（``userIn.shopMap`` 中 ``platform=="tiktok"``）。

        兼容 ``shopSiteList`` / ``marketplaceIds`` / ``childShopList`` 展开；
        本账号未绑定 TikTok 店铺 → 返回 ``[]``（如实反映，不造假）。
        """
        data = self.user_in()
        raw = data.get("shopMap")
        if raw is None:
            raw = data.get("shopList") or data.get("shops")
        if isinstance(raw, dict):
            rows = list(raw.values())
        elif isinstance(raw, list):
            rows = raw
        else:
            rows = []

        out: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            platform = _str(row.get("platform")).strip().lower()
            if platform not in ("tiktok", "tiktokshop", "tiktok_shop"):
                continue
            main = self._shop_entry(row)
            if main:
                out.append(main)
            children = _json_maybe(row.get("childShopList"))
            for child in self._shop_children(children):
                entry = self._shop_entry(child)
                if entry:
                    entry["platform"] = entry.get("platform") or "tiktok"
                    out.append(entry)

        # 去重（id 优先）并稳定排序
        seen: set[str] = set()
        unique: list[dict] = []
        for shop in out:
            key = shop.get("id") or ""
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            unique.append(shop)
        unique.sort(key=lambda s: (_num(s.get("id"), 0), str(s.get("id") or "")))
        return unique

    @staticmethod
    def _shop_children(children) -> list[dict]:
        if isinstance(children, dict):
            children = list(children.values())
        if not isinstance(children, list):
            return []
        rows: list[dict] = []
        for child in children:
            if isinstance(child, dict):
                rows.append(child)
            elif isinstance(child, list):
                rows.extend(c for c in child if isinstance(c, dict))
        return rows

    def _shop_entry(self, row: dict) -> dict | None:
        if not isinstance(row, dict):
            return None
        shop_id = _first(row.get("id"), row.get("idStr"), row.get("shopId"),
                         row.get("shopIdStr"))
        if not shop_id:
            return None
        sites = self._shop_sites(row)
        marketplace_ids = self._marketplace_ids(row)
        return {
            "id": shop_id,
            "name": _first(row.get("name"), row.get("shopName"), shop_id),
            "platform": _first(row.get("platform"), "tiktok") or "tiktok",
            "merchant_id": _first(row.get("merchantId")),
            "marketplace_id": _first(row.get("marketPlaceId"), row.get("marketplaceId")),
            "marketplace_ids": marketplace_ids,
            "sites": sites,
            "is_tiktok": True,
        }

    @staticmethod
    def _marketplace_ids(row: dict) -> list[str]:
        raw = row.get("marketplaceIds")
        if raw is None:
            raw = row.get("marketPlaceIds")
        raw = _json_maybe(raw)
        tokens: list[str] = []
        if isinstance(raw, str):
            for sep in (",", "#", "|", " "):
                raw = raw.replace(sep, ",")
            tokens = [p.strip() for p in raw.split(",") if p.strip()]
        elif isinstance(raw, list):
            tokens = [str(p).strip() for p in raw if str(p).strip()]
        elif raw not in (None, ""):
            tokens = [str(raw).strip()]
        single = _first(row.get("marketPlaceId"), row.get("marketplaceId"))
        if single:
            tokens.append(single)
        seen: set[str] = set()
        out: list[str] = []
        for token in tokens:
            if token not in seen:
                seen.add(token)
                out.append(token)
        return out

    @staticmethod
    def _coerce_site_code(token) -> str:
        code = _str(token).strip()
        if not code:
            return ""
        if code.upper() in SITE_META:
            return code.upper()
        if code.lower() == "4seller":
            return "US"
        if 1 < len(code) <= 3 and code.isalpha():
            return code.upper()
        return ""

    @classmethod
    def _site_rows(cls, node) -> list[tuple[str, str, str]]:
        """把 shopSiteList 的多种形状展开成 ``(site, site_shop_id, name)``。"""
        node = _json_maybe(node)
        rows: list[tuple[str, str, str]] = []
        if node in (None, "", {}, []):
            return rows
        if isinstance(node, str):
            for token in node.replace("#", ",").replace("|", ",").replace(" ", ",").split(","):
                code = cls._coerce_site_code(token)
                if code:
                    rows.append((code, "", ""))
            return rows
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict):
                    code = cls._coerce_site_code(
                        _first(item.get("site"), item.get("marketPlaceId"),
                               item.get("marketplaceId"), item.get("siteCode"),
                               item.get("code"), item.get("region")))
                    if not code:
                        rows.extend(cls._site_rows(item.get("shopSiteList")))
                        continue
                    rows.append((code, _first(item.get("idStr"), item.get("id"),
                                              item.get("siteShopId"), item.get("shopId")),
                                 _first(item.get("name"), item.get("shopName"),
                                        item.get("siteShopName"), item.get("label"))))
                elif isinstance(item, str):
                    rows.extend(cls._site_rows(item))
            return rows
        if isinstance(node, dict):
            code = cls._coerce_site_code(
                _first(node.get("site"), node.get("marketPlaceId"),
                       node.get("marketplaceId"), node.get("siteCode")))
            if code:
                rows.append((code, _first(node.get("idStr"), node.get("id"),
                                          node.get("siteShopId")),
                             _first(node.get("name"), node.get("shopName"))))
                return rows
            for key, value in node.items():
                site_code = cls._coerce_site_code(key)
                if not site_code:
                    continue
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            rows.append((site_code,
                                         _first(item.get("idStr"), item.get("id"),
                                                item.get("siteShopId")),
                                         _first(item.get("name"), item.get("shopName"))))
                        elif isinstance(item, str):
                            rows.append((site_code, "", item))
                elif isinstance(value, dict):
                    rows.append((site_code,
                                 _first(value.get("idStr"), value.get("id"),
                                        value.get("siteShopId")),
                                 _first(value.get("name"), value.get("shopName"))))
                elif value not in (None, ""):
                    rows.append((site_code, "", _str(value)))
        return rows

    @classmethod
    def _shop_sites(cls, row: dict) -> list[dict]:
        """店铺可用站点（``shopSiteList`` ∪ ``marketplaceIds``，去重保序）。

        全球店铺的 ``childShopList`` 子店站点也并入父店，保证 UI 站点下拉完整。
        """
        raw_sites = row.get("shopSiteList")
        if raw_sites is None:
            raw_sites = row.get("shopSites") or row.get("sites")
        rows = cls._site_rows(raw_sites)
        for token in cls._marketplace_ids(row):
            code = cls._coerce_site_code(token)
            if code:
                rows.append((code, "", ""))
        for child in cls._shop_children(_json_maybe(row.get("childShopList"))):
            rows.extend(cls._site_rows(child))
            for token in cls._marketplace_ids(child):
                code = cls._coerce_site_code(token)
                if code:
                    rows.append((code, "", ""))
        seen: set[str] = set()
        out: list[dict] = []
        for code, site_shop_id, name in rows:
            if not code or code in seen:
                if code in seen and site_shop_id:
                    for existing in out:
                        if existing.get("site") == code and not existing.get("site_shop_id"):
                            existing["site_shop_id"] = site_shop_id
                            existing["name"] = name or existing.get("name", "")
                continue
            seen.add(code)
            out.append({"site": code, "site_shop_id": site_shop_id, "name": name})
        return out

    def _l7_sites(self) -> list[str]:
        try:
            payload = self._get("/api/tiktokProduct/getL7ShopIdAndSite.json")
        except Exception:
            return list(L7_SITES)
        data = _as_dict(payload.get("data"))
        raw = data.get("tiktokL7Site")
        if isinstance(raw, str):
            raw = _json_maybe(raw)
        if not isinstance(raw, list):
            return list(L7_SITES)
        codes = [_str(code).strip().upper() for code in raw if _str(code).strip()]
        return codes or list(L7_SITES)

    @staticmethod
    def _site_row(code: str, cross_border: bool = True,
                  site_shop_id: str = "") -> dict:
        meta = SITE_META.get(code) or {}
        return {
            "site": code,
            "code": code,
            "country": _str(meta.get("country")),
            "currency": _str(meta.get("currency")),
            "language": _str(meta.get("language") or "en"),
            "language_name": _str(meta.get("language_name") or "英语"),
            "cross_border": bool(cross_border),
            "site_shop_id": _str(site_shop_id),
        }

    def sites(self, shop_id: str = "") -> list[dict]:
        """站点列表：有店铺 → 店铺站点 ∪ L7；无店铺 → 23 个 L7 站点。

        返回 ``{site,code,country,currency,language,language_name,cross_border,
        site_shop_id}``；L7 站点 ``cross_border=True``（可用于无店铺时选类目）。
        """
        shop_sites: list[dict] = []
        if shop_id not in (None, ""):
            try:
                for shop in self.shops():
                    if str(shop.get("id")) == str(shop_id):
                        shop_sites = list(shop.get("sites") or [])
                        break
            except Exception:
                shop_sites = []
        out: list[dict] = []
        seen: set[str] = set()
        for row in shop_sites:
            code = self._coerce_site_code(row.get("site"))
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(self._site_row(code, True, row.get("site_shop_id", "")))
        for code in self._l7_sites():
            if code in seen:
                continue
            seen.add(code)
            out.append(self._site_row(code, True))
        return out

    def site_limits(self, site: str = "") -> dict:
        """站点限额（getLimitBySite）；失败/空站点返回内置默认，不抛错。"""
        limits = dict(DEFAULT_LIMITS)
        if site in (None, ""):
            return limits
        try:
            payload = self._post("/api/tiktokProduct/getLimitBySite.json",
                                 {"site": str(site)})
        except Exception:
            return limits
        data = _as_dict(payload.get("data"))
        for key in DEFAULT_LIMITS:
            value = data.get(key)
            if value is None:
                continue
            try:
                if key in ("priceMin", "priceMax"):
                    limits[key] = float(value)
                else:
                    limits[key] = int(float(value))
            except Exception:
                continue
        return limits

    def warehouses(self, shop_id: str = "") -> list[dict]:
        """仓库列表（best-effort：失败返回 []）。"""
        try:
            payload = self._post("/api/tiktokProduct/listWarehouse.json",
                                 {"shopId": str(shop_id or "-1")})
        except Exception:
            return []
        data = payload.get("data")
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    # ---------------- 商品 ----------------
    def list_products(self, state="draft", page=1, page_size=20,
                      shop_id="-1", keyword="", site="") -> dict:
        """分页列表；state ∈ draft/offline/online。"""
        state = _str(state or "draft").strip().lower()
        if state not in ("draft", "offline", "online"):
            raise TikTokError(f"未知列表状态：{state}（可选 draft/offline/online）")
        data = {
            "pageNo": int(page or 1),
            "pageSize": int(page_size or 20),
            "shopId": _str(shop_id) or "-1",
            "dxmState": state,
            "searchValue": _str(keyword),
        }
        if site not in (None, ""):
            data["site"] = _str(site)
        payload = self._post("/api/tiktokProduct/pageList.json", data)
        raw = _as_dict(payload.get("data"))
        page_info = _as_dict(raw.get("page"))
        rows = page_info.get("list") or raw.get("list") or []
        counters = {
            "drafts": _num(raw.get("draftNum")),
            "wait_publish": _num(raw.get("waitPublish")),
            "publish_fail": _num(raw.get("publishFail")),
            "publishing": _num(raw.get("publishing")),
        }
        if not any(counters.values()):
            try:
                counts = self.counts(shop_id)
                counters = {
                    "drafts": _num(counts.get("drafts")),
                    "wait_publish": _num(counts.get("wait_publish")),
                    "publish_fail": _num(counts.get("publish_fail")),
                    "publishing": _num(counts.get("publishing")),
                }
            except Exception:
                pass
        markets = raw.get("markets") or raw.get("siteMap") or raw.get("marketPids") or {}
        if not isinstance(markets, dict):
            markets = {}
        return {
            "items": [normalize_product(row) for row in _as_list(rows)
                      if isinstance(row, dict)],
            "total": _num(page_info.get("totalSize") or raw.get("totalSize")),
            "page_no": _num(page_info.get("pageNo") or page, 1),
            "total_page": _num(page_info.get("totalPage")),
            "drafts": counters["drafts"],
            "wait_publish": counters["wait_publish"],
            "publish_fail": counters["publish_fail"],
            "publishing": counters["publishing"],
            "markets": {str(k): str(v) for k, v in markets.items()},
        }

    def counts(self, shop_id="-1") -> dict:
        """采集箱计数（保留原始键 + 归一化键：drafts/wait_publish/...）。"""
        payload = self._post("/api/tiktokProduct/getOfflineCounts.json",
                             {"shopId": _str(shop_id) or "-1"})
        data = _as_dict(payload.get("data"))
        out = dict(data)
        out.update({
            "drafts": _num(data.get("draftNum")),
            "wait_publish": _num(data.get("waitPublish")),
            "publish_fail": _num(data.get("publishFail")),
            "publishing": _num(data.get("publishing")),
            "publishing_timing": _num(data.get("publishingTiming")),
        })
        return out

    def product_detail(self, product_id: str) -> dict:
        """详情：``gettiktokProduct(idStr)`` → 失败回退 ``getByProductId(productId)``。

        返回接口 ``data`` 原样（含 product/category/categoryList/categoryTree）；
        两者都失败/为空时抛中文 :class:`TikTokError`。
        """
        product_id = _str(product_id).strip()
        if not product_id:
            raise TikTokError("商品 id 为空：无法读取 TikTok 商品详情")
        try:
            payload = self._post("/api/tiktokProduct/gettiktokProduct.json",
                                 {"idStr": product_id})
            data = payload.get("data")
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
        try:
            payload = self._post("/api/tiktokProduct/getByProductId.json",
                                 {"productId": product_id})
        except TikTokError as exc:
            raise TikTokError(
                f"读不到商品 {product_id}：{exc}（该账号可能未绑定 TikTok 店铺，"
                "或商品不在 TikTok 采集箱）") from exc
        data = payload.get("data")
        if isinstance(data, dict) and data:
            return data
        raise TikTokError(
            f"读不到商品 {product_id}（接口返回空数据）：该账号未绑定 TikTok 店铺，"
            "或 TikTok 采集箱中没有该商品")

    def product_variants(self, product_id: str) -> list[dict]:
        """变种列表（best-effort：失败/无商品返回 []）。"""
        if product_id in (None, ""):
            return []
        try:
            payload = self._post("/api/tiktokProduct/getVariantByProductId.json",
                                 {"id": _str(product_id)})
        except Exception:
            return []
        data = payload.get("data")
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    # ---------------- 类目 ----------------
    @staticmethod
    def _require_site(site: str) -> str:
        site = _str(site).strip().upper()
        if not site:
            raise TikTokError("站点 site 必填（L7 站点代码，如 GB/US/DE）")
        return site

    def categories(self, site, parent_id="0", shop_id="-1",
                   cross_border=True) -> list[dict]:
        """类目树：顶层 ``categoryParentId="0"``；下钻传父节点 **categoryId**。"""
        site = self._require_site(site)
        parent = _str(parent_id if parent_id not in (None, "") else "0") or "0"
        payload = self._post("/api/tiktokCategory/list.json", {
            "shopId": _str(shop_id) or "-1",
            "site": site,
            "crossBorder": 1 if cross_border else 0,
            "categoryParentId": parent,
        })
        data = payload.get("data")
        if isinstance(data, dict):
            data = data.get("list") or data.get("categoryList") or []
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def search_categories(self, keyword, site, shop_id="-1",
                          cross_border=True) -> list[dict]:
        """类目搜索：关键词字段名是 ``category``（``keyword`` 无效，实测）。"""
        site = self._require_site(site)
        keyword = _str(keyword).strip()
        if not keyword:
            return []
        payload = self._post("/api/tiktokCategory/searchCategory.json", {
            "shopId": _str(shop_id) or "-1",
            "site": site,
            "crossBorder": 1 if cross_border else 0,
            "category": keyword,
        })
        data = payload.get("data")
        if isinstance(data, dict):
            data = data.get("list") or data.get("categoryList") or []
        return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []

    def category_attributes(self, site, category_id, shop_id="-1",
                            cross_border=True, attr_type=3) -> list[dict]:
        """类目属性：必须带 ``type=3``（不带只返回 1 条基础属性，实测）。"""
        site = self._require_site(site)
        category_id = _str(category_id).strip()
        if not category_id:
            return []
        data = {
            "shopId": _str(shop_id) or "-1",
            "site": site,
            "crossBorder": 1 if cross_border else 0,
            "categoryId": category_id,
        }
        try:
            data["type"] = int(attr_type)
        except Exception:
            data["type"] = 3
        payload = self._post("/api/tiktokCategory/attributeList.json", data)
        rows = payload.get("data")
        if isinstance(rows, dict):
            rows = rows.get("list") or rows.get("attributeList") or []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def category_history(self, site, shop_id="-1") -> list[dict]:
        """最近使用类目（historyList 主用；getHistoryCategory 兜底，失败 []）。"""
        site = self._require_site(site)
        data = {"shopId": _str(shop_id) or "-1", "site": site}
        try:
            payload = self._post("/api/tiktokCategory/historyList.json", data)
            rows = payload.get("data")
            if isinstance(rows, list) and rows:
                return [row for row in rows if isinstance(row, dict)]
        except Exception:
            pass
        try:
            payload = self._post("/api/tiktokCategory/getHistoryCategory.json", data)
            rows = payload.get("data")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        except Exception:
            pass
        return []

    def brands(self, site, category_id="", shop_id="-1") -> list[dict]:
        """品牌列表（best-effort：接口实测 500/失败 → []）。"""
        site = _str(site).strip().upper()
        if not site:
            return []
        params = {
            "shopId": _str(shop_id) or "-1",
            "site": site,
            "crossBorder": 1,
            "categoryId": _str(category_id),
        }
        try:
            payload = self._post("/api/tiktokCategory/listCategoryBrands.json", params)
            rows = payload.get("data")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        except Exception:
            pass
        try:
            payload = self._get("/api/tiktokCategory/listCategoryBrand.json", params)
            rows = payload.get("data")
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        except Exception:
            pass
        return []

    # ---------------- 违禁词（只读，失败不抛） ----------------
    def check_banned_words(self, title="", description="") -> list[str]:
        """违禁词检查（仅提示，不阻断；失败返回 []）。"""
        try:
            payload = self._post("/api/bannedWord/singleCheckForBannedWord.json", {
                "shopId": "-1",
                "productId": "",
                "editUrl": f"{self.base}/web/tiktokProduct/add",
                "platform": "tiktok",
                "productTitle": _str(title),
                "description": _str(description),
            })
        except Exception:
            return []
        return _banned_hits(payload.get("data"))

    # ---------------- 唯一写入口：ZIP + multipart ----------------
    @staticmethod
    def build_save_zip(payload: dict) -> bytes:
        """构造 ZIP（单条目 ``tiktokSave.txt``，UTF-8 紧凑 JSON）。"""
        return zip_payload(payload)

    # 旧命名兼容
    zip_payload = build_save_zip

    @staticmethod
    def _raise_save_error(message: str) -> None:
        """保存失败：含「店铺」→ TikTokNotReady（中文可操作提示）。"""
        msg = _str(message).strip() or "保存失败"
        if "店铺" in msg:
            raise TikTokNotReady(
                "未绑定/未选择 TikTok 店铺：请先在店小秘『授权店铺』绑定 TikTok Shop，"
                f"再回到 TikTok 刊登页保存。（服务端返回：{msg}）")
        raise TikTokError(msg[:300])

    def save_product(self, payload: dict, op: str = OP_SAVE,
                     save_mode: str = "") -> dict:
        """保存/发布唯一写入口：POST multipart ``file``(ZIP)+``op``(+``saveMode``)。

        - 字段顺序：``file`` 在前，``op`` 在后（``saveMode`` 仅在非空时追加）；
        - 写接口不自动重试；
        - 未绑定店铺的错误（含「店铺」）→ :class:`TikTokNotReady`；
        - 返回接口原始 JSON。
        """
        if not isinstance(payload, dict) or not payload:
            raise TikTokError("保存包为空：拒绝调用保存接口")
        op = _str(op or OP_SAVE).strip() or OP_SAVE
        if op not in VALID_OPS:
            raise TikTokError(f"未知操作 op={op}（仅支持 1/2/3/5）")
        blob = self.build_save_zip(payload)
        save_mode = _str(save_mode).strip()
        # 用「文件列表」而不是 dict：requests 对 files dict 会先编码 data 再编码
        # files，契约要求 multipart 里 file 字段在前、op 在后，因此把 op/saveMode
        # 也放进 files（(None, value) = 普通表单字段），顺序即为列表顺序。
        fields: list[tuple[str, tuple]] = [
            ("file", ("blob", blob, "application/zip")),
            ("op", (None, op)),
        ]
        if save_mode:
            fields.append(("saveMode", (None, save_mode)))
        headers = {
            "Referer": f"{self.base}/web/tiktokProduct/edit?id={_str(payload.get('id')) or ''}",
        }
        try:
            payload_out = self._request(
                "POST", "/api/tiktokProduct/add.json",
                files=fields,
                headers=headers,
                retry=False,
            )
        except TikTokError as exc:
            self._raise_save_error(str(exc))
            raise  # pragma: no cover - _raise_save_error 总是抛异常
        except Exception as exc:  # noqa: BLE001
            raise TikTokError(f"保存请求失败：{str(exc)[:160]}") from exc
        # code=0 但 msg 是失败语义（店小秘老毛病）：按失败处理
        inner = _as_dict(payload_out.get("data"))
        msg = _str(inner.get("msg") or payload_out.get("msg"))
        if msg:
            try:
                from ..services.publisher import is_failure_msg
                if is_failure_msg(msg):
                    self._raise_save_error(msg)
            except TikTokError:
                raise
            except Exception:
                pass
        return payload_out

    # 旧命名兼容
    def save(self, payload: dict, op: str = OP_SAVE,
             save_mode: str = "") -> dict:
        return self.save_product(payload, op, save_mode)

    # ---------------- 批量写（无自动重试） ----------------
    @staticmethod
    def _ids_value(ids) -> str:
        if isinstance(ids, (list, tuple, set)):
            return ",".join(str(item) for item in ids if str(item).strip())
        return _str(ids).strip()

    def publish_products(self, ids, shop_ids="", save_mode="") -> dict:
        """批量发布/移入待发布（写接口，不自动重试）。"""
        data = {"ids": self._ids_value(ids)}
        if shop_ids not in (None, ""):
            data["shopIds"] = _str(shop_ids)
        save_mode = _str(save_mode).strip()
        if save_mode:
            data["saveMode"] = save_mode
        try:
            return self._post("/api/tiktokProduct/batchPublish.json", data, retry=False)
        except TikTokError as exc:
            self._raise_save_error(str(exc))
            raise  # pragma: no cover

    def delete_products(self, ids) -> dict:
        """批量删除（写接口，不自动重试）。"""
        try:
            return self._post("/api/tiktokProduct/batchProductDelete.json",
                              {"ids": self._ids_value(ids)}, retry=False)
        except TikTokError as exc:
            self._raise_save_error(str(exc))
            raise  # pragma: no cover


__all__ = [
    "TikTokClient", "TikTokError", "TikTokNotReady",
    "BASE", "UA", "SAVE_NAME", "SAVE_MODE_AS_DRAFT",
    "OP_SAVE", "OP_PUBLISH", "OP_MOVE_PENDING", "OP_SCHEDULE",
    "L7_SITES", "L7_SHOP_ID", "SITE_META", "DEFAULT_LIMITS",
    "parse_cookie", "zip_payload", "normalize_product", "extract_product",
]

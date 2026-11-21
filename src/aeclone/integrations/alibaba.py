"""Alibaba.com（国际站）商品接口：对接店小秘的 alibabaIcbuProduct 私有 API。

已实测（2026-11-21）：
- 详情：GET  /api/alibabaIcbuProduct/edit.json?id=<id>
- 列表：GET  /api/alibabaIcbuProduct/list.json
- 保存：POST /api/alibabaIcbuProduct/add.json
         multipart/form-data: file=zip(alibabaSave.txt=JSON), op=1
- 类目：/api/alibabaIcbuCategory/list.json
        /api/alibabaIcbuCategory/getByCategoryId.json
        /api/alibabaIcbuCategory/attributeList.json
        /api/alibabaIcbuCategory/schemaList.json
- 变种模板：/api/variationTemplate/com/alibaba/getNameListByCategory.json
- 违禁词：/api/bannedWord/alibabaCheckBannedWord
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import requests

BASE = "https://www.dianxiaomi.com/api"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SAVE_NAME = "alibabaSave.txt"

# 保存 JSON 里保持字符串的字段（店小秘后端按字符串收，内部再反序列化）
JSON_STRING_KEYS = ("attributes", "detailImage", "bulkDiscountPrices",
                    "wholesaleTrade", "productSku")


def parse_cookie(text: str) -> dict:
    return {k.strip(): v.strip() for k, v in
            (p.split("=", 1) for p in text.split(";") if "=" in p)}


class AlibabaError(RuntimeError):
    pass


class AlibabaClient:
    def __init__(self, cookies: dict | str | None = None, timeout: int = 30):
        if isinstance(cookies, str):
            cookies = parse_cookie(cookies)
        elif cookies is None:
            cookies = {}
        self.timeout = timeout
        self.s = requests.Session()
        self.s.cookies.update(cookies or {})
        self.s.headers.update({"User-Agent": UA,
                               "X-Requested-With": "XMLHttpRequest"})

    # ---------- 基础请求 ----------
    def _get(self, path: str, params: dict | None = None) -> dict:
        r = self.s.get(BASE + path, params=params, timeout=self.timeout)
        r.raise_for_status()
        return self._json(r)

    def _post(self, path: str, data=None, files=None) -> dict:
        r = self.s.post(BASE + path, data=data, files=files, timeout=self.timeout)
        r.raise_for_status()
        return self._json(r)

    @staticmethod
    def _json(r: requests.Response) -> dict:
        try:
            d = r.json()
        except Exception:
            raise AlibabaError(f"非 JSON 响应：{r.status_code} {r.text[:200]}")
        if d.get("code") not in (0, "0"):
            raise AlibabaError(d.get("msg") or f"接口错误 code={d.get('code')}")
        return d

    # ---------- 商品 ----------
    def product_detail(self, product_id: str) -> dict:
        d = self._get("/alibabaIcbuProduct/edit.json", {"id": str(product_id)})
        return (d.get("data") or {}).get("product") or {}

    def list_products(self, page: int = 1, page_size: int = 20, dxm_state: str | None = None,
                      shop_id: str | None = None, search: str | None = None) -> dict:
        params = {"page": page, "pageSize": page_size}
        if dxm_state:
            params["dxmState"] = dxm_state
        if shop_id:
            params["shopId"] = shop_id
        if search:
            params["searchValue"] = search
        return self._get("/alibabaIcbuProduct/list.json", params)

    def shops(self) -> list[dict]:
        d = self._post("/alibabaIcbuShopInfoSync/list.json", data={})
        data = d.get("data") or {}
        return data.get("shopInfoList") or data.get("list") or []

    # ---------- 类目 / 属性 ----------
    def categories(self, parent_id: str | None = None) -> list[dict]:
        """类目列表；传 parent_id 时取其子类目（店小秘用 pcid 参数）。"""
        params = {"pcid": str(parent_id)} if parent_id else {}
        d = self._get("/alibabaIcbuCategory/list.json", params)
        data = d.get("data") or []
        if isinstance(data, dict):
            return data.get("categoryList") or []
        return data

    def search_categories(self, keyword: str, max_nodes: int = 400,
                          max_depth: int = 4) -> list[dict]:
        """按关键词搜索类目：优先服务端 searchCategory 接口（category 参数）。"""
        kw = (keyword or "").strip()
        if not kw:
            return []
        try:
            d = self._post("/alibabaIcbuCategory/searchCategory.json", data={"category": kw})
            rows = d.get("data") or []
            if isinstance(rows, dict):
                rows = rows.get("list") or rows.get("categoryList") or []
            if rows:
                return rows[:max_nodes]
        except Exception:
            pass
        kw = kw.lower()
        max_nodes = min(max_nodes, 120)
        cache = getattr(self, "_cat_cache", None)
        if cache is None:
            cache = self._cat_cache = {}
        if not cache:
            for root in self.categories():
                cache[str(root["categoryId"])] = root
        hits, queue, seen, scanned = [], [str(r["categoryId"]) for r in cache.values()], set(), 0
        while queue and scanned < max_nodes:
            cid = queue.pop(0)
            if cid in seen:
                continue
            seen.add(cid); scanned += 1
            node = cache.get(cid)
            if not node:
                continue
            name = f'{node.get("nameZh","")} {node.get("nameEn","")} {node.get("nodePath","")}'.lower()
            if kw in name:
                hits.append(node)
            if int(node.get("level") or 1) < max_depth:
                try:
                    children = self.categories(cid)
                except Exception:
                    children = []
                for ch in children:
                    ch["_parent"] = cid
                    cache[str(ch["categoryId"])] = ch
                    queue.append(str(ch["categoryId"]))
        return hits

    def required_attributes(self, category_id: str) -> list[dict]:
        return [a for a in self.category_attributes(category_id).get("attributes", [])
                if int(a.get("required") or 0) == 1]

    def category_detail(self, category_id: str, shop_id: str | None = None) -> dict:
        params = {"categoryId": str(category_id)}
        if shop_id:
            params["shopId"] = str(shop_id)
        d = self._post("/alibabaIcbuCategory/getByCategoryId.json", data=params)
        return d.get("data") or {}

    def category_history(self, category_id: str) -> list[str]:
        d = self._post("/alibabaIcbuCategory/getHistoryCategory.json",
                       data={"categoryId": str(category_id)})
        return d.get("data") or []

    def category_attributes(self, category_id: str) -> dict:
        data = self._post("/alibabaIcbuCategory/attributeList.json",
                          data={"categoryId": str(category_id)}).get("data") or []
        attrs = data if isinstance(data, list) else (data.get("attributes") or data.get("list") or [])
        return {"attributes": attrs}

    def category_schema(self, category_id: str) -> dict:
        data = self._post("/alibabaIcbuCategory/schemaList.json",
                          data={"categoryId": str(category_id)}).get("data") or []
        fields = data if isinstance(data, list) else (data.get("fields") or data.get("list") or [])
        return {"fields": fields}

    def variation_names(self, category_id: str) -> list:
        d = self._get("/variationTemplate/com/alibaba/getNameListByCategory.json",
                      {"categoryId": str(category_id)}).get("data") or []
        return d if isinstance(d, list) else (d.get("list") or d.get("names") or [])

    # ---------- 保存 ----------
    @staticmethod
    def build_save_dict(product: dict) -> dict:
        """把 edit.json 的 product 转成保存用 JSON（字段名与实测一致）。"""
        def j(v):
            return v if isinstance(v, str) else json.dumps(v if v is not None else [],
                                                          ensure_ascii=False)
        img = product.get("mainImage") or ""
        draft = product.get("draftImgUrl") or img.replace(",", "|")
        return {
            "op": "1",
            "id": str(product.get("id") or ""),
            "productId": str(product.get("productId") or ""),
            "productIdDecrypt": str(product.get("productIdDecrypt") or ""),
            "isSmartEdit": str(product.get("isSmartEdit") or 0),
            "shopId": str(product.get("shopId") or ""),
            "categoryId": str(product.get("categoryId") or ""),
            "publishType": str(product.get("publishType") or 0),
            "sourceUrl": str(product.get("sourceUrl") or ""),
            "fullCid": str(product.get("fullCid") or ""),
            "subject": str(product.get("subject") or ""),
            "keywords": str(product.get("keywords") or ""),
            "groupId": str(product.get("groupId") or ""),
            "motorIds": str(product.get("motorIds") or ""),
            "attributes": j(product.get("attributes")),
            "mainImage": img,
            "draftImgUrl": draft,
            "textDesc": str(product.get("textDesc") or ""),
            "detailImage": j(product.get("detailImage")),
            "shipmentPlace": str(product.get("shipmentPlace") or ""),
            "warehouseType": str(product.get("warehouseType") or ""),
            "overseaCountryRegion": str(product.get("overseaCountryRegion") or ""),
            "priceType": str(product.get("priceType") or "ladder_price"),
            "bulkDiscountPrices": j(product.get("bulkDiscountPrices")),
            "wholesaleTrade": j(product.get("wholesaleTrade")),
            "sourcingTrade": str(product.get("sourcingTrade") or ""),
            "productSku": j(product.get("productSku")),
            "salePkgDesc": str(product.get("salePkgDesc") or ""),
            "logisticsProperty": str(product.get("logisticsProperty") or ""),
            "marketSamplingQuantity": str(product.get("marketSamplingQuantity") or ""),
            "marketSamplingPrice": str(product.get("marketSamplingPrice") or ""),
            "marketSample": str(product.get("marketSample") or 0),
        }

    @staticmethod
    def zip_payload(save_dict: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(SAVE_NAME, json.dumps(save_dict, ensure_ascii=False))
        return buf.getvalue()

    def save_product(self, product: dict, save_dict: dict | None = None,
                     op: str | int = "1") -> dict:
        data = dict(save_dict or self.build_save_dict(product))
        data["op"] = str(op)
        blob = self.zip_payload(data)
        files = {"file": (SAVE_NAME.replace(".txt", ".zip"), blob,
                          "application/octet-stream")}
        return self._post("/alibabaIcbuProduct/add.json",
                          data={"op": str(data.get("op") or "1")}, files=files)

    def save_product_file(self, zip_path: str | Path, op: str = "1") -> dict:
        """保存已构建好的 zip（内含 alibabaSave.txt），便于调试。"""
        with open(zip_path, "rb") as fh:
            files = {"file": (Path(zip_path).name, fh.read(),
                              "application/octet-stream")}
        return self._post("/alibabaIcbuProduct/add.json", data={"op": op}, files=files)

    # ---------- 分组 / 运费 / 店铺选项 ----------
    def product_groups(self, shop_id: str) -> list[dict]:
        d = self._get(f"/alibabaIcbuShopInfoSync/syncProductGroupNoAll.json?shopId={shop_id}")
        data = d.get("data") or {}
        return data.get("alibabaIcbuProductGroupList") or []

    def freight_templates(self, shop_id: str) -> list[dict]:
        d = self._post("/alibabaIcbuShopInfoSync/freightList.json",
                       data={"shopId": str(shop_id)})
        return (d.get("data") or {}).get("freightTemplateList") or []

    def shop_options(self) -> dict:
        """店铺级下拉数据：国家/单位/省份映射。"""
        return self._post("/alibabaIcbuShopInfoSync/getSpecialInfo.json", data={}).get("data") or {}

    def units(self) -> list[dict]:
        unit = (self.shop_options() or {}).get("unit") or {}
        return [{"label": k, "value": v} for k, v in unit.items()]

    # ---------- 违禁词 ----------
    def check_banned_words(self, text: str) -> dict:
        return self._get("/bannedWord/alibabaCheckBannedWord", {"text": text})

"""店小秘 Shopee 全球/站点商品接口；路径和 ZIP 格式来自实际编辑器 JS。"""
from copy import deepcopy
import io
import json
import zipfile

from .alibaba import AlibabaClient, parse_cookie


class ShopeeClient:
    def __init__(self, cookies, *, mode="global", timeout=30):
        self.mode = "site" if mode == "site" else "global"
        self.prefix = "shopeeProduct" if self.mode == "site" else "shopeeGlobalProduct"
        self.http = AlibabaClient(cookies, timeout=timeout)

    def _get(self, path, params=None):
        return self.http._get(path, params).get("data")

    def _post(self, path, data=None):
        return self.http._post(path, data=data or {}).get("data")

    def user_info(self):
        return self._get("/userIn.json") or {}

    def shops(self):
        mapping = self.user_info().get("shopMap") or {}
        rows = mapping.values() if isinstance(mapping, dict) else mapping
        result = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            platform = str(row.get("platform") or row.get("platformName") or "").lower()
            if platform not in ("shopee", "shopeeglobal"):
                continue
            # 全球账号与站点店铺在店小秘分别展示，避免跨端点保存。
            eligible = (int(row.get("smtAutoOnline") or 0) == 0 if self.mode == "global"
                        else not row.get("marketplaceIds"))
            if not eligible:
                continue
            entry = deepcopy(row)
            entry["id"] = str(row.get("idStr") or row.get("id") or "")
            result.append(entry)
        return result

    def list_products(self, state="draft", page=1, page_size=20, shop_id="-1", keyword=""):
        offline = state in ("waitPublish", "publishFail", "publishing", "timingPublish")
        params = dict(pageNo=page, pageSize=page_size, shopId=shop_id or "-1",
            dxmState="offline" if offline else state, searchType=2, searchValue=keyword,
            productSearchType=1, sortValue=2, sortName="", fullCid="",
            checkState="", advancedTime=1, commentType=0, quantityType=0)
        if offline:
            params["dxmOfflineState"] = state
        data = self._post(f"/{self.prefix}/pageList.json", params) or {}
        page_data = data.get("page") or {}
        return {"items": page_data.get("list") or [], "total": page_data.get("totalSize", 0),
                "pages": page_data.get("totalPage", 1), "page": page_data.get("pageNo", page)}

    def product_detail(self, product_id):
        data = self._get(f"/{self.prefix}/edit.json", {"id": str(product_id)}) or {}
        if not isinstance(data, dict) or not isinstance(data.get("product"), dict):
            raise ValueError("未读到 Shopee 商品详情，请检查账号、商品类型和 ID")
        return data

    def limits(self, site="global"):
        return self._post("/shopeeCategory/getLimitBySite.json", {"site": site, "smtAutoOnline": "1"}) or {}

    def categories(self, parent_id="0", shop_id="", keyword=""):
        suffix = "listNew" if self.mode == "site" else "globalList"
        if keyword:
            suffix = "searchCategoryNew" if self.mode == "site" else "searchGlobalCategory"
        return self._post(f"/shopeeCategory/{suffix}.json", {
            "categoryParentId": str(parent_id), "shopId": str(shop_id),
            "category": keyword}) or []

    def attributes(self, category_id, shop_id=""):
        suffix = "attributeListNew" if self.mode == "site" else "globalAttributeList"
        return self._post(f"/shopeeCategory/{suffix}.json", {
            "categoryId": str(category_id), "shopId": str(shop_id)}) or []

    def logistics(self, shop_id):
        return self._get(f"/{self.prefix}/getLogistics.json", {"shopId": str(shop_id)}) or []

    def save_product(self, payload, op="1"):
        name = "shopeeSave.txt" if self.mode == "site" else "shopeeGlobalSave.txt"
        output = io.BytesIO()
        data = deepcopy(payload)
        data["op"] = str(op)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, json.dumps(data, ensure_ascii=False))
        result = self.http._post(f"/{self.prefix}/add.json", data={"op": str(op)},
            files={"file": (name.replace(".txt", ".zip"), output.getvalue(), "application/octet-stream")})
        inner = result.get("data")
        if isinstance(inner, dict) and inner.get("code") not in (None, 0, "0", 200, "200"):
            raise ValueError(str(inner.get("msg") or "平台拒绝保存"))
        return result


def make_client(cfg):
    from .cookie_parse import normalize_cookie
    raw = normalize_cookie(getattr(cfg, "shopee_cookies", "")
                           or getattr(cfg, "manual_cookies", {}))
    if not raw:
        from .browser_cookies import resolve
        source = resolve("dianxiaomi.com")
        raw = source.cookies if source.ok else {}
    if not raw:
        raise ValueError("请在「连接与店铺」粘贴店小秘 Cookie，或读取已登录的本机浏览器")
    return ShopeeClient(raw, mode=getattr(cfg, "shopee_mode", "global"),
                        timeout=int(getattr(cfg, "timeout", 30) or 30))

"""把商品详情转换为处理流水线需要的数据，保留 SKU 的原始行身份。"""
from copy import deepcopy
import json
import re


def sku_rows(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            value = []
    return [deepcopy(row) for row in (value or []) if isinstance(row, dict)]


def hydrate_product(product: dict, detail: dict) -> dict:
    result = deepcopy(product)
    result["raw"] = {**result.get("raw", {}), **deepcopy(detail)}
    result["title"] = result.get("title") or detail.get("subject") or ""
    result["detail"] = result.get("detail") or detail.get("detail") or ""
    result["category_id"] = result.get("category_id") or detail.get("categoryId")
    if not result.get("images"):
        urls = detail.get("imageURLs") or ""
        result["images"] = ([str(url) for url in urls] if isinstance(urls, list)
                            else [url.strip() for url in str(urls).split(";") if url.strip()])
    if not result.get("detail_images"):
        result["detail_images"] = list(dict.fromkeys(re.findall(
            r'<img[^>]+src=["\']([^"\']+)', result["detail"], re.I)))
    rows = sku_rows(detail.get("aeopAeProductSKUs") or detail.get("smtProductSkuList"))
    result["_source_sku_rows"] = rows
    if not result.get("skus"):
        result["skus"] = []
        for row in rows:
            item = deepcopy(row)
            item["_source_id"] = str(row.get("id") or row.get("skuId") or "")
            item["sku"] = str(row.get("skuCode") or row.get("sku") or "")
            item["original_sku"] = item["sku"]
            item["stock"] = row.get("ipmSkuStock", "")
            item["price"] = row.get("skuPrice", "")
            image = row.get("mobileModelSkuImage") or ""
            for prop in row.get("aeopSKUProperty") or []:
                if isinstance(prop, dict) and not image:
                    image = prop.get("imageUrlList") or ""
            if image:
                item["image"] = str(image).split(";")[0]
            result["skus"].append(item)
    return result

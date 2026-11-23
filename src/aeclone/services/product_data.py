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



# 采集数据里的 SKU 元数据：不是规格维度，解析前统一剔除。
_SKU_META = {
    "skuimage", "extraimages", "image", "img", "imageurllist",
    "skupropertyname", "propertyvaluename", "propertyvaluedefinitionname",
    "skuid", "sku", "id", "skupropertyid", "propertyvalueid",
    "parentpropertyid", "parentvalueid", "plug_parent_property_id",
    "plug_parent_value_id", "mobilemodelskuimage", "skupropertylist",
    "skucode", "currencycode", "skuprice", "ipmskustock", "skustock",
}


def _scalar_value(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    return bool(str(value).strip())


def clean_sku_row(row: dict) -> dict:
    """Remove metadata / empty / container values from one acquired SKU row."""
    result = deepcopy(row)
    props = row.get("aeopSKUProperty")
    if isinstance(props, list):
        cleaned = []
        for item in props:
            if not isinstance(item, dict):
                continue
            if "skuPropertyId" in item or "propertyValueId" in item:
                if _scalar_value(item.get("skuPropertyId")) and _scalar_value(item.get("propertyValueId")):
                    cleaned.append(deepcopy(item))
                continue
            named = {}
            for key, value in item.items():
                if str(key).strip().lower() in _SKU_META:
                    continue
                if _scalar_value(value):
                    named[key] = value
            if named:
                cleaned.append(named)
        result["aeopSKUProperty"] = cleaned
    for key in ("attributes", "prop"):
        value = result.get(key)
        if isinstance(value, dict):
            result[key] = {k: v for k, v in value.items()
                           if str(k).strip() and _scalar_value(v)}
        elif isinstance(value, list):
            result[key] = [v for v in value if _scalar_value(v)]
    return result


def clean_sku_rows(rows) -> list[dict]:
    return [clean_sku_row(row) for row in (rows or []) if isinstance(row, dict)]


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
    rows = clean_sku_rows(sku_rows(
        detail.get("aeopAeProductSKUs") or detail.get("smtProductSkuList")))
    # 把清洗后的行同步回 raw，后续依据身份匹配/重复合并都使用同一份数据。
    result["raw"]["aeopAeProductSKUs"] = deepcopy(rows)
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

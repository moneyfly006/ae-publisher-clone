"""Validate existing selling prices without using them as acquisition cost."""
import math

from .product_data import sku_rows


def existing_sku_prices(rows) -> list[float]:
    rows = sku_rows(rows)
    if not rows:
        raise ValueError("没有可保留的原始 SKU 售价")
    prices = []
    for row in rows:
        try:
            price = float(row.get("skuPrice"))
        except (TypeError, ValueError):
            price = 0
        if not math.isfinite(price) or not 0 < price <= 999999:
            raise ValueError("原始 SKU 售价缺失、无效或超过平台上限，不能自动保留")
        prices.append(price)
    return prices

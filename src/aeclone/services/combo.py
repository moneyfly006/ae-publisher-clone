"""套装 SKU：把主 SKU 展开成「套装」SKU（可预览，可单测）。"""
from __future__ import annotations


def _price_of(sku: dict) -> float | None:
    for key in ("price", "sale_price", "productPrice"):
        v = sku.get(key)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except Exception:
            continue
    return None


def expand(skus: list[dict], prefix: str = "套装", factor: float = 2.0,
           fallback_price: float | None = None) -> list[dict]:
    """返回「主 SKU + 套装 SKU」。

    - 名称：`{prefix}-{原SKU名}`；原 SKU 名为空时用序号
    - 价格：原 SKU 价 × factor；没有价格时用 fallback_price × factor（仍无则不带价格）
    - 库存：沿用原 SKU（`modify_stock_999` 等已在 _normalize 里处理）
    """
    base_list: list[dict] = []
    combos: list[dict] = []
    for i, sku in enumerate(skus or [], 1):
        base = dict(sku) if isinstance(sku, dict) else {"sku": str(sku)}
        base_list.append(base)
        combo = dict(base)
        name = str(base.get("sku") or base.get("name") or "").strip()
        combo["sku"] = f"{prefix}-{name}" if name else f"{prefix}-{i}"
        price = _price_of(base)
        if price is None and fallback_price is not None:
            try:
                price = float(fallback_price)
            except Exception:
                price = None
        if price is not None:
            combo["price"] = round(price * max(0.0, float(factor)), 2)
        combos.append(combo)
    return base_list + combos


def preview(skus: list[dict], prefix: str = "套装", factor: float = 2.0,
            fallback_price: float | None = None, limit: int = 6) -> list[str]:
    """给界面用的可读预览（每个 SKU 一行）。"""
    rows = []
    for sku in expand(skus, prefix, factor, fallback_price)[:limit]:
        price = sku.get("price")
        rows.append(f"{sku.get('sku') or '-'}" + (f" · ¥{price}" if price is not None else ""))
    return rows

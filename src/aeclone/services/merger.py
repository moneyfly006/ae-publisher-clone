"""组合商品：把多个产品合并成一个（图片合并、SKU 合并、标题合并）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MergeResult:
    product: dict
    notes: list[str]


def merge_products(products: list[dict], title_sep: str = " + ", max_images: int = 12) -> MergeResult:
    if not products:
        raise ValueError("没有可合并的产品")
    base = dict(products[0])
    images: list[str] = []
    skus: list[dict] = []
    titles: list[str] = []
    for p in products:
        titles.append((p.get("title") or "").strip())
        for u in (p.get("images") or []):
            if u not in images:
                images.append(u)
        for s in (p.get("skus") or []):
            name = str(s.get("sku") or "")
            tag = (p.get("title") or "")[:12]
            ns = dict(s)
            ns["sku"] = f"{tag}-{name}" if name else tag
            skus.append(ns)
    base["title"] = title_sep.join([t for t in titles if t])[:200]
    base["images"] = images[:max_images]
    base["skus"] = skus
    base["merged_from"] = [str(p.get("id")) for p in products]
    notes = [f"合并 {len(products)} 个产品", f"图片 {len(base['images'])} 张", f"SKU {len(skus)} 个"]
    return MergeResult(base, notes)

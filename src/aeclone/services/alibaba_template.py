"""Alibaba 模板产品：保存类目/必填属性/贸易字段快照，供采集箱商品补全。"""
from __future__ import annotations

import json
import time

from ..config import save_config


def _loads(raw, default):
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def load_snapshot(cfg) -> dict:
    return _loads(getattr(cfg, "alibaba_template_snapshot", "{}") or "{}", {})


def build_snapshot(client, product_id: str) -> dict:
    """读取真实模板商品，冻结类目/属性/贸易字段。"""
    p = client.product_detail(str(product_id)) or {}
    if not p:
        raise RuntimeError("模板商品读取为空")
    attrs = p.get("attributes") or []
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except Exception:
            attrs = []
    from ..integrations.alibaba import AlibabaClient
    save = AlibabaClient.build_save_dict(p)
    fields = {}
    for k in ("categoryId", "fullCid", "priceType", "bulkDiscountPrices",
              "wholesaleTrade", "salePkgDesc", "logisticsProperty", "shipmentPlace",
              "warehouseType", "marketSamplingQuantity", "marketSamplingPrice",
              "marketSample", "mainVideoId", "detailVideoId", "videoUrl", "video"):
        fields[k] = save.get(k) if save.get(k) not in (None, "") else p.get(k)
    return {
        "product_id": str(product_id),
        "title": str(p.get("subject") or p.get("title") or ""),
        "category_id": str(p.get("categoryId") or ""),
        "full_cid": str(p.get("fullCid") or ""),
        "category_name": str(p.get("categoryName") or p.get("nodePath") or ""),
        "attributes": attrs if isinstance(attrs, list) else [],
        "fields": fields,
        "saved_at": time.time(),
    }


def save_snapshot(cfg, snap: dict) -> None:
    cfg.set("alibaba_template_snapshot", json.dumps(snap or {}, ensure_ascii=False))
    cfg.set("alibaba_template_product_id", str((snap or {}).get("product_id") or ""))
    cfg.set("alibaba_template_product_name", str((snap or {}).get("title") or ""))
    save_config(cfg)


def snapshot_attrs_for_payload(snap: dict) -> list[dict]:
    """快照属性 → 保存包属性（只作为缺省值）。"""
    out = []
    for a in (snap or {}).get("attributes") or []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("attribute_name") or a.get("name") or "").strip()
        val = str(a.get("value_name") or a.get("value") or "").strip()
        if not name or not val:
            continue
        out.append({"attribute_id": str(a.get("attribute_id") or "-1"),
                    "attribute_name": name, "value_id": str(a.get("value_id") or "-1"),
                    "value_name": val})
    return out

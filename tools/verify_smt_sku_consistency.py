#!/usr/bin/env python3
"""只读复核速卖通(SMT)商品的 SKU 属性一致性（修复「属性键和值必须是非空标量」）。

用法::

    .venv/bin/python tools/verify_smt_sku_consistency.py --product-id 220114600917618096

流程：真实读取店小秘商品详情 → hydrate_product → recover_skus →
check_consistency；输出每个 SKU 的规格维度与问题；只读、不保存、不发布。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aeclone.config import AppConfig  # noqa: E402
from aeclone.integrations.dianxiaomi import DianxiaomiClient  # noqa: E402
from aeclone.services.product_data import hydrate_product  # noqa: E402
from aeclone.services.sku_recovery import recover_skus  # noqa: E402
from aeclone.services.variants import check_consistency  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--product-id", required=True)
    ap.add_argument("--output", default=str(ROOT / "docs" / "smt-sku-consistency.json"))
    args = ap.parse_args()
    cfg = AppConfig.load()
    cookies = dict(cfg.manual_cookies or {})
    if not cookies:
        print("缺少店小秘 Cookie（manual_cookies）")
        return 2
    dxm = DianxiaomiClient(cookies, overrides=cfg.endpoint_overrides or {})
    detail = dxm.product_detail(args.product_id)
    product = hydrate_product({"id": args.product_id}, detail)
    product, note = recover_skus(product, dxm)
    issues = check_consistency(product.get("skus") or [], [])
    rows = []
    for sku in product.get("skus") or []:
        dims = {k: v for k, v in (sku.get("attributes") or {}).items()} \
            if isinstance(sku.get("attributes"), dict) else {}
        rows.append({"sku": sku.get("sku") or sku.get("skuCode"),
                     "source": "aeopAeProductSKUs/smtProductSkuList",
                     "has_source_rows": bool(sku.get("aeopSKUProperty")
                                             or sku.get("skuPropertyList"))})
    report = {
        "product_id": args.product_id,
        "title": str((detail.get("product") or detail).get("subject") or "")[:120],
        "sku_count": len(product.get("skus") or []),
        "repair_note": note,
        "issues": [{"sku": i.sku, "reason": i.reason} for i in issues],
        "skus": rows,
        "ok": not issues,
        "read_only": True,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("product_id", "sku_count", "issues", "ok")},
                     ensure_ascii=False))
    print("证据：", out)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

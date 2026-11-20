"""Read-only query of the platform's current customs metadata; never saves products."""
from pathlib import Path
import json
import sys
import argparse
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aeclone.config import AppConfig
from aeclone.integrations.dianxiaomi import DianxiaomiClient
from aeclone.services.product_data import hydrate_product
from aeclone.services.publisher import Publisher
from aeclone.services.sku_recovery import recover_skus
from aeclone.services.customs_metadata import refreshed_customs


def verify_recovery(client, cfg):
    prior = json.loads((ROOT / "build/verification/live-data-diagnostic.json").read_text())
    report = dict(time=datetime.now().isoformat(), read_only=True, ai_calls=0,
                  platform_saves=0, products=[], customs=[])
    pub = Publisher.__new__(Publisher)
    pub.cfg = cfg
    for state in prior["task_states"]:
        raw = (client._get("product_detail", {"id": state["id"]}).get("data") or {}).get("product") or {}
        product = hydrate_product({"id": state["id"]}, raw)
        row = dict(id=state["id"], sku_count=len(product["_source_sku_rows"]))
        try:
            _, note = recover_skus(product, client, ROOT / "build/verification/native-config/sku-snapshots")
            row["sku"] = dict(status="recovered" if note else "valid", note=note)
        except ValueError as error:
            row["sku"] = dict(status="blocked", reason=str(error))
        try:
            priced = pub._price(product)
            row["price"] = dict(status=priced["price_result"].mode, note=priced["price_result"].note)
        except ValueError as error:
            row["price"] = dict(status="blocked", reason=str(error))
        report["products"].append(row)
    for state in prior["existing_failures"]:
        raw = (client._get("product_detail", {"id": state["id"]}).get("data") or {}).get("product") or {}
        row = dict(id=state["id"])
        try:
            value = refreshed_customs(raw, query=client.query_customs_metadata)
            row.update(status="refreshed" if value else "current", metadata=json.loads(value) if value else None)
        except ValueError as error:
            row.update(status="blocked", reason=str(error))
        report["customs"].append(row)
    (ROOT / "docs/live-auto-repair-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-recovery", action="store_true")
    args = parser.parse_args()
    cfg = AppConfig(json.loads((ROOT / "build/verification/native-config/config.json").read_text()))
    client = DianxiaomiClient(cfg.manual_cookies or {}, timeout=20, overrides=cfg.endpoint_overrides or {})
    if args.verify_recovery:
        return verify_recovery(client, cfg)
    pid = "184807709052714289"
    product = (client._get("product_detail", {"id": pid}).get("data") or {}).get("product") or {}
    old = json.loads(product.get("hsCode") or "{}")
    selected = [{"property_name": row["propertyName"], "value_list": [{"value_name": row["valueName"]}]}
                for row in old.get("pvList", []) if row.get("propertyName") and row.get("valueName")]
    response = client._request("post", client.base + "/api/smtCommProduct/getGlobalHsCodeById.json",
        params={"shopId": str(product.get("shopId") or cfg.store_id), "id": pid},
        json={"selected_pv_list": selected}, timeout=20)
    data = client._parse(response)
    report = dict(read_only=True, product_id=pid, category_id=product.get("categoryId"),
                  existing=old, selected=selected, response=data)
    (ROOT / "build/verification/current-customs-query.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

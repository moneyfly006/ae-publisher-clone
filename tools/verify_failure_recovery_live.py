"""Inspect live failure repairs and optionally save at most three corrected drafts."""
from pathlib import Path
from dataclasses import asdict
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aeclone.config import AppConfig, get_config
from aeclone.services.collect_automation import client_for, isolated_config, identity, process_one
from aeclone.services.listing_failures import classify
from aeclone.services.listing_recovery import amazon_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-drafts", action="store_true")
    args = parser.parse_args()
    inspection = json.loads((ROOT / "build/publish-failures-inspection.json").read_text())
    report = {}
    for platform, data in inspection.items():
        cfg = AppConfig(get_config().as_dict())
        cookie = "reference-cookie.txt" if platform == "alibaba" else "amazon-reference-cookie.txt"
        cfg.set(platform + "_cookies", (ROOT / "build" / cookie).read_text().strip())
        config = isolated_config(cfg, platform)
        client = client_for(platform, cfg)
        records = []
        saves = 0
        for row in data.get("records", []):
            path = ROOT / "build" / f"{platform}-failed-detail-{row['id']}.json"
            detail = json.loads(path.read_text())
            product = detail.get("product", detail)
            error = str(product.get("errMsg") or "")
            findings = classify(platform, error)
            rec = dict(id=row["id"], findings=[asdict(f) for f in findings], saved=False)
            if platform == "amazon" and not all(f.kind == "reviewing" for f in findings):
                # Fetch latest again before planning a possible write.
                latest = client.product_detail(row["id"])
                product = latest.get("product", latest)
                plan = amazon_plan(product, config, error=product.get("errMsg") or "")
                rec.update(actions=plan["actions"], blockers=plan["blockers"], status=plan["status"])
                checked = process_one(platform, row["id"], config, client, plan["overrides"],
                                      dry_run=True, op="1", use_ai=False)
                rec.update(draft_precheck_ok=bool(checked.ok), precheck_error=checked.error)
                if args.save_drafts and checked.ok and plan["actions"] and saves < 3:
                    result = process_one(platform, row["id"], config, client, plan["overrides"],
                                         dry_run=False, op="1", use_ai=False)
                    rec.update(saved=bool(result.saved), save_ok=bool(result.ok), save_error=result.error)
                    if result.saved:
                        saves += 1
                        latest = client.product_detail(row["id"])["product"]
                        rec["readback_id_matches"] = identity(latest) == row["id"]
                        rec["preserved"] = {key: latest.get(key) == product.get(key) for key in
                            ("standardPrice", "quantity", "mainImage", "categoryId", "standardProductId")}
                        attrs = json.loads(latest.get("productAttr") or "{}")
                        rec["title_schema_synced"] = attrs.get("item_name", [{}])[0].get("value") == latest.get("title")
                        rec["state"] = latest.get("dxmOfflineState") or latest.get("dxmState")
                        rec["remaining_platform_error"] = latest.get("errMsg") or ""
            records.append(rec)
        report[platform] = records
    path = ROOT / "build" / ("failure-recovery-live-save.json" if args.save_drafts else "failure-recovery-live-check.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

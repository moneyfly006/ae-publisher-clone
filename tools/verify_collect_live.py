"""Bounded account-specific collection verification. Never submits a listing online."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aeclone.config import get_config, AppConfig
from aeclone.services.collect_automation import client_for, run_collect, snapshot, isolated_config, identity
from aeclone.core.guard import check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("alibaba", "amazon"))
    parser.add_argument("--save-one-draft", action="store_true")
    parser.add_argument("--generated-bullets", action="store_true", help="使用已通过四平台AI实测的五点文案保存单件Amazon草稿")
    args = parser.parse_args()
    platform = args.platform
    cookie_file = ROOT / "build" / ("reference-cookie.txt" if platform == "alibaba" else "amazon-reference-cookie.txt")
    cfg = AppConfig(get_config().as_dict())
    cfg.set(platform + "_cookies", cookie_file.read_text().strip())
    cfg.set(platform + "_collect_ai", False)
    cfg.set(platform + "_collect_overrides", "{}")
    cfg.set(platform + "_collect_op", "1")
    cfg.set(platform + "_collect_limit", 3)
    # Credentials only go to the client's existing fixed service host.
    client = client_for(platform, cfg)
    report = {"platform": platform, "mode": "save-one-draft" if args.save_one_draft else "read-only", "errors": []}
    try:
        shops = client.shops()
        report["shops"] = [{"id": str(s.get("id") or s.get("shopId") or ""),
                            "name": str(s.get("name") or s.get("shopName") or "")} for s in shops]
        if shops:
            cfg.set(platform + "_shop_id", str(shops[0].get("id") or shops[0].get("shopId") or ""))
        rows = snapshot(client, platform, limit=3)
        report["source_count_sample"] = len(rows)
        report["ids"] = [identity(r) for r in rows]
        report["precheck"] = run_collect(cfg, platform, dry_run=True, client=client, rows=rows)
        report["license"] = dict(zip(("ok", "reason"), check(cfg)))
        if args.save_one_draft:
            eligible = {r["id"] for r in report["precheck"] if r["ok"]}
            chosen = next((r for r in rows if identity(r) in eligible), None)
            if chosen:
                before = client.product_detail(identity(chosen))
                (ROOT / "build" / f"{platform}-collect-before.json").write_text(json.dumps(before, ensure_ascii=False, indent=2))
                if args.generated_bullets:
                    if platform != "amazon":
                        raise ValueError("生成五点保存仅适用于Amazon")
                    product = before.get("product", before)
                    if str(product.get("dxmState")) != "draft":
                        raise ValueError("只允许对采集箱草稿执行本次AI回填验证")
                    generated = json.loads((ROOT / "build/amazon-verified-ai-copy.json").read_text())
                    if len(generated.get("bullets", [])) != 5:
                        raise ValueError("已生成五点内容不完整")
                    cfg.set("amazon_collect_overrides", json.dumps(dict(bullets=generated["bullets"]), ensure_ascii=False))
                report["save"] = run_collect(cfg, platform, dry_run=False, client=client, rows=[chosen])
                after = client.product_detail(identity(chosen))
                (ROOT / "build" / f"{platform}-collect-after.json").write_text(json.dumps(after, ensure_ascii=False, indent=2))
                before = before.get("product", before)
                after = after.get("product", after)
                report["preserved"] = {k: before.get(k) == after.get(k) for k in
                    ("categoryId", "mainImage", "imageUrls", "originalPrice", "standardPrice", "quantity", "variationList")
                    if k in before}
                if args.generated_bullets:
                    attrs = json.loads(after.get("productAttr") or "{}")
                    report["ai_bullets_roundtrip"] = (after.get("bulletPoint", "").split("±") == generated["bullets"]
                        and [r["value"] for r in attrs.get("bullet_point", [])] == generated["bullets"])
            else:
                report["errors"].append("没有通过预检的商品，未执行写入")
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    output = ROOT / "build" / f"{platform}-collect-live{'-ai-save' if args.generated_bullets else '-save' if args.save_one_draft else ''}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

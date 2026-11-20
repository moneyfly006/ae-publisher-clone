"""Read-only publication monitoring. Never submits, retries or deletes products."""
from pathlib import Path
import argparse
import datetime
import json
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aeclone.config import AppConfig
from aeclone.integrations.dianxiaomi import DianxiaomiClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "build/verification/native-config")
    parser.add_argument("--since", default="2026-11-20 23:52:48")
    parser.add_argument("--watch", type=int, default=0)
    parser.add_argument("--interval", type=int, default=20)
    parser.add_argument("--inspect-skus", action="store_true")
    parser.add_argument("--inspect-customs", action="store_true")
    args = parser.parse_args()
    output = ROOT / "build/verification/publication-monitor.jsonl"
    started = time.monotonic()
    previous = None
    last_line = ""
    while True:
        cfg = AppConfig(json.loads((args.config / "config.json").read_text()))
        client = DianxiaomiClient(cfg.manual_cookies or {}, timeout=15, overrides=cfg.endpoint_overrides or {})
        secrets = [str(value) for value in (cfg.manual_cookies or {}).values() if len(str(value)) > 5]
        secrets += [str(cfg.api_key or "")]
        def redact(value):
            text = str(value)
            for secret in secrets:
                if secret: text = text.replace(secret, "[redacted]")
            return re.sub(r'https?://\S+', '[url]', text)
        lines = (args.config / "logs/app.log").read_text(errors="replace").splitlines()
        current = [line for line in lines if line[:19] >= args.since]
        ids = list(dict.fromkeys(re.findall(r'\[\d+/\d+\]\s+(\d{8,})', "\n".join(current))))
        result = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "read_only": True,
                  "task_ids": ids, "preview_seen": any("预演:" in line for line in current)}
        try:
            failed, failed_meta = client.product_page(1, 100, cfg.store_id, "publishFail")
            result["failed_total"] = failed_meta.get("total")
            result["existing_failures"] = []
            targets = list(dict.fromkeys(ids + [str(p["id"]) for p in failed[:10]]))
            result["task_states"] = []
            for pid in targets:
                response = client._get("product_detail", {"id": pid})
                product = (response.get("data") or {}).get("product")
                if not isinstance(product, dict) or not product:
                    state = dict(id=pid, status="UNKNOWN", error="商品详情未返回，不能确认发布状态")
                else:
                    reason = product.get("errMsg") or product.get("auditFailureReason") or ""
                    state = dict(id=pid, status=str(product.get("productStatusType") or "draft"),
                                 error=redact(reason)[:1200])
                    if args.inspect_customs:
                        state["category_id"] = product.get("categoryId")
                        state["hsCode"] = product.get("hsCode")
                    if pid in ids:
                        state["price_fields"] = {key: product.get(key) for key in
                            ("sourcePrice", "productPrice", "currencyCode", "sourceCurrency", "sourcePriceUsdMin",
                             "sourcePriceUsdMax", "sourcePriceUsd", "sourceDiscountPriceUsd",
                             "productMinPrice", "productMaxPrice", "price", "goodsPrice") if key in product}
                        skus = product.get("aeopAeProductSKUs") or []
                        if isinstance(skus, str):
                            try: skus = json.loads(skus)
                            except ValueError: skus = []
                        state["sku_count"] = len(skus) if isinstance(skus, list) else 0
                        state["sku_prices"] = [row.get("skuPrice") for row in skus[:5]] if isinstance(skus, list) else []
                        if args.inspect_skus:
                            state["sku_identity"] = [{key: row.get(key) for key in
                                ("id", "skuId", "skuCode", "aeopSKUProperty")} for row in skus]
                            state["detail_field_names"] = list(product)
                            alternate = product.get("smtProductSkuList") or []
                            if isinstance(alternate,str):
                                try: alternate=json.loads(alternate)
                                except ValueError: alternate=[]
                            state["alternate_sku_rows"] = [{key:row.get(key) for key in
                                ("id","skuId","skuCode","aeopSKUProperty","attributes","skuAttr","skuPrice")}
                                for row in alternate if isinstance(row,dict)] if isinstance(alternate,list) else []
                result["task_states" if pid in ids else "existing_failures"].append(state)
            if args.inspect_customs and cfg.template_product_id:
                template=(client._get("product_detail", {"id":cfg.template_product_id}).get("data") or {}).get("product") or {}
                result["template_customs"]={"category_id":template.get("categoryId"),"hsCode":template.get("hsCode")}
        except Exception as error:
            result["connection_error"] = redact(f"{type(error).__name__}: {error}")[:400]
        selected = [redact(line) for line in current if "[ERROR]" in line or "[WARN]" in line or
                    "完成：" in line or "预演完成" in line or "保存到店小秘:" in line]
        result["recent_results"] = selected[-20:]
        signature = json.dumps({key: value for key, value in result.items() if key != "time"}, ensure_ascii=False, sort_keys=True)
        if signature != previous:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a") as stream: stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            previous = signature
        if time.monotonic() - started >= args.watch: break
        time.sleep(max(5, args.interval))
    return 0

if __name__ == "__main__": raise SystemExit(main())

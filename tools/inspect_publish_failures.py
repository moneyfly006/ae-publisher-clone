"""Read failed listings and exact platform errors; never writes or republishes."""
from pathlib import Path
from collections import Counter
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aeclone.config import AppConfig, get_config
from aeclone.services.collect_automation import client_for, identity


def read_failed(client, platform):
    selected, seen, signatures = [], set(), set()
    for page in range(1, 101):
        if platform == "alibaba":
            response = client.list_products(page, 100, "publishFail")
            data = ((response.get("data") or {}).get("page") or {})
            rows, pages, total = data.get("list") or [], data.get("totalPage"), data.get("totalSize")
        else:
            data = client.list_products("publishFail", page, 100, "-1", "")
            rows, pages, total = data.get("items") or [], data.get("total_page"), data.get("total")
        signature = tuple(identity(r) for r in rows)
        if rows and signature in signatures:
            raise ValueError("发布失败接口重复分页，未完成读取")
        signatures.add(signature)
        for row in rows:
            pid = identity(row)
            if pid and pid not in seen:
                seen.add(pid)
                selected.append(row)
        if not rows or (pages and page >= int(pages)) or (total is not None and len(seen) >= int(total)):
            return selected
    raise ValueError("失败商品页数超过读取上限")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("alibaba", "amazon", "all"), default="all")
    parser.add_argument("--details", action="store_true")
    args = parser.parse_args()
    output = {}
    for platform in (("alibaba", "amazon") if args.platform == "all" else (args.platform,)):
        cfg = AppConfig(get_config().as_dict())
        cookie = ROOT / "build" / ("reference-cookie.txt" if platform == "alibaba" else "amazon-reference-cookie.txt")
        cfg.set(platform + "_cookies", cookie.read_text().strip())
        try:
            client = client_for(platform, cfg)
            rows = read_failed(client, platform)
            raw_file = ROOT / "build" / (platform + "-failed-listings.json")
            raw_file.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            raw_file.chmod(0o600)
            records = []
            for row in rows:
                error = str(row.get("err_msg") or row.get("errMsg") or row.get("errorMsg") or row.get("failReason") or "")
                records.append({"id": identity(row), "title": str(row.get("subject") or row.get("title") or ""), "error": error})
                if args.details:
                    detail = client.product_detail(identity(row))
                    detail_file = ROOT / "build" / f"{platform}-failed-detail-{identity(row)}.json"
                    detail_file.write_text(json.dumps(detail, ensure_ascii=False, indent=2))
                    detail_file.chmod(0o600)
                    product = detail.get("product", detail)
                    records[-1]["list_error"] = error
                    records[-1]["error"] = str(product.get("errMsg") or product.get("err_msg") or error)
                    records[-1]["state"] = str(product.get("dxmOfflineState") or product.get("dxmState") or "")
            output[platform] = {"count": len(rows), "error_groups": dict(Counter(r["error"] for r in records)), "records": records}
        except Exception as exc:
            output[platform] = {"error": str(exc)}
    path = ROOT / "build" / "publish-failures-inspection.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

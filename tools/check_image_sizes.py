#!/usr/bin/env python3
"""图片尺寸体检工具（不影响任何线上数据）。

用法：
    # 1) 查一个产品 JSON（含 images 字段）
    python tools/check_image_sizes.py product.json

    # 2) 查一堆 URL（每行一个）
    python tools/check_image_sizes.py urls.txt --urls

    # 3) 查一批产品（JSON 数组）
    python tools/check_image_sizes.py products.json --batch

    # 自定义阈值（默认 800）
    python tools/check_image_sizes.py product.json --min 1000
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aeclone.services.image_validation import (  # noqa: E402
    audit_products, summarize, validate_image_size,
)


def _load(path: Path):
    txt = path.read_text(encoding="utf-8")
    if txt.lstrip()[:1] in "[{":
        return json.loads(txt)
    return [ln.strip() for ln in txt.splitlines() if ln.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description="检查图片尺寸是否 ≥800×800")
    ap.add_argument("input", help="产品 JSON / 产品数组 JSON / URL 列表文件")
    ap.add_argument("--min", type=int, default=800, help="最小边长，默认 800")
    ap.add_argument("--urls", action="store_true", help="输入是 URL 列表")
    ap.add_argument("--batch", action="store_true", help="输入是产品数组")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    p = Path(args.input).expanduser()
    if not p.exists():
        print(f"文件不存在：{p}", file=sys.stderr)
        return 2
    data = _load(p)

    if args.urls:
        rows = []
        for u in data:
            ok, info = validate_image_size(u, args.min, args.min)
            rows.append({"url": u, "ok": ok, "info": info})
        passed = sum(1 for r in rows if r["ok"])
        failed = len(rows) - passed
        if args.json:
            print(json.dumps({"passed": passed, "failed": failed, "rows": rows},
                             ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f"{'✓' if r['ok'] else '✗'} {r['info']:>14}  {r['url']}")
            print(f"\n合格 {passed} / 不合格 {failed}")
        return 0 if failed == 0 else 1

    products = data if args.batch else [data]
    res = audit_products(products, args.min)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        for pr in res["products"]:
            flag = "✓" if not pr["bad"] else "✗"
            print(f"{flag} [{pr['id']}] {pr['title']}  合格 {pr['ok']}/{pr['checked']}")
            for idx, url, info in pr["bad"]:
                print(f"     第{idx}张 {info}  {url}")
        print(f"\n共 {res['total']} 张：合格 {res['passed']} / 不合格 {res['failed']}")
    return 0 if res["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

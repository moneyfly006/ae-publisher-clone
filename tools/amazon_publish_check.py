"""Amazon 真实发布核验（Lead 专用，默认只读；真实发布必须 --allow-write）。

流程（每个商品）：
  1) 独立只读快照（editSp + 5 个列表状态，看发布前所在位置）；
  2) 预演（run_amazon_product dry_run=True，含必填属性自动补齐 + 校验，不写）；
  3) （--allow-write）op=2 保存并发布 → 轮询发布结果（verify_publish）；
  4) 再次独立只读核查（发布中/发布失败/在线/待发布/采集箱）与 errMsg 分类；
  5) 证据追加 docs/amazon-publish-verification.json。

用法：
  .venv/bin/python tools/amazon_publish_check.py --product-id 220114595363182310 --price 8900 --quantity 2
  AURORA_SKIP_LICENSE=1 .venv/bin/python tools/amazon_publish_check.py \
      --product-id 220114595363182310,220114595363182036 --price 8900 --quantity 2 \
      --allow-write --note "publish test #1"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
EVIDENCE = ROOT / "docs" / "amazon-publish-verification.json"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _append(record: dict) -> None:
    data = {"generated_at": _now(), "runs": []}
    if EVIDENCE.exists():
        try:
            data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
            data.setdefault("runs", [])
        except Exception:
            pass
    data["generated_at"] = _now()
    data["runs"].append(record)
    writes = sum(len([r for r in run.get("products", []) if r.get("write")])
                 for run in data["runs"])
    data["summary"] = {"runs": len(data["runs"]), "product_writes": writes,
                       "last_ok": bool(record.get("ok"))}
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _required(err: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"属性:([a-z0-9_]+)", str(err or ""))))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Amazon 真实发布核验（默认只读）")
    ap.add_argument("--product-id", required=True, help="逗号分隔的多个商品 id")
    ap.add_argument("--price", type=float, default=0.0)
    ap.add_argument("--quantity", type=int, default=2)
    ap.add_argument("--op", default="2", choices=["2", "3", "1"])
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--desc-file", default="", help="可选：description 覆盖文件（JSON: {value: ...} 或纯文本）")
    ap.add_argument("--title", default="", help="可选：覆盖标题（如去除单词重复）")
    ap.add_argument("--required-attrs", default="",
                    help="可选：逗号分隔的必填属性名（来自上次发布失败 errMsg，第二轮补齐）")
    ap.add_argument("--category-id", default="", help="可选：覆盖类目 id（修复类目错配）")
    ap.add_argument("--category-path", default="", help="可选：覆盖类目 nodePath")
    ap.add_argument("--note", default="")
    ap.add_argument("--wait", type=int, default=0, help="发布后轮询秒数（0=用 cfg.amazon_publish_wait）")
    args = ap.parse_args(argv)

    try:
        from aeclone.config import get_config
        from aeclone.services import amazon_pipeline
        from aeclone.services.amazon_publish import make_client
    except Exception as exc:  # noqa: BLE001
        print("后端未就绪：", exc)
        return 2

    cfg = get_config()
    client = make_client(cfg)
    pids = [p.strip() for p in str(args.product_id).split(",") if p.strip()]
    desc_override = ""
    if args.desc_file and Path(args.desc_file).exists():
        raw = Path(args.desc_file).read_text(encoding="utf-8")
        try:
            desc_override = str(json.loads(raw).get("value") or "")
        except Exception:
            desc_override = raw
    wait = int(args.wait or getattr(cfg, "amazon_publish_wait", 60) or 60)

    run_rec = {"when": _now(), "mode": "publish" if args.allow_write else "dry",
               "op": args.op, "note": args.note, "write": bool(args.allow_write),
               "products": []}
    ok_all = True

    for pid in pids:
        rec = {"product_id": pid, "write": False, "ok": False}
        try:
            before = amazon_pipeline.verify_publish(pid, cfg, client=client, wait_seconds=0)
            rec["before"] = {k: before.get(k) for k in
                             ("state", "status", "err_msg", "asin", "counts")}
            detail = client.product_detail(pid)
            product = detail.get("product") or {}
            rec["title"] = str(product.get("title") or "")[:160]
            rec["category_id"] = product.get("categoryId")
            rec["before_required_attrs"] = _required(str(rec["before"].get("err_msg") or ""))

            ov = {"standard_price": float(args.price or 0) or None,
                  "quantity": int(args.quantity or 0)}
            if args.title:
                ov["title"] = str(args.title)
            if desc_override:
                ov["description"] = desc_override
            if args.required_attrs:
                ov["required_attributes"] = [x.strip() for x in
                                             str(args.required_attrs).split(",") if x.strip()]
            if args.category_id:
                ov["category_id"] = str(args.category_id)
            if args.category_path:
                ov["category_path"] = str(args.category_path)
            dry = amazon_pipeline.run_amazon_product(pid, cfg, overrides=ov,
                                                     dry_run=True, use_ai=False,
                                                     op=str(args.op))
            rec["preview_ok"] = bool(dry.ok)
            rec["preview_issues"] = list(dry.issues or [])
            rec["preview_notes"] = list(getattr(dry, "attribute_notes", []) or [])
            print(f"\n== {pid} 预演: {'PASS' if dry.ok else 'FAIL'} | issues={dry.issues}"
                  f" | notes={rec['preview_notes'][:4]}", flush=True)
            print(dry.log(), flush=True)
            if not dry.ok:
                rec["error"] = "预演未通过"
                ok_all = False
                run_rec["products"].append(rec)
                continue

            if not args.allow_write:
                rec["ok"] = True
                run_rec["products"].append(rec)
                continue

            rec["write"] = True
            print(f"真实发布 {pid} op={args.op} price={ov['standard_price']} qty={ov['quantity']}…",
                  flush=True)
            res = amazon_pipeline.run_amazon_product(pid, cfg, overrides=ov,
                                                     dry_run=False, use_ai=False,
                                                     op=str(args.op), confirm_write=True)
            rec["saved"] = bool(res.saved)
            rec["save_ok"] = bool(res.ok)
            rec["save_result"] = {k: str(v)[:200] for k, v in (res.save_result or {}).items()}
            rec["publish_state"] = str(getattr(res, "publish_state", "") or "")
            rec["publish_detail"] = str(getattr(res, "publish_detail", "") or "")
            rec["publish_error"] = str(getattr(res, "publish_error", "") or "")
            rec["publish_asin"] = str(getattr(res, "publish_asin", "") or "")
            rec["required_attrs"] = list(getattr(res, "required_attrs", []) or [])
            print(f"  保存={'OK' if res.saved else 'FAIL'} | 发布状态={rec['publish_state'] or '-'}"
                  f" | {rec['publish_detail'][:160]}", flush=True)
            # 独立复查（再次只读）
            after = amazon_pipeline.verify_publish(pid, cfg, client=client,
                                                   wait_seconds=max(0, wait if not
                                                                    rec["publish_state"] else 0))
            rec["after"] = {k: after.get(k) for k in
                            ("state", "status", "ok", "detail", "err_msg", "asin", "counts")}
            rec["after_required_attrs"] = _required(str(rec["after"].get("err_msg") or ""))
            print(f"  复查: state={rec['after'].get('state')} status={rec['after'].get('status')}"
                  f" detail={str(rec['after'].get('detail'))[:140]}", flush=True)
            rec["ok"] = bool(rec["saved"] and rec["after"].get("status") in
                             ("online", "reviewing", "processing", "failed"))
            if not rec["ok"]:
                ok_all = False
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {exc}"[:400]
            ok_all = False
            print(f"  {pid} 失败：{rec['error']}", flush=True)
        run_rec["products"].append(rec)

    run_rec["ok"] = ok_all
    _append(run_rec)
    print("\n证据：", EVIDENCE)
    print("结论：", "OK" if ok_all else "有失败/未完成项（见证据）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())

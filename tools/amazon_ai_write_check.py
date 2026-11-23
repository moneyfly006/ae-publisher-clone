"""Amazon AI 文案真实编辑核验（Lead 专用，默认只读）。

写入护栏：真实分支必须同时 `--allow-write` 且调用 `run_amazon_product(confirm_write=True)`；
否则 pipeline 会在连接前拒绝（2026-11-22 事故后加固）。

流程：读采集箱商品 → AI 生成标题/五点/描述/关键词 → 组装 overrides → 预演校验
     →（--allow-write）op=1 真实保存 → 回读 editSp 逐字段对比 → 证据入库。

安全：
- 默认只预演，不写、不花钱（--ai-file 可复用已生成文案，避免重复计费）；
- 真实写必须 --allow-write，且只允许 op=1/3（op=2 发布 v1 不测）；
- 唯一写入口是 services.amazon_pipeline.run_amazon_product(dry_run=False) → save_to_amazon；
- 每次写前后记录 editSp 快照；证据追加 docs/amazon-write-verification.json。

用法：
  .venv/bin/python tools/amazon_ai_write_check.py                 # 只预演
  AURORA_SKIP_LICENSE=1 .venv/bin/python tools/amazon_ai_write_check.py \
      --allow-write --op 1 --note "AI edit #2"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVIDENCE = ROOT / "docs" / "amazon-write-verification.json"
AI_CACHE = ROOT / ".tmp-probe" / "amazon_ai_text.json"
DEFAULT_PID = "220114595363182310"


def _num(v, default=0):
    try:
        return int(float(str(v)))
    except Exception:
        return default


def _append_evidence(record: dict) -> None:
    data = {"generated_at": _dt.datetime.now().isoformat(timespec="seconds"), "runs": []}
    if EVIDENCE.exists():
        try:
            data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
            data.setdefault("runs", [])
        except Exception:
            pass
    data["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    data["runs"].append(record)
    data["summary"] = {
        "runs": len(data["runs"]),
        "writes": sum(1 for r in data["runs"] if r.get("write")),
        "last_ok": bool(record.get("ok")),
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Amazon AI 文案编辑核验（默认只读）")
    ap.add_argument("--product-id", default=DEFAULT_PID)
    ap.add_argument("--op", default="1", choices=["1", "3"])
    ap.add_argument("--allow-write", action="store_true")
    ap.add_argument("--ai-file", default=str(AI_CACHE),
                    help="AI 文案缓存 JSON（存在则复用，避免重复付费）")
    ap.add_argument("--regenerate", action="store_true", help="强制重新调用 AI")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    pid = str(args.product_id)

    try:
        from aeclone.config import get_config
        from aeclone.services import amazon_pipeline
        from aeclone.services.amazon_publish import make_client
    except Exception as exc:  # noqa: BLE001
        print("后端未就绪：", exc)
        return 2

    cfg = get_config()
    client = make_client(cfg)
    record = {"when": _dt.datetime.now().isoformat(timespec="seconds"),
              "kind": "ai_edit", "mode": "write" if args.allow_write else "dry",
              "product_id": pid, "op": args.op, "note": args.note,
              "write": False, "ok": False}
    ai_file = Path(args.ai_file)

    try:
        if not client.check_login():
            raise RuntimeError("登录失效")
        detail = client.product_detail(pid)
        product = (detail or {}).get("product") or {}
        if not product:
            raise RuntimeError("读不到商品")
        record["account"] = str(getattr(cfg, "amazon_shop_id", "") or "")
        record["site"] = detail.get("site")
        record["category_id"] = product.get("categoryId")
        record["before_title"] = str(product.get("title") or "")
        record["before_bullets"] = len(
            [b for b in str(product.get("bulletPoint") or "").split("±") if b.strip()])

        # ---- AI 文案（可复用缓存）----
        text = None
        if ai_file.exists() and not args.regenerate:
            try:
                cached = json.loads(ai_file.read_text(encoding="utf-8"))
                if cached.get("product_id") == pid and cached.get("text"):
                    text = cached["text"]
                    record["ai_cache"] = str(ai_file)
            except Exception:
                text = None
        if not text:
            mp = str(product.get("marketPlaceId") or "")
            print("调用 AI 生成文案（付费）…")
            text, notes = amazon_pipeline.ai_generate_all(product, cfg, mp, count=5)
            record["ai_notes"] = notes
            ai_file.parent.mkdir(parents=True, exist_ok=True)
            ai_file.write_text(json.dumps({"product_id": pid, "text": text,
                                           "generated_at": _dt.datetime.now().isoformat(
                                               timespec="seconds")},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
        record["ai_title"] = str(text.get("title") or "")
        record["ai_bullets"] = len(text.get("bullets") or [])

        overrides = {
            "title": str(text.get("title") or ""),
            "bullets": [str(b) for b in (text.get("bullets") or [])],
            "description": str(text.get("description") or ""),
            "search_terms": str(text.get("search_terms") or ""),
        }
        record["overrides_summary"] = {
            "title_chars": len(overrides["title"]),
            "bullets": len(overrides["bullets"]),
            "desc_chars": len(overrides["description"]),
            "st_bytes": len(overrides["search_terms"].encode("utf-8")),
        }

        # ---- 预演（只读）----
        dry = amazon_pipeline.run_amazon_product(pid, cfg, overrides=overrides,
                                                 dry_run=True, use_ai=False, op=args.op)
        record["preview_ok"] = bool(dry.ok)
        record["preview_issues"] = list(dry.issues or [])
        record["preview_warnings"] = list(dry.warnings or [])[:8]
        print("预演：", "PASS" if dry.ok else "FAIL", "| issues:", dry.issues)
        print(dry.log())
        if not dry.ok:
            record["error"] = "预演未通过"
            _append_evidence(record)
            return 1

        if not args.allow_write:
            record["ok"] = True
            _append_evidence(record)
            print("只读预演完成（未写数据、未改动商品）。证据：", EVIDENCE)
            return 0

        # ---- 真实写入（唯一收口）----
        record["write"] = True
        print(f"真实写入：op={args.op}（run_amazon_product → save_to_amazon）…")
        res = amazon_pipeline.run_amazon_product(pid, cfg, overrides=overrides,
                                                 dry_run=False, use_ai=False,
                                                 op=args.op, confirm_write=True)
        record["saved"] = bool(res.saved)
        record["save_ok"] = bool(res.ok)
        record["result_error"] = str(res.error or "")
        record["save_result"] = {k: str(v)[:200] for k, v in (res.save_result or {}).items()}
        print("保存：", "OK" if res.saved else "FAIL", "| error:", res.error)
        if not res.saved:
            _append_evidence(record)
            return 1

        # ---- 回读对比 ----
        after_detail = client.product_detail(pid)
        after = (after_detail or {}).get("product") or {}
        checks = {}
        expect = overrides
        checks["title"] = str(after.get("title") or "").strip() == expect["title"].strip()
        got_bullets = [b.strip() for b in str(after.get("bulletPoint") or "").split("±")
                       if b.strip()]
        checks["bullets"] = got_bullets == [b.strip() for b in expect["bullets"]]
        checks["bullets_count"] = len(got_bullets)
        got_st = str(after.get("searchTerms") or "").strip()
        checks["search_terms"] = got_st == expect["search_terms"].strip()
        got_desc = str(after.get("description") or "")
        checks["description_nonempty"] = bool(got_desc.strip())
        checks["description_changed"] = got_desc.strip() != str(product.get("description") or "").strip()
        record["readback"] = {
            "title": str(after.get("title") or ""),
            "bullets": got_bullets,
            "search_terms": got_st,
            "description_chars": len(got_desc),
        }
        record["checks"] = checks
        record["ok"] = all(v is True for k, v in checks.items()
                           if k in ("title", "bullets", "search_terms",
                                    "description_nonempty", "description_changed"))
        for k, v in checks.items():
            print(f"  {'✓' if v else '✗'} {k}: {v}")
        if not record["ok"]:
            record["error"] = "回读内容与写入不一致"
        _append_evidence(record)
        print("证据已写入", EVIDENCE)
        return 0 if record["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"[:400]
        _append_evidence(record)
        print("失败：", record["error"])
        return 1


if __name__ == "__main__":
    sys.exit(main())

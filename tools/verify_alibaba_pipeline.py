"""Alibaba 刊登流水线真实环境核验：只读 + dry-run（绝不保存/发布）。

做什么
------
1. 用真实配置里的 Cookie（cfg.alibaba_cookies → cfg.manual_cookies → 本机浏览器）创建客户端；
2. 只读：店铺草稿列表、草稿详情、类目 required attrs / schema、违禁词检查；
3. 选一个草稿跑 ``run_alibaba_product(..., dry_run=True, use_ai=False)``，打印每一步与 issues；
4. 对少量草稿做「本地构建 + 校验」体检（脏类目/脏 SKU 会被拦截）；
5. 输出 JSON 到 docs/alibaba-pipeline-verification.json。

安全
----
- ``dry_run=True`` 且进程内强制 ``AURORA_NO_SAVE=1``；用只读包装客户端把 save_product
  变成会抛错的哨兵——只要流水线试图保存，核验立即失败。
- 不调用付费 AI（use_ai=False），不打印 Cookie/api_key。
- Cookie 不可用/已过期时输出 ``status="SKIP"`` 并以 0 退出（不是失败）。

跑法
----
``.venv/bin/python tools/verify_alibaba_pipeline.py``
可选：``--product-id`` / ``--shop-id`` / ``--limit`` / ``--audit-limit`` / ``--output``
``--config-dir``（用隔离配置目录，默认读真实 ~/Library/Application Support/AuroraList）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_OUTPUT = ROOT / "docs" / "alibaba-pipeline-verification.json"


class ReadOnlyClient:
    """只读代理：任何 save_product 调用都视为核验失败。"""

    def __init__(self, inner):
        self._inner = inner
        self.save_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def save_product(self, *args, **kwargs):  # noqa: D401
        self.save_calls += 1
        raise AssertionError("只读核验模式禁止调用 save_product")


def _stamp() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _cookie_source(cfg) -> str:
    if str(getattr(cfg, "alibaba_cookies", "") or "").strip():
        return "alibaba_cookies"
    if getattr(cfg, "manual_cookies", None):
        return "manual_cookies"
    return "browser"


def _looks_like_cookie_issue(msg: str) -> bool:
    low = str(msg or "").lower()
    return any(k in low for k in ("cookie", "登录", "未登录", "login", "auth",
                                  "401", "403", "forbidden", "unauthorized",
                                  "非 json", "html"))


def _product_brief(p: dict) -> dict:
    return {
        "product_id": str(p.get("id") or ""),
        "subject": str(p.get("subject") or "")[:120],
        "category_id": str(p.get("categoryId") or ""),
        "full_cid": str(p.get("fullCid") or ""),
        "price_type": str(p.get("priceType") or ""),
        "shop_id": str(p.get("shopId") or ""),
        "has_main_image": bool(str(p.get("mainImage") or "").strip()),
        "has_text_desc": bool(str(p.get("textDesc") or "").strip()),
        "product_sku_present": bool(p.get("productSku")),
    }


def _list_drafts(client, shop_id: str, limit: int) -> tuple[list[dict], dict]:
    data = client.list_products(page=1, page_size=max(1, min(limit, 100)),
                                dxm_state="draft", shop_id=shop_id or None)
    payload = data.get("data") or {}
    page = payload.get("page") or {}
    rows = page.get("list") or []
    info = {
        "count": len(rows),
        "total_size": page.get("totalSize"),
        "total_page": page.get("totalPage"),
    }
    return rows, info


def _pick_draft(rows: list[dict], cfg, product_id: str) -> str:
    if product_id:
        return product_id
    ids = [str(r.get("id") or "") for r in rows if r.get("id")]
    test_pid = str(getattr(cfg, "alibaba_test_product_id", "") or "").strip()
    if test_pid and test_pid in ids:
        return test_pid
    # 优先挑一个类目合法（纯数字）的草稿，便于展示正常预演链路
    for r in rows:
        cid = str(r.get("categoryId") or "").strip()
        if cid.isdigit() and int(cid) > 0 and str(r.get("mainImage") or "").strip():
            return str(r.get("id"))
    return ids[0] if ids else ""


def _audit_drafts(client, cfg, rows: list[dict], selected: str, limit: int) -> list[dict]:
    """本地 build+validate 体检：不发保存请求，仅读详情。"""
    from aeclone.services.alibaba_payload import (
        build_alibaba_save, resolve_category, validate_alibaba_save,
    )
    out: list[dict] = []
    for row in rows:
        if len(out) >= limit:
            break
        pid = str(row.get("id") or "")
        if not pid:
            continue
        try:
            product = client.product_detail(pid)
            if not product:
                continue
            cid, _full = resolve_category(product, cfg)
            save = build_alibaba_save(product, cfg)
            issues = validate_alibaba_save(product, save, cfg, [], [])
            out.append({
                "product_id": pid,
                "category_id": str(save.get("categoryId") or ""),
                "price_type": str(save.get("priceType") or ""),
                "issue_count": len(issues),
                "issues": issues[:12],
                "is_selected": pid == selected,
            })
        except Exception as e:  # noqa: BLE001
            out.append({"product_id": pid, "error": f"{type(e).__name__}: {str(e)[:120]}"})
    return out


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Alibaba 刊登流水线只读 + dry-run 核验")
    ap.add_argument("--product-id", default="", help="指定要 dry-run 的商品 id")
    ap.add_argument("--shop-id", default="", help="店铺 id（默认取配置/列表首行）")
    ap.add_argument("--limit", type=int, default=50, help="草稿列表读取条数（默认 50）")
    ap.add_argument("--audit-limit", type=int, default=3, help="额外体检草稿数量（0=关闭）")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON 输出路径")
    ap.add_argument("--config-dir", default="", help="隔离配置目录（默认真实配置）")
    args = ap.parse_args(argv)

    if args.config_dir:
        os.environ["AURORA_CONFIG_DIR"] = args.config_dir
    # 双保险：即使调用方传错 dry_run=False，也只能预演
    os.environ["AURORA_NO_SAVE"] = "1"
    os.environ.pop("AURORA_TEST", None)

    from aeclone.config import AppConfig
    from aeclone.services.alibaba_pipeline import run_alibaba_product
    from aeclone.services.alibaba_publish import make_client

    output = Path(args.output)
    report: dict = {
        "generated_at": _stamp(),
        "mode": "read-only + dry-run (never saves, no paid AI)",
        "status": "ERROR",
        "use_ai": False,
        "dry_run": True,
        "env_guard": {"AURORA_NO_SAVE": os.environ.get("AURORA_NO_SAVE")},
        "config_source": args.config_dir or "real (~/Library/Application Support/AuroraList)",
        "cookie_source": "unknown",
        "shop_id": "",
        "drafts": {},
        "selected": {},
        "dry_run_result": {},
        "draft_audit": [],
        "readonly_endpoints": [
            "GET /api/alibabaIcbuProduct/list.json",
            "GET /api/alibabaIcbuProduct/edit.json",
            "POST /api/alibabaIcbuCategory/attributeList.json",
            "POST /api/alibabaIcbuCategory/schemaList.json",
            "GET /api/bannedWord/alibabaCheckBannedWord",
        ],
        "notes": [],
    }

    try:
        cfg = AppConfig.load()
    except Exception as e:  # noqa: BLE001
        report["status"] = "ERROR"
        report["error"] = f"配置读取失败：{type(e).__name__}: {str(e)[:160]}"
        _write(output, report)
        print("ERROR: 配置读取失败")
        return 1

    report["cookie_source"] = _cookie_source(cfg)

    # 1) 客户端（Cookie 缺失 → SKIP）
    try:
        raw_client = make_client(cfg)
    except Exception:  # noqa: BLE001
        report["status"] = "SKIP"
        report["skip_reason"] = ("没有可用 Cookie：请在「设置 → Alibaba 刊登」粘贴 Cookie，"
                                 "或在「店铺与登录」读取/粘贴店小秘 Cookie 后重试")
        _write(output, report)
        print(f"SKIP: {report['skip_reason']}")
        print(f"报告已写入 {output}")
        return 0

    client = ReadOnlyClient(raw_client)

    # 2) 店铺（配置 > 指定 > 列表首行推断）
    shop_id = (args.shop_id or str(getattr(cfg, "alibaba_shop_id", "") or "")).strip()
    try:
        if not shop_id:
            probe = raw_client.list_products(page=1, page_size=1)
            probe_rows = ((probe.get("data") or {}).get("page") or {}).get("list") or []
            if probe_rows:
                shop_id = str(probe_rows[0].get("shopId") or "")
        report["shop_id"] = shop_id
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {str(e)[:160]}"
        if _looks_like_cookie_issue(msg):
            report["status"] = "SKIP"
            report["skip_reason"] = f"只读接口不可用（Cookie 可能过期）：{msg}"
            _write(output, report)
            print(f"SKIP: {report['skip_reason']}")
            return 0
        report["status"] = "ERROR"
        report["error"] = f"连接验证失败：{msg}"
        _write(output, report)
        print(f"ERROR: {report['error']}")
        return 1

    # 3) 草稿列表（只读）
    rows: list[dict] = []
    try:
        rows, info = _list_drafts(raw_client, shop_id, args.limit)
        report["drafts"] = {
            **info,
            "sampled": [{k: r.get(k) for k in ("id", "subject", "categoryId",
                                                "priceType", "dxmState")} for r in rows[:10]],
        }
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {str(e)[:160]}"
        if _looks_like_cookie_issue(msg):
            report["status"] = "SKIP"
            report["skip_reason"] = f"读取草稿列表失败（Cookie 可能过期）：{msg}"
            _write(output, report)
            print(f"SKIP: {report['skip_reason']}")
            return 0
        report["status"] = "ERROR"
        report["error"] = f"读取草稿列表失败：{msg}"
        _write(output, report)
        print(f"ERROR: {report['error']}")
        return 1

    # 4) 选草稿
    selected = _pick_draft(rows, cfg, str(args.product_id or "").strip())
    report["selected"] = {"product_id": selected}
    if not selected:
        report["status"] = "SKIP"
        report["skip_reason"] = f"店铺 {shop_id} 没有可 dry-run 的草稿（草稿数 {len(rows)}）"
        _write(output, report)
        print(f"SKIP: {report['skip_reason']}")
        return 0

    # 5) 真实 dry-run（use_ai=False，绝不保存）
    try:
        result = run_alibaba_product(selected, cfg, dry_run=True, use_ai=False,
                                     client=client)
    except Exception as e:  # noqa: BLE001
        report["status"] = "ERROR"
        report["error"] = f"dry-run 异常：{type(e).__name__}: {str(e)[:200]}"
        _write(output, report)
        print(f"ERROR: {report['error']}")
        return 1

    try:
        product = raw_client.product_detail(selected)
    except Exception:  # noqa: BLE001
        product = {}
    report["selected"].update(_product_brief(product))
    report["dry_run_result"] = {
        "ok": result.ok,
        "preview": result.preview,
        "saved": result.saved,
        "error": result.error,
        "subject": result.subject,
        "subject_chars": len(result.subject),
        "keywords": result.keywords,
        "text_chars": result.text_chars,
        "category_id": result.category_id,
        "issues": result.issues,
        "warnings": result.warnings,
        "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in result.steps],
    }
    report["safety"] = {
        "dry_run_preview": bool(result.preview),
        "never_saved": not result.saved,
        "save_product_calls": client.save_calls,
    }

    # 6) 少量草稿本地体检（只读详情 + build/validate，不保存）
    if args.audit_limit > 0:
        try:
            report["draft_audit"] = _audit_drafts(raw_client, cfg, rows[:args.audit_limit],
                                                  selected, args.audit_limit)
        except Exception as e:  # noqa: BLE001
            report["notes"].append(f"草稿体检跳过：{type(e).__name__}: {str(e)[:120]}")

    dirty = [a for a in report["draft_audit"] if a.get("issue_count")]
    report["summary"] = {
        "drafts_sampled": len(rows),
        "selected_dry_run_ok": result.ok,
        "selected_issues": len(result.issues),
        "audited": len(report["draft_audit"]),
        "audited_with_issues": len(dirty),
        "conclusion": (
            "只读链路可用；dry-run 预演完成且未保存"
            + (f"；所选草稿校验未通过 {len(result.issues)} 项（脏草稿会被新校验拦截）"
               if result.issues else "；所选草稿校验通过")
        ),
    }
    if client.save_calls:
        report["status"] = "ERROR"
        report["error"] = "安全违规：dry-run 期间调用了 save_product"
    elif not result.preview or result.saved:
        report["status"] = "ERROR"
        report["error"] = "dry-run 语义异常：preview=False 或 saved=True"
    else:
        report["status"] = "OK"

    _write(output, report)

    print(f"状态：{report['status']}")
    print(f"Cookie 来源：{report['cookie_source']}（不显示内容）")
    print(f"店铺：{shop_id or '(未知)'} · 草稿 {report['drafts'].get('count')} / "
          f"共 {report['drafts'].get('total_size')}")
    print(f"Dry-run 商品：{selected}")
    print(f"  ok={result.ok} preview={result.preview} saved={result.saved} "
          f"subject={len(result.subject)}字符 category={result.category_id}")
    print("Steps:")
    for s in result.steps:
        print(f"  {'✓' if s.ok else '✗'} {s.name}: {s.detail}")
    if result.issues:
        print("Issues（本地校验，未保存）：")
        for i in result.issues:
            print(f"  - {i}")
    if result.warnings:
        print("Warnings：")
        for w in result.warnings:
            print(f"  - {w}")
    print(f"结论：{report['summary']['conclusion']}")
    print(f"报告已写入 {output}")
    return 0 if report["status"] in ("OK", "SKIP") else 1


if __name__ == "__main__":
    sys.exit(main())

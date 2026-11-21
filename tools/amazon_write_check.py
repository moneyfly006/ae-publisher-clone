"""Amazon 真实写入核验（Lead 专用，默认只读；真实写必须显式 --allow-write）。

用途：对「店小秘 Amazon 采集箱商品」做一次 op=1 保存往返，验证保存包结构与回读一致性。
独立实现（不 import 后端模块），用于交叉验证 integrations/amazon.py 的保存实现。

安全边界：
- 默认 --mode dry：只读 editSp + 组装/校验保存包，绝不调用写接口；
- --mode roundtrip 且带 --allow-write 才会向 /api/amazonProductSp/add.json 发一次 op=1；
- 每次真实写前后都保存 editSp 快照，写后逐字段对比，证据写入 docs/amazon-write-verification.json；
- Cookie 从 --cookie-file / DXM_COOKIE_PATH / .tmp-probe/cookies.txt 读取，从不明文打印。

用法：
  .venv/bin/python tools/amazon_write_check.py --mode dry
  .venv/bin/python tools/amazon_write_check.py --mode roundtrip --allow-write --note "roundtrip #1"
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path

import requests

BASE = "https://www.dianxiaomi.com"
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "amazon-write-verification.json"

# 实测抓包得到的 39 键（顺序保持）
SAVE_KEYS = [
    "id", "shopId", "marketPlaceId", "currency", "title", "titleDifferentiation",
    "categoryId", "categoryBrowsePath", "audience", "isVariation", "brandSettledType",
    "fullCid", "sourceUrl", "standardProductType", "standardProductId", "sku", "brand",
    "manufacturer", "productAttr", "standardPrice", "saleSalePrice", "saleStartDateStr",
    "saleEndDateStr", "quantity", "conditionType", "conditionValue", "mainImage",
    "extraImagesStr", "draftImgUrl", "variationTheme", "skuTitle", "description",
    "bulletPoint", "gpsrManufacturerReference", "dssResponsiblePartyAddress", "searchTerms",
]
# 修正：真实键名是 dsaResponsiblePartyAddress / gpsrSafetyAttestation / switchFulfillmentTo /
# merchantShippingGroup，这里按真实抓包顺序重建，避免拼写错误。
SAVE_KEYS = [
    "id", "shopId", "marketPlaceId", "currency", "title", "titleDifferentiation",
    "categoryId", "categoryBrowsePath", "audience", "isVariation", "brandSettledType",
    "fullCid", "sourceUrl", "standardProductType", "standardProductId", "sku", "brand",
    "manufacturer", "productAttr", "standardPrice", "saleSalePrice", "saleStartDateStr",
    "saleEndDateStr", "quantity", "conditionType", "conditionValue", "mainImage",
    "extraImagesStr", "draftImgUrl", "variationTheme", "skuTitle", "description",
    "bulletPoint", "gpsrManufacturerReference", "dsaResponsiblePartyAddress",
    "gpsrSafetyAttestation", "switchFulfillmentTo", "merchantShippingGroup", "searchTerms",
]

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


class ApiError(RuntimeError):
    pass


def _cookie_paths() -> list[Path]:
    out = []
    env = os.environ.get("DXM_COOKIE_PATH", "").strip()
    if env:
        out.append(Path(env).expanduser())
    out.append(ROOT / ".tmp-probe" / "cookies.txt")
    return out


def load_cookie(explicit: str = "") -> str:
    cands = [Path(explicit).expanduser()] if explicit else _cookie_paths()
    for p in cands:
        try:
            raw = p.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if raw:
            return raw
    raise SystemExit("找不到 Cookie：用 --cookie-file 指定，或放 .tmp-probe/cookies.txt，"
                     "或设 DXM_COOKIE_PATH")


def mask(cookie: str) -> str:
    first = cookie.split(";", 1)[0].strip()
    return (first[:14] + "…") if len(first) > 14 else "…"


def make_session(cookie: str) -> requests.Session:
    s = requests.Session()
    for part in cookie.split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        s.cookies.set(k, v, domain=".dianxiaomi.com")
    s.headers.update({"User-Agent": _UA, "X-Requested-With": "XMLHttpRequest",
                      "Referer": f"{BASE}/web/amazon/pageList/draft"})
    return s


def _parse(r: requests.Response, what: str) -> dict:
    if r.status_code == 401 or r.status_code == 403:
        raise ApiError(f"{what}: 登录已失效（HTTP {r.status_code}）")
    if r.status_code >= 400:
        raise ApiError(f"{what}: HTTP {r.status_code} {r.text[:120]}")
    try:
        return r.json()
    except Exception as exc:  # noqa: BLE001
        raise ApiError(f"{what}: 返回非 JSON {r.text[:120]}") from exc


def api_get(s: requests.Session, path: str, params: dict | None = None) -> dict:
    return _parse(s.get(BASE + path, params=params, timeout=40), path)


def api_post(s: requests.Session, path: str, data: dict | None = None) -> dict:
    return _parse(s.post(BASE + path, data=data or {}, timeout=60), path)


def edit_detail(s: requests.Session, product_id: str) -> dict:
    d = api_get(s, "/api/amazonProductSp/editSp.json", {"id": str(product_id)})
    if str(d.get("code")) != "0":
        raise ApiError(d.get("msg") or f"editSp 失败 code={d.get('code')}")
    return d.get("data") or {}


def _num(v, default=0):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except Exception:
        return default


def build_payload(detail: dict, currency: str = "") -> dict:
    """由 editSp 详情构造保存包（不改内容）。"""
    p = dict(detail.get("product") or {})
    out = {k: p.get(k) for k in SAVE_KEYS}
    out["currency"] = currency or p.get("standardPriceCurrency") or ""
    for k in ("standardPrice", "saleSalePrice"):
        v = p.get(k)
        try:
            f = float(v or 0)
        except Exception:
            f = 0.0
        out[k] = None if f <= 0 else round(f, 2)
    for k in ("quantity", "isVariation", "gpsrSafetyAttestation", "skuTitle"):
        out[k] = _num(p.get(k), 0)
    if not out.get("bulletPoint"):
        out["bulletPoint"] = ""
    if not out.get("searchTerms"):
        out["searchTerms"] = ""
    return out


def validate_payload(payload: dict) -> list[str]:
    issues = []
    title = str(payload.get("title") or "").strip()
    if not title:
        issues.append("标题为空")
    if len(title) > 200:
        issues.append(f"标题超过 200 字符（{len(title)}）")
    if not str(payload.get("sku") or "").strip():
        issues.append("SKU 为空")
    if not str(payload.get("brand") or "").strip():
        issues.append("品牌为空")
    if not str(payload.get("mainImage") or "").startswith("http"):
        issues.append("主图为空或非 http")
    cid = str(payload.get("categoryId") or "").strip()
    if not cid or cid.lower() == "none" or not re.fullmatch(r"\d+", cid):
        issues.append(f"类目无效：{cid!r}")
    path = str(payload.get("categoryBrowsePath") or "").strip()
    if cid and path and path.rsplit("/", 1)[-1] != cid:
        issues.append("categoryBrowsePath 末段与 categoryId 不一致")
    bullets = [b for b in str(payload.get("bulletPoint") or "").split("±") if b.strip()]
    if len(bullets) > 10:
        issues.append(f"卖点超过 10 条（schema maxItems=10，当前 {len(bullets)}）")
    for i, b in enumerate(bullets, 1):
        if len(b) > 700:
            issues.append(f"卖点 {i} 超过 700 字符（{len(b)}）")
    desc = str(payload.get("description") or "")
    if not desc.strip():
        issues.append("产品描述为空（required）")
    elif len(desc.encode("utf-8")) > 20000:
        issues.append(f"产品描述超过 20000 字节（{len(desc.encode('utf-8'))}）")
    terms = str(payload.get("searchTerms") or "")
    if len(terms) > 500 or len(terms.encode("utf-8")) > 2000:
        issues.append(f"Search Terms 超过 schema 上限（{len(terms)} 字符 / "
                      f"{len(terms.encode('utf-8'))} 字节）")
    if payload.get("marketPlaceId") in (None, ""):
        issues.append("marketPlaceId 为空")
    if not str(payload.get("productAttr") or "").strip():
        issues.append("productAttr 为空（可能丢属性）")
    return issues


def zip_upload(s: requests.Session, payload: dict, op: str = "1") -> dict:
    """真实写入口：ZIP(amazonSave.txt) + multipart(file, op)。"""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("amazonSave.txt", raw.encode("utf-8"))
    files = {"file": ("blob", buf.getvalue(), "application/zip")}
    r = s.post(BASE + "/api/amazonProductSp/add.json", files=files, data={"op": str(op)},
               timeout=90, headers={"Referer": f"{BASE}/web/amazon/edit?id={payload.get('id')}"})
    d = _parse(r, "save_product")
    if str(d.get("code")) != "0":
        raise ApiError(d.get("msg") or f"保存失败 code={d.get('code')}")
    return d


def _diff(before: dict, after: dict, keys: list[str]) -> dict:
    changed = {}
    for k in keys:
        if str(before.get(k)) != str(after.get(k)):
            changed[k] = {"before": str(before.get(k))[:160], "after": str(after.get(k))[:160]}
    return changed


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
    ap = argparse.ArgumentParser(description="Amazon 真实写入核验（默认只读）")
    ap.add_argument("--mode", choices=["dry", "roundtrip"], default="dry")
    ap.add_argument("--product-id", default="220114595363182310")
    ap.add_argument("--cookie-file", default="")
    ap.add_argument("--allow-write", action="store_true",
                    help="真实 op=1 保存（仅 roundtrip 模式需要；用户已授权 2 次）")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)

    cookie = load_cookie(args.cookie_file)
    s = make_session(cookie)
    record = {"when": _dt.datetime.now().isoformat(timespec="seconds"),
              "mode": args.mode, "product_id": args.product_id, "note": args.note,
              "cookie_masked": mask(cookie), "write": False, "ok": False}

    try:
        login = api_get(s, "/api/userIn.json")
        if str(login.get("code")) != "0":
            raise ApiError(f"登录失效：{login.get('msg')}")
        account = (login.get("data") or {})
        record["account"] = account.get("account")
        record["puid"] = account.get("id")

        detail = edit_detail(s, args.product_id)
        product = detail.get("product") or {}
        record["site"] = detail.get("site")
        record["title"] = str(product.get("title") or "")[:120]
        record["before"] = {k: product.get(k) for k in SAVE_KEYS}
        record["before_bullets"] = len([b for b in str(product.get("bulletPoint") or "").split("±") if b.strip()])

        currency = ""
        try:
            cur = api_post(s, "/api/amazon/currency/list.json",
                           {"shopId": product.get("shopId"), "marketPlaceId": product.get("marketPlaceId")})
            currency = str(((cur.get("data") or {}) or {}).get("currency") or "")
        except Exception:  # noqa: BLE001
            pass
        payload = build_payload(detail, currency)
        issues = validate_payload(payload)
        record["payload_keys"] = list(payload.keys())
        record["payload_key_count"] = len(payload)
        record["payload_bytes"] = len(json.dumps(payload, ensure_ascii=False))
        record["issues"] = issues
        record["currency"] = currency
        record["bullets"] = len([b for b in str(payload.get("bulletPoint") or "").split("±") if b.strip()])
        record["zip_probe"] = "ok"
        print(f"商品 {args.product_id} · 站点 {detail.get('site')} · "
              f"39键={len(payload)} · issues={len(issues)} · currency={currency}")
        for it in issues:
            print("  ⚠", it)

        if args.mode == "dry":
            record["ok"] = not issues
            _append_evidence(record)
            print("dry 模式：未写任何数据。证据已写入", EVIDENCE)
            return 0 if record["ok"] else 1

        if not args.allow_write:
            print("roundtrip 需要 --allow-write（真实保存一次 op=1）。已退出。")
            _append_evidence(record)
            return 2
        if issues:
            print("保存包校验未通过，拒绝真实写入：", issues)
            _append_evidence(record)
            return 1

        record["write"] = True
        print("真实写入：POST /api/amazonProductSp/add.json (op=1, ZIP/multipart) …")
        resp = zip_upload(s, payload, op="1")
        record["save_response"] = {k: str(v)[:200] for k, v in resp.items()}
        print("保存返回：code=", resp.get("code"), "msg=", resp.get("msg"))

        after_detail = edit_detail(s, args.product_id)
        after = after_detail.get("product") or {}
        record["after"] = {k: after.get(k) for k in SAVE_KEYS}
        changed = _diff(record["before"], record["after"], SAVE_KEYS)
        record["changed_fields"] = changed
        record["changed_count"] = len(changed)
        print(f"回读对比：changed={len(changed)}")
        for k, v in changed.items():
            print(f"  Δ {k}: {v['before'][:60]} → {v['after'][:60]}")
        record["ok"] = len(changed) == 0
        _append_evidence(record)
        print("证据已写入", EVIDENCE)
        return 0 if record["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        record["error"] = str(exc)[:400]
        record["ok"] = False
        _append_evidence(record)
        print("失败：", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

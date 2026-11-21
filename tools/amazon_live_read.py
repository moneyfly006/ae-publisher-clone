"""Amazon 真实账号**只读**核验脚本（绝不调用保存写接口，绝不写任何数据）。

覆盖：登录 / 店铺 / 站点 / 三态列表 / 详情 / 类目属性 / 类目树 / 按路径取类目 /
最近类目 / 产品类型 / 类目预测 / 币种 / 物流 / 品牌 / 违禁词 / 保存包构造 /
预演（dry-run）。

Cookie 来源优先级：``--cookie-file`` → ``DXM_COOKIE_PATH`` → ``.tmp-probe/cookies.txt``。
Cookie 只在内存里使用，输出时脱敏，绝不写入仓库文件。

用法::

    .venv/bin/python tools/amazon_live_read.py
    .venv/bin/python tools/amazon_live_read.py --product-id 220114595363182310
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "docs" / "amazon-live-read.json"
DEFAULT_PRODUCT_ID = "220114595363182310"

sys.path.insert(0, str(ROOT / "src"))

from aeclone.config import AppConfig  # noqa: E402
from aeclone.integrations.amazon import AmazonClient, AmazonError  # noqa: E402
from aeclone.services import amazon_payload as AP  # noqa: E402
from aeclone.services import amazon_prompts as PR  # noqa: E402
from aeclone.services.amazon_pipeline import (  # noqa: E402
    run_amazon_product, OP_SAVE,
)


class Report:
    """步骤记录器：每步带 ok/detail/data，最后汇总并写 JSON。"""

    def __init__(self) -> None:
        self.steps: list[dict] = []
        self.data: dict = {}

    def step(self, name: str, ok: bool, detail: str = "", data=None) -> bool:
        entry = {"name": name, "ok": bool(ok), "detail": str(detail or "")[:600]}
        if data is not None:
            entry["data"] = data
        self.steps.append(entry)
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}: {entry['detail']}")
        return bool(ok)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s["ok"] for s in self.steps)

    def summary(self) -> dict:
        passed = sum(1 for s in self.steps if s["ok"])
        return {"total": len(self.steps), "passed": passed,
                "failed": len(self.steps) - passed, "all_pass": self.ok}


def cookie_paths(explicit: str = "") -> list[Path]:
    out: list[Path] = []
    if explicit:
        out.append(Path(explicit).expanduser())
        return out
    env = os.environ.get("DXM_COOKIE_PATH", "").strip()
    if env:
        out.append(Path(env).expanduser())
    out.append(ROOT / ".tmp-probe" / "cookies.txt")
    return out


def load_cookie(explicit: str = "") -> tuple[str, str]:
    """返回 (cookie 原文, 来源路径)；找不到时抛 SystemExit。"""
    for path in cookie_paths(explicit):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if text:
            return text, str(path)
    raise SystemExit("找不到 Cookie：用 --cookie-file 指定，或放 .tmp-probe/cookies.txt，"
                     "或设 DXM_COOKIE_PATH")


def mask_cookie(cookie: str) -> str:
    """只暴露第一个 Cookie 名与前 10 个字符，避免把真实会话写进证据。"""
    first = str(cookie or "").split(";", 1)[0].strip()
    name, _, value = first.partition("=")
    return f"{name}={value[:10]}…" if value else f"{name}=…"


def _plain_len(value) -> int:
    return len(PR.strip_html(value))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Amazon 只读真实核验（不写任何数据）")
    parser.add_argument("--cookie-file", default="", help="Cookie 文件路径（优先级最高）")
    parser.add_argument("--product-id", default="", help="核验用商品 id（默认取采集箱第一条）")
    parser.add_argument("--out", default=str(OUT_PATH), help="证据 JSON 输出路径")
    args = parser.parse_args(argv)

    rep = Report()
    cookie, source = load_cookie(args.cookie_file)
    rep.data["cookie_source"] = source
    rep.data["cookie_masked"] = mask_cookie(cookie)
    print(f"Cookie 来源：{source} · {rep.data['cookie_masked']}（只读模式，绝不写）")

    cfg = AppConfig()
    client = AmazonClient(cookie, timeout=40)

    def _forbidden(*_a, **_kw):
        raise AssertionError("只读脚本禁止调用保存写接口 save_product")

    # 双重保险：即使误调用保存入口也立刻失败
    client.save_product = _forbidden  # type: ignore[method-assign]

    try:
        # 1) 登录
        try:
            user = client.user_in()
            account = str(user.get("account") or "")
            puid = user.get("id")
            rep.data.update({"account": account, "puid": puid})
            rep.step("登录 userIn", bool(user), f"{account} · puid={puid}")
        except Exception as exc:  # noqa: BLE001
            rep.step("登录 userIn", False, f"登录失败：{exc}")
            return _finish(rep, args.out)

        # 2) 店铺
        shops = client.shops()
        rep.data["shops"] = shops
        rep.step("店铺 shops", bool(shops),
                 "、".join(f"{s['id']}:{s['name']}" for s in shops[:5]) or "无 Amazon 店铺")
        if not shops:
            return _finish(rep, args.out)
        shop_id = shops[0]["id"]
        rep.data["shop_id"] = shop_id

        # 3) 站点
        sites = client.sites(shop_id)
        rep.data["sites"] = sites
        codes = ", ".join(f"{s['code']}({s['currency']})" for s in sites)
        rep.step("站点 sites", bool(sites), codes or "无可用站点")

        # 4) 三态列表
        lists: dict[str, dict] = {}
        for state in ("draft", "offline", "online"):
            try:
                data = client.list_products(state=state, page=1, page_size=5,
                                            shop_id="-1")
                lists[state] = data
                rep.step(f"列表 {state}", True,
                         f"total={data['total']} page={data['page_no']}/{data['total_page']} "
                         f"items={len(data['items'])} · 采集箱{data['drafts']} "
                         f"待发布{data['wait_publish']} 失败{data['publish_fail']}")
            except Exception as exc:  # noqa: BLE001
                rep.step(f"列表 {state}", False, f"{exc}")
        rep.data["lists"] = {
            state: {k: v for k, v in data.items() if k != "items"}
            for state, data in lists.items()
        }
        rep.data["list_samples"] = {
            state: data["items"][:2] for state, data in lists.items()
        }

        # 5) 商品详情（默认用采集箱第一条，更贴近真实工作流）
        pid = str(args.product_id or "").strip()
        if not pid:
            draft_items = (lists.get("draft") or {}).get("items") or []
            pid = str(draft_items[0]["id"]) if draft_items else DEFAULT_PRODUCT_ID
        rep.data["product_id"] = pid
        try:
            detail = client.product_detail(pid)
            product = detail.get("product") or {}
            site_code = str(detail.get("site") or "")
            mp = str(product.get("marketPlaceId") or "")
            rep.data["detail_site"] = site_code
            rep.data["detail_marketplace"] = mp
            rep.data["product_summary"] = {
                "title": str(product.get("title") or "")[:200],
                "sku": product.get("sku"),
                "categoryId": product.get("categoryId"),
                "categoryBrowsePath": product.get("categoryBrowsePath"),
                "marketPlaceId": mp,
                "standardPrice": product.get("standardPrice"),
                "saleSalePrice": product.get("saleSalePrice"),
                "quantity": product.get("quantity"),
                "bullets": len([b for b in str(product.get("bulletPoint") or "").split("±")
                                if b.strip()]),
                "description_chars": len(str(product.get("description") or "")),
                "fields": len(product),
            }
            rep.step("详情 editSp", bool(product and detail.get("site")),
                     f"id={pid} · 站点 {site_code} · {len(product)} 字段 · "
                     f"卖点 {rep.data['product_summary']['bullets']} 条")
        except Exception as exc:  # noqa: BLE001
            rep.step("详情 editSp", False, f"{exc}")
            return _finish(rep, args.out)

        if not site_code:
            site_code = next((s["code"] for s in sites), "CA")
        if not mp:
            mp = next((s["marketplace_id"] for s in sites
                       if s["code"] == site_code), "")

        # 6) 类目属性（getProductAttrById：id + site=marketPlaceId）
        try:
            attrs = client.product_attributes(pid, mp)
            note = f"{len(attrs)} 个属性" if attrs else "接口返回空（保存包 productAttr 走详情原样回写）"
            rep.step("类目属性 getProductAttrById", isinstance(attrs, dict), note)
            rep.data["attribute_count"] = len(attrs) if isinstance(attrs, dict) else 0
        except Exception as exc:  # noqa: BLE001
            rep.step("类目属性 getProductAttrById", False, f"{exc}")

        # 7) 类目树（顶层 parentId=0，site=站点代码）
        roots: list[dict] = []
        try:
            roots = client.category_roots(site_code)
            first = roots[0] if roots else {}
            rep.step("类目树 roots", bool(roots),
                     f"{len(roots)} 个顶层类目 · 首项 "
                     f"{first.get('chineseName') or first.get('categoryName')} "
                     f"(id={first.get('id')}, categoryId={first.get('categoryId')})")
            rep.data["category_roots"] = [
                {"id": r.get("id"), "categoryId": r.get("categoryId"),
                 "categoryName": r.get("categoryName"),
                 "chineseName": r.get("chineseName"),
                 "leafCategory": r.get("leafCategory")}
                for r in roots[:8]
            ]
        except Exception as exc:  # noqa: BLE001
            rep.step("类目树 roots", False, f"{exc}")

        # 8) 子类目（用内部 id 下钻）
        if roots:
            try:
                children = client.category_children(site_code, roots[0].get("id"))
                rep.step("类目树 children", True,
                         f"parentId={roots[0].get('id')} → {len(children)} 个子类目")
                rep.data["category_children_sample"] = [
                    {"id": c.get("id"), "categoryId": c.get("categoryId"),
                     "categoryName": c.get("categoryName")} for c in children[:5]]
            except Exception as exc:  # noqa: BLE001
                rep.step("类目树 children", False, f"{exc}")
        else:
            rep.step("类目树 children", False, "无顶层类目，跳过下钻")

        # 9) 按路径取类目（用商品原类目路径）
        path = str(product.get("categoryBrowsePath") or "")
        if path:
            try:
                node = client.category_by_path(site_code, path)
                match = str(node.get("categoryId") or "") == str(product.get("categoryId") or "")
                rep.step("类目路径 getCategoryBySiteNodePath", bool(node) and match,
                         f"{path} → {node.get('chineseName') or node.get('categoryName')}"
                         f"(categoryId={node.get('categoryId')}, 匹配原类目={match})")
                rep.data["category_by_path"] = {
                    "id": node.get("id"), "categoryId": node.get("categoryId"),
                    "categoryName": node.get("categoryName"),
                    "chineseName": node.get("chineseName"),
                    "nodePathId": node.get("nodePathId"),
                    "leafCategory": node.get("leafCategory"),
                }
            except Exception as exc:  # noqa: BLE001
                rep.step("类目路径 getCategoryBySiteNodePath", False, f"{exc}")
        else:
            rep.step("类目路径 getCategoryBySiteNodePath", False, "商品没有 categoryBrowsePath")

        # 10) 最近使用类目（shopId + site=站点代码）
        try:
            history = client.category_history(shop_id, site_code)
            rep.step("最近类目 listHistory", isinstance(history, list),
                     f"{len(history)} 条 · " + "、".join(
                         str(h.get("categoryName") or h.get("categoryId"))
                         for h in history[:3]))
            rep.data["category_history_count"] = len(history)
        except Exception as exc:  # noqa: BLE001
            rep.step("最近类目 listHistory", False, f"{exc}")

        # 11) 产品类型（site=站点代码；传 marketPlaceId 会返回空）
        try:
            types = client.product_types(site_code)
            rep.step("产品类型 gerProductTypes", bool(types),
                     f"{len(types)} 个 · {', '.join(types[:5])}")
            rep.data["product_type_count"] = len(types)
        except Exception as exc:  # noqa: BLE001
            rep.step("产品类型 gerProductTypes", False, f"{exc}")

        # 12) 类目预测（best-effort，可能为空/失败）
        try:
            pred = client.predict_category(shop_id, site_code,
                                           str(product.get("title") or "")[:200])
            rep.step("类目预测 predict", isinstance(pred, dict),
                     f"productType={pred.get('productType') or '无'}（best-effort）")
            rep.data["predict"] = {k: pred.get(k) for k in
                                   ("productType", "amaoznCategorySearchVos")}
        except Exception as exc:  # noqa: BLE001
            rep.step("类目预测 predict", True, f"best-effort 跳过：{exc}")

        # 13) 币种 / 物流 / 品牌（shopId + marketPlaceId）
        try:
            currency = client.currency(shop_id, mp)
            rep.step("币种 currency", bool(currency), currency or "接口返回空")
        except Exception as exc:  # noqa: BLE001
            rep.step("币种 currency", False, f"{exc}")
        try:
            logistics = client.logistics(shop_id, mp)
            rep.step("物流模板 getLogisticsTemplates", bool(logistics),
                     "、".join(str(x.get("templateName") or x.get("templateId"))
                              for x in logistics[:3]) or "无模板")
            rep.data["logistics"] = [
                {"templateId": x.get("templateId"), "templateName": x.get("templateName")}
                for x in logistics[:10]]
        except Exception as exc:  # noqa: BLE001
            rep.step("物流模板 getLogisticsTemplates", False, f"{exc}")
        try:
            brands = client.brands(shop_id, mp)
            rep.step("品牌 getBrands", isinstance(brands, list),
                     f"{len(brands)} 个品牌" + ("（空数组是正常结果）" if not brands else ""))
        except Exception as exc:  # noqa: BLE001
            rep.step("品牌 getBrands", False, f"{exc}")

        # 14) 违禁词（只读；普通文本 + 明显违规文本各一次；data=null 表示无命中）
        try:
            normal = client.check_banned_words(
                title=str(product.get("title") or "")[:200],
                description=str(product.get("description") or "")[:2000],
                bullet_point=str(product.get("bulletPoint") or "")[:1000],
                search_terms=str(product.get("searchTerms") or ""))
            probe_text = "Best price 100% guarantee free shipping contact a@b.com"
            probe = client.check_banned_words(title=probe_text, description="",
                                              bullet_point="", search_terms="")
            # 客户端 helper 失败会吞成 []；这里再用原始返回确认接口本身可用（code=0）
            raw = client._post("/api/bannedWord/amazonCheckBannedWord.json", {
                "title": probe_text, "description": "", "bulletPoint": "",
                "searchTerms": ""})
            api_ok = str(raw.get("code")) == "0"
            raw_data = raw.get("data")
            rep.data["banned_words"] = {
                "normal": normal[:20], "probe": probe[:20],
                "raw_data": raw_data if isinstance(raw_data, (dict, list)) else None,
            }
            rep.step("违禁词 amazonCheckBannedWord", api_ok,
                     f"接口 code={raw.get('code')}（{'可用' if api_ok else '异常'}）· "
                     f"原商品命中 {len(normal)} 个 · 测试文本命中 {len(probe)} 个"
                     + ("；data=null 表示词表未命中" if raw_data is None else ""))
        except Exception as exc:  # noqa: BLE001
            rep.step("违禁词 amazonCheckBannedWord", False, f"{exc}")

        # 15) 保存包构造 + 校验（只读，不写）
        try:
            from aeclone.services.amazon_pipeline import prepare_save
            detail2, payload, issues, warnings = prepare_save(pid, cfg, client=client)
            ok_keys = list(payload.keys()) == AP.SAVE_KEYS and len(payload) == 39
            rep.data["payload"] = {
                "key_count": len(payload),
                "keys_ok": ok_keys,
                "title_chars": len(str(payload.get("title") or "")),
                "bullets": len(AP.split_bullets(payload.get("bulletPoint") or "")),
                "description_bytes": len(str(payload.get("description") or "").encode()),
                "search_terms_bytes": len(str(payload.get("searchTerms") or "").encode()),
                "standardPrice": payload.get("standardPrice"),
                "saleSalePrice": payload.get("saleSalePrice"),
                "currency": payload.get("currency"),
                "marketPlaceId": payload.get("marketPlaceId"),
                "issues": list(issues),
                "warnings": list(warnings),
            }
            rep.step("保存包构造/校验（只读）", ok_keys,
                     f"39键={ok_keys} · issues={len(issues)} · warnings={len(warnings)}"
                     + (f" · {issues[:3]}" if issues else ""))
            if issues:
                rep.data["payload"]["issue_detail"] = list(issues)
        except Exception as exc:  # noqa: BLE001
            rep.step("保存包构造/校验（只读）", False, f"{exc}")

        # 16) 流水线预演（dry_run=True，绝不调用保存接口）
        try:
            res = run_amazon_product(pid, cfg, dry_run=True, use_ai=False,
                                     op=OP_SAVE, client=client)
            rep.data["preview"] = {
                "ok": res.ok, "preview": res.preview, "saved": res.saved,
                "error": res.error, "issues": res.issues, "warnings": res.warnings,
                "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail}
                          for s in res.steps],
            }
            rep.step("流水线预演 dry-run", res.ok and res.preview and not res.saved,
                     f"ok={res.ok} preview={res.preview} saved={res.saved} "
                     f"steps={len(res.steps)}" + (f" · {res.error[:120]}" if res.error else ""))
        except Exception as exc:  # noqa: BLE001
            rep.step("流水线预演 dry-run", False, f"{exc}")

        return _finish(rep, args.out)
    except Exception as exc:  # noqa: BLE001
        rep.step("脚本异常", False, f"{type(exc).__name__}: {exc}")
        return _finish(rep, args.out)


def _finish(rep: Report, out_path: str) -> int:
    record = {
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "mode": "read-only",
        "write_calls": 0,
        "summary": rep.summary(),
        "data": rep.data,
        "steps": rep.steps,
        "limits": [
            "本脚本只调用只读接口；唯一写入口 save_product 已被替换为断言，从未调用",
            "保存包只构造+校验，不做任何写操作",
            "属性/预测/品牌接口返回空是合法结果（best-effort）",
        ],
    }
    path = Path(out_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n证据已写入：{path}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n写证据失败：{exc}")
    s = record["summary"]
    print(f"只读核验：{s['passed']}/{s['total']} 通过，{s['failed']} 失败，"
          f"写操作 0 次")
    return 0 if s["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

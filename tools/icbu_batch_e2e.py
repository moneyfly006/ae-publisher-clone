"""ICBU 采集箱多商品端到端实测工具（选择类目 → 必填属性 → 预演 → op=1 保存 → 回读）。

用途
----
在店小秘 ICBU 店铺的真实采集箱草稿上验证「批量选类目 + 必填属性补齐 + 保存」链路：

1. ``--inventory``（默认包含）分页拉全店铺草稿，逐个 ``product_detail`` 体检，
   记录 categoryId / priceType / SKU / 主图 / 详情长度 / 属性数；
2. 对内置候选商品逐个用 ``run_alibaba_product(dry_run=True, use_ai=False, op="1",
   overrides=...)`` 预演（类目 & 属性预设全部通过 overrides 传入，不改全局 cfg）；
3. 只有 ``--save`` 时才对「预演 ok、issues 为空」的商品逐个真实 ``op=1`` 保存，
   保存后 ``sleep 2s`` 回读详情并 diff 关键字段（categoryId/fullCid/subject/keywords/
   priceType/bulkDiscountPrices/productSku/mainImage/textDesc/wholesaleTrade/dxmState）；
4. 输出 JSON 到 ``docs/icbu-batch-e2e.json``。

安全约束（写死在工具里）
------------------------
- 默认 dry-run；``--save`` 才可能真实写；且写死 ``op="1"``（不接受其它 op）。
- 保存前必须经 ``run_alibaba_product`` 的 validate + 授权 guard，本工具不直接调
  ``save_product``；dry-run 阶段用只读包装客户端把 ``save_product`` 变成哨兵。
- 单次最多保存 8 个商品；禁止保存 220114598434958580（Lead 的 AI 保存测试商品）。
- ``--save`` 时若检测到 ``AURORA_NO_SAVE`` / ``AURORA_TEST`` 直接拒绝（先 unset 再跑）。
- 不打印/落盘 Cookie、api_key；只读接口；不调用付费 AI（use_ai=False）。

跑法
----
    .venv/bin/python tools/icbu_batch_e2e.py              # 只读体检 + dry-run
    .venv/bin/python tools/icbu_batch_e2e.py --save       # 再对通过的候选真实 op=1 保存
    .venv/bin/python tools/icbu_batch_e2e.py --product-ids 220114598434958576 \
        --plan-json /tmp/plan.json                         # 指定候选与类目覆盖

``--plan-json`` 形如 ``{"<product_id>": {"category_id": "202107201",
"attributes": {"Place of Origin": "China"}}``；未给时用内置 ``DEFAULT_PLAN``。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_OUTPUT = ROOT / "docs" / "icbu-batch-e2e.json"
DEFAULT_SHOP_ID = "9466975"
OP_SAVE = "1"
MAX_SAVES = 8
# Lead 留给 AI 保存测试的商品：任何情况下都不允许本工具改写
FORBIDDEN_IDS = {"220114598434958580"}

# 关键字段（回读 diff 用）；长字段只存哈希/长度，避免 JSON 里塞整段详情/图片列表
DIFF_FIELDS = (
    "categoryId", "fullCid", "subject", "keywords", "priceType",
    "bulkDiscountPrices", "wholesaleTrade", "mainImage", "textDesc",
    "detailImage", "productSku", "dxmState", "shopId",
)

# 内置候选计划：product_id -> {category_id, attributes(预设，仅用于补齐缺失必填)}
# 说明：attributes 里的值是「保存测试用的合理占位」，不是从商品数据里读到的真实值；
# 报告会把它们标成假设值（不臆造为真实规格）。
POOL_ATTRS = {
    "Place of Origin": "China",
    "Color": "Other",
    "Material": "Other",
    "Applicable People": "All",
    "Type": "Above Ground Pools",
}
MESH_BAG_ATTRS = {
    "Style": "Other",
    "Logo/Pattern": "Other",
    "Place of Origin": "China",
    "Material": "Mesh",
    "Color": "Other",
}
SPRINKLER_ATTRS = {
    "Color": "Other",
    "Place of Origin": "China",
    "Material": "Plastic",
}
# ATV 预设按商品标题推断：60V / 2000W / 90km 续航 / CE / 四轮越野
ATV_ATTRS = {
    "Maximum Torque(Nm)": "60-80Nm",
    "Maximum Speed": "75-100Km/h",
    "Driving Type": "4Wd",
    "Charge Time(h)": "7-9h",
    "Grade Ability": "20-25°",
    "Driving Mileage": "≥90km",
    "Transmission System": "Chain Drive",
    "Power": "> 1000W",
    "Voltage": "60 V",
    "Place of Origin": "China",
}
# 推断值假设（Lead 要求：构造的 attributes 必须在报告里逐项标注「需人工复核」）
ASSUMPTIONS: dict[str, list[str]] = {
    "220114598434958114": [
        "Voltage=60 V、Power=> 1000W：由标题「60V 2000W」推断",
        "Driving Mileage=≥90km：由标题「90km de Autonomia」推断",
        "Certificate/CE 未写入属性（标题含 Certificado CE，但该类目无对应必填属性）",
        "Maximum Speed=75-100Km/h、Maximum Torque(Nm)=60-80Nm、Driving Type=4Wd、"
        "Charge Time(h)=7-9h、Grade Ability=20-25°、Transmission System=Chain Drive："
        "商品数据无来源，按电动 ATV 常见值推断，需人工复核",
        "Place of Origin=China：按采集源产地推断",
    ],
    "220114598434958546": [
        "Material=Mesh：由标题「Mesh Bag」推断",
        "Style=Other、Logo/Pattern=Other、Color=Other：无来源，占位值需人工复核",
        "Place of Origin=China：按采集源产地推断",
    ],
    "220114598434958532": [
        "Material=Plastic：按喷头品类常见材质推断（也可能是 Metal），需人工复核",
        "Color=Other：无来源，占位值需人工复核",
        "Place of Origin=China：按采集源产地推断",
    ],
    "220114598434958576": [
        "Color=Other：商品缺必填 Color，无来源，占位值需人工复核",
    ],
    "220114598434958574": [
        "Place of Origin=China、Color=Other、Material=Other、Applicable People=All、"
        "Type=Above Ground Pools：商品原有属性不含这些必填项，全部为占位/推断值，"
        "需人工复核后才能提交审核",
    ],
    "220114598434958570": [
        "Place of Origin=China、Color=Other、Material=Other、Applicable People=All、"
        "Type=Above Ground Pools：商品原有属性不含这些必填项，全部为占位/推断值，"
        "需人工复核后才能提交审核",
    ],
    "220114598434958562": [
        "Color=Other、Applicable People=All：商品缺必填项，占位值需人工复核",
    ],
}
GENERAL_ASSUMPTIONS = [
    "所有 attributes 中标记为 Other/推断 的值只用于打通「保存链路」测试，不代表真实规格；"
    "提交审核前必须人工复核",
    "商品原有属性一律保留，预设只补齐缺失的必填项；报告 attrs_written 里列出实际补写项",
    "ATV 标题原长 147 字符，保存时按 Alibaba subject≤128 截到 122 字符（词边界），语义被截断",
    "ATV 关键词语种来自葡萄牙语原标题（quadriciclo/off-road/sem/escovas/para），"
    "面向英文站发布前应改为英文关键词",
    "所有 8 个商品原 keywords 为空，保存时由标题提取 5 个英文词写入",
    "sku_price 商品的 bulkDiscountPrices 由空串规整为 []；ladder_price 商品原阶梯价保留",
    "无 SKU 的 ladder 商品保存后 productSku 变成 []（present-but-empty），"
    "由 build_save_dict 把缺失值序列化为 [] 导致",
]

DEFAULT_PLAN: dict[str, dict] = {
    # 泳池（容器泳池）ladder/sku 混合 5 个
    "220114598434958576": {"category_id": "202107201", "attributes": POOL_ATTRS},
    "220114598434958578": {"category_id": "202107201", "attributes": POOL_ATTRS},
    "220114598434958574": {"category_id": "202107201", "attributes": POOL_ATTRS},
    "220114598434958570": {"category_id": "202107201", "attributes": POOL_ATTRS},
    "220114598434958562": {"category_id": "202107201", "attributes": POOL_ATTRS},
    # 电动 ATV
    "220114598434958114": {"category_id": "201139102", "attributes": ATV_ATTRS},
    # 网袋
    "220114598434958546": {"category_id": "201730007", "attributes": MESH_BAG_ATTRS},
    # 草坪喷泉喷头（Garden Sprinklers 叶子类目）
    "220114598434958532": {"category_id": "100001819", "attributes": SPRINKLER_ATTRS},
}

COOKIE_ISSUE_KEYS = ("cookie", "登录", "未登录", "login", "auth", "401", "403",
                     "forbidden", "unauthorized", "非 json", "html")


class ReadOnlyClient:
    """只读代理：dry-run 阶段任何 save_product 调用都算安全违规。"""

    def __init__(self, inner):
        self._inner = inner
        self.save_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def save_product(self, *args, **kwargs):  # noqa: D401
        self.save_calls += 1
        raise AssertionError("dry-run 阶段禁止调用 save_product")


def _stamp() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(v) -> str:
    return hashlib.sha256(str(v or "").encode("utf-8")).hexdigest()[:12]


def _json_load(raw, default):
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str) and raw.strip()[:1] in "[{":
        try:
            return json.loads(raw)
        except Exception:
            return default
    return default


def _as_attr_rows(product: dict) -> list[dict]:
    raw = _json_load(product.get("attributes"), [])
    out = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("attribute_name") or a.get("name") or "").strip()
        val = str(a.get("value_name") or a.get("value") or "").strip()
        if name:
            out.append({"name": name, "value": val})
    return out


def _sku_summary(product: dict) -> dict:
    from aeclone.services.alibaba_payload import sku_summary
    has, valid, dims = sku_summary(product.get("productSku"))
    return {"present": has, "valid": valid, "dims": dims}


def _trade_summary(product: dict) -> dict:
    trade = _json_load(product.get("wholesaleTrade"), {})
    if not isinstance(trade, dict):
        trade = {}
    inv = trade.get("inventory")
    return {
        "moq": trade.get("min_order_quantity"),
        "unit_type": trade.get("unit_type"),
        "sale_type": trade.get("sale_type"),
        "package_size": trade.get("package_size"),
        "weight": trade.get("weight"),
        "inventory": inv,
        "has_hs": bool(trade.get("tariffsHsCode")),
    }


def _snapshot(product: dict) -> dict:
    """回读快照：关键字段 + 长字段哈希/长度。"""
    main_image = str(product.get("mainImage") or "")
    imgs = [u for u in main_image.split(",") if u.strip()]
    text_desc = str(product.get("textDesc") or "")
    detail = product.get("detailImage")
    detail_rows = _json_load(detail, []) if detail else []
    attrs = _as_attr_rows(product)
    return {
        "id": str(product.get("id") or ""),
        "categoryId": str(product.get("categoryId") or ""),
        "fullCid": str(product.get("fullCid") or ""),
        "subject": str(product.get("subject") or ""),
        "subject_chars": len(str(product.get("subject") or "")),
        "keywords": str(product.get("keywords") or ""),
        "priceType": str(product.get("priceType") or ""),
        "bulkDiscountPrices": str(product.get("bulkDiscountPrices") or ""),
        "bulkDiscountPrices_sha": _sha(product.get("bulkDiscountPrices")),
        "productSku_sha": _sha(product.get("productSku")),
        "sku": _sku_summary(product),
        "mainImage_sha": _sha(main_image),
        "mainImage_count": len(imgs),
        "mainImage_first": imgs[0] if imgs else "",
        "textDesc_chars": len(text_desc),
        "textDesc_sha": _sha(text_desc),
        "detailImage_count": len(detail_rows) if isinstance(detail_rows, list) else 0,
        "attributes_count": len(attrs),
        "attributes_names": [a["name"] for a in attrs],
        "wholesaleTrade": _trade_summary(product),
        "wholesaleTrade_sha": _sha(product.get("wholesaleTrade")),
        "dxmState": str(product.get("dxmState") or ""),
        "shopId": str(product.get("shopId") or ""),
    }


def _brief(product: dict) -> dict:
    snap = _snapshot(product)
    return {
        "id": snap["id"],
        "subject": snap["subject"][:140],
        "subject_chars": snap["subject_chars"],
        "category_id": snap["categoryId"],
        "full_cid": snap["fullCid"],
        "price_type": snap["priceType"],
        "sku": snap["sku"],
        "text_desc_chars": snap["textDesc_chars"],
        "main_image_count": snap["mainImage_count"],
        "attributes_count": snap["attributes_count"],
        "has_bulk_discount": bool(snap["bulkDiscountPrices"].strip("[] ")),
        "dxm_state": snap["dxmState"],
        "shop_id": snap["shopId"],
    }


def _diff(before: dict, after: dict) -> dict:
    """只输出真正变化的字段（长字段给前后哈希/长度，不落全文）。"""
    out = {}
    for key in DIFF_FIELDS:
        b = before.get(key)
        a = after.get(key)
        if b == a:
            continue
        if key == "subject":
            out[key] = {"before": str(b)[:120], "after": str(a)[:120]}
        elif key == "keywords":
            out[key] = {"before": str(b), "after": str(a)}
        elif key == "mainImage":
            out[key] = {"before_count": len(str(b).split(",")) if b else 0,
                        "after_count": len(str(a).split(",")) if a else 0,
                        "changed": "content" if b != a else "same"}
        elif key == "textDesc":
            out[key] = {"before_chars": len(str(b or "")), "after_chars": len(str(a or "")),
                        "before_sha": _sha(b), "after_sha": _sha(a)}
        elif key in ("productSku", "detailImage", "bulkDiscountPrices", "wholesaleTrade"):
            out[key] = {"before_sha": _sha(b), "after_sha": _sha(a),
                        "before_chars": len(str(b or "")), "after_chars": len(str(a or ""))}
        else:
            out[key] = {"before": b, "after": a}
    # 附加派生字段变化
    for key in ("attributes_count", "sku", "mainImage_count"):
        if before.get(key) != after.get(key):
            out[key] = {"before": before.get(key), "after": after.get(key)}
    return out


def _looks_like_cookie_issue(msg: str) -> bool:
    low = str(msg or "").lower()
    return any(k in low for k in COOKIE_ISSUE_KEYS)


def _cookie_source(cfg) -> str:
    if str(getattr(cfg, "alibaba_cookies", "") or "").strip():
        return "alibaba_cookies"
    if getattr(cfg, "manual_cookies", None):
        return "manual_cookies"
    return "browser"


def _list_all_drafts(client, shop_id: str, page_size: int = 50) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    page_no, total_page, total_size = 1, 1, None
    while True:
        resp = client.list_products(page=page_no, page_size=page_size,
                                    dxm_state="draft", shop_id=shop_id or None)
        page = ((resp.get("data") or {}).get("page") or {})
        rows.extend(page.get("list") or [])
        total_page = int(page.get("totalPage") or 1)
        total_size = page.get("totalSize")
        if page_no >= total_page:
            break
        page_no += 1
    return rows, {"count": len(rows), "total_size": total_size, "total_page": total_page}


def _category_full_cid(client, category_id: str) -> str:
    """用 category_detail 的 nodePathId（斜杠路径）拼出保存用 fullCid（逗号路径）。"""
    try:
        detail = client.category_detail(category_id)
    except Exception:
        return ""
    path = str(detail.get("nodePathId") or "").strip()
    parts = [p.strip() for p in path.split("/") if p.strip()]
    if parts and parts[-1] != str(category_id):
        parts.append(str(category_id))
    return ",".join(parts)


def _required_coverage(client, category_id: str, product: dict) -> dict:
    """类目必填属性 vs 商品现有属性覆盖情况。"""
    try:
        req = client.required_attributes(category_id)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    have = {a["name"].strip().lower() for a in _as_attr_rows(product)}
    names, covered, missing = [], [], []
    for a in req:
        name = str(a.get("nameEn") or a.get("nameZh") or "").strip()
        if not name:
            continue
        names.append(name)
        (covered if name.lower() in have else missing).append(name)
    return {"required_count": len(names), "required": names,
            "covered": covered, "missing": missing}


def _resolve_plan(plan_overrides: dict, product_ids: list[str]) -> dict[str, dict]:
    plan = {pid: dict(cfg) for pid, cfg in DEFAULT_PLAN.items()}
    plan.update(plan_overrides or {})
    if product_ids:
        return {pid: plan.get(pid, {}) for pid in product_ids}
    return plan


def _build_overrides(client, product: dict, plan_entry: dict, cache: dict) -> dict:
    pid = str(product.get("id") or "")
    cid = str(plan_entry.get("category_id") or "").strip()
    full = ""
    if cid:
        if cid not in cache:
            cache[cid] = _category_full_cid(client, cid)
        full = cache[cid]
    ov: dict = {}
    if cid:
        ov["category_id"] = cid
    if full:
        ov["full_cid"] = full
    if plan_entry.get("attributes"):
        ov["attributes"] = dict(plan_entry["attributes"])
    if not cid:
        return {}
    return ov


def _run_pipeline(result_mod, product_id: str, cfg, client, dry_run: bool, ov: dict):
    return result_mod.run_alibaba_product(
        product_id, cfg, dry_run=dry_run, op=OP_SAVE, use_ai=False,
        client=client, overrides=ov or None)


def _classify_failure(result, product_after: dict | None) -> str:
    text = f"{result.error} {getattr(result, 'save_result', '')}"
    if result.issues:
        return "validate（必填/数据结构未通过本地校验）"
    if _looks_like_cookie_issue(text):
        return "cookie/auth（登录态失效）"
    if "读取商品详情" in text or "读取类目规则" in text or "连接 Alibaba" in text:
        return "network/api（只读接口失败）"
    if "授权校验" in text:
        return "license（授权 guard 拒绝）"
    if "保存到 Alibaba" in text or result.save_result:
        return "platform（店小秘/平台拒绝保存）"
    return "code/exception（流水线内部异常）"


def _write_json(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_plan_json(path: str) -> dict:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("--plan-json 必须是 {product_id: {category_id, attributes}} 对象")
    out = {}
    for pid, entry in raw.items():
        if not isinstance(entry, dict):
            raise SystemExit(f"--plan-json 中 {pid} 的值必须是对象")
        out[str(pid).strip()] = entry
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ICBU 采集箱多商品 E2E 实测（默认 dry-run）")
    ap.add_argument("--save", action="store_true", help="真实 op=1 保存（默认只预演）")
    ap.add_argument("--shop-id", default=DEFAULT_SHOP_ID, help="店铺 id（默认 9466975）")
    ap.add_argument("--product-ids", default="", help="逗号分隔的候选商品 id（默认内置 8 个）")
    ap.add_argument("--plan-json", default="", help="类目/属性覆盖计划 JSON 路径")
    ap.add_argument("--page-size", type=int, default=50, help="草稿列表分页大小")
    ap.add_argument("--sleep", type=float, default=2.0, help="保存后回读前 sleep 秒数")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON 输出路径")
    ap.add_argument("--config-dir", default="", help="隔离配置目录（默认真实配置）")
    ap.add_argument("--skip-inventory", action="store_true", help="跳过全量草稿体检")
    ap.add_argument("--post-check", action="store_true",
                    help="只读回读已保存商品并更新报告（不保存任何商品）")
    args = ap.parse_args(argv)

    dry_run = not args.save
    if args.save:
        if os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"):
            print("ERROR: 检测到 AURORA_NO_SAVE/AURORA_TEST，--save 会被强制降级为预演；"
                  "请先 unset 这两个环境变量再执行。")
            return 2
    else:
        # 双保险：dry-run 阶段进程内禁止真实写入
        os.environ["AURORA_NO_SAVE"] = "1"
        os.environ.pop("AURORA_TEST", None)

    if args.config_dir:
        os.environ["AURORA_CONFIG_DIR"] = args.config_dir

    from aeclone.config import AppConfig
    from aeclone.integrations.alibaba import AlibabaClient
    from aeclone.services import alibaba_pipeline as pipeline
    from aeclone.services.alibaba_payload import build_alibaba_save
    from aeclone.services.alibaba_publish import make_client

    product_ids = [x.strip() for x in str(args.product_ids or "").split(",") if x.strip()]
    plan = _resolve_plan(_load_plan_json(args.plan_json), product_ids)
    if args.save:
        forbidden = [p for p in plan if p in FORBIDDEN_IDS]
        if forbidden:
            print(f"ERROR: 计划里包含禁止改写的商品 {forbidden}（Lead 保留），已拒绝真实保存。")
            return 2
        if len(plan) > MAX_SAVES:
            print(f"ERROR: 计划保存 {len(plan)} 个商品，超过上限 {MAX_SAVES}。")
            return 2

    output = Path(args.output)
    report: dict = {
        "generated_at": _stamp(),
        "mode": "save (op=1)" if args.save else "dry-run (read-only + preview)",
        "status": "ERROR",
        "use_ai": False,
        "dry_run": dry_run,
        "op": OP_SAVE,
        "authorized_scope": (
            "用户授权：仅 op=1 保存草稿；禁止 op=2/3/5、删除、发布、重试、改价；"
            f"最多 {MAX_SAVES} 个商品；不改 220114598434958580"
        ),
        "env_guard": {
            "AURORA_NO_SAVE": os.environ.get("AURORA_NO_SAVE"),
            "AURORA_TEST": os.environ.get("AURORA_TEST"),
        },
        "config_source": args.config_dir or "real (~/Library/Application Support/AuroraList)",
        "cookie_source": "unknown",
        "shop_id": args.shop_id,
        "plan": {pid: {k: (sorted(v) if k == "attributes" else v)
                       for k, v in entry.items()} for pid, entry in plan.items()},
        "inventory": {},
        "candidates": [],
        "saves": [],
        "summary": {},
        "defects": [],
        "notes": [],
    }

    try:
        cfg = AppConfig.load()
    except Exception as e:  # noqa: BLE001
        report["error"] = f"配置读取失败：{type(e).__name__}: {str(e)[:160]}"
        _write_json(output, report)
        print(f"ERROR: {report['error']}")
        return 1
    report["cookie_source"] = _cookie_source(cfg)

    try:
        raw_client = make_client(cfg)
    except Exception as e:  # noqa: BLE001
        report["status"] = "SKIP"
        report["skip_reason"] = f"没有可用 Cookie：{type(e).__name__}: {str(e)[:160]}"
        _write_json(output, report)
        print(f"SKIP: {report['skip_reason']}")
        return 0
    # 有 Cookie 才创建会话客户端；dry-run 阶段套只读哨兵
    session = AlibabaClient(cfg.manual_cookies or {}, timeout=30) if getattr(cfg, "manual_cookies", None) \
        else raw_client
    client = session if args.save else ReadOnlyClient(session)
    # 统一走 make_client 语义（alibaba_cookies > manual_cookies > 浏览器）
    if getattr(cfg, "alibaba_cookies", None):
        session = AlibabaClient(cfg.alibaba_cookies, timeout=30)
        client = session if args.save else ReadOnlyClient(session)
    report["notes"].append("客户端 Cookie 顺序：alibaba_cookies > manual_cookies > 本机浏览器（不记录内容）")

    # ---------- 0) 只读保存后校验（不保存任何商品） ----------
    if args.post_check:
        return _run_post_check(args, cfg, client, plan, output)

    # ---------- 1) 只读体检 ----------
    rows: list[dict] = []
    if not args.skip_inventory:
        try:
            rows, info = _list_all_drafts(client, args.shop_id, args.page_size)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:160]}"
            if _looks_like_cookie_issue(msg):
                report["status"] = "SKIP"
                report["skip_reason"] = f"读取草稿列表失败（Cookie 可能过期）：{msg}"
            else:
                report["error"] = f"读取草稿列表失败：{msg}"
            _write_json(output, report)
            print(f"{report['status']}: {report.get('skip_reason') or report.get('error')}")
            return 0 if report["status"] == "SKIP" else 1

        records = []
        cat_cache: dict[str, str] = {}
        for row in rows:
            pid = str(row.get("id") or "")
            if not pid:
                continue
            rec = {"product_id": pid,
                   "list": {k: row.get(k) for k in ("subject", "categoryId", "fullCid",
                                                    "priceType", "dxmState", "shopId")}}
            try:
                product = client.product_detail(pid)
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"product_detail 失败：{type(e).__name__}: {str(e)[:140]}"
                records.append(rec)
                continue
            snap = _snapshot(product)
            rec.update({
                "detail_ok": bool(product),
                "subject": snap["subject"][:160],
                "subject_chars": snap["subject_chars"],
                "category_id": snap["categoryId"],
                "full_cid": snap["fullCid"],
                "price_type": snap["priceType"],
                "sku": snap["sku"],
                "text_desc_chars": snap["textDesc_chars"],
                "main_image_count": snap["mainImage_count"],
                "detail_image_count": snap["detailImage_count"],
                "attributes_count": snap["attributes_count"],
                "attributes_names": snap["attributes_names"][:20],
                "keywords_raw": snap["keywords"],
                "bulk_discount_prices": snap["bulkDiscountPrices"][:200],
                "wholesale_trade": snap["wholesaleTrade"],
                "dxm_state": snap["dxmState"],
                "in_plan": pid in plan,
            })
            if pid in plan:
                entry = plan[pid]
                cid = str(entry.get("category_id") or "")
                rec["target"] = {
                    "category_id": cid,
                    "full_cid": cat_cache.get(cid) or _category_full_cid(client, cid),
                    "attributes_presets": sorted(entry.get("attributes") or {}),
                }
                cat_cache[cid] = rec["target"]["full_cid"]
                rec["required_coverage"] = _required_coverage(client, cid, product)
                if entry.get("attributes"):
                    rec["required_coverage"]["preset_fill_planned"] = sorted(entry["attributes"])
            records.append(rec)

        empty_cat = [r["product_id"] for r in records if not str(r.get("category_id") or "").strip()]
        report["inventory"] = {
            "shop_id": args.shop_id,
            **info,
            "empty_category_count": len(empty_cat),
            "empty_category_ids": empty_cat,
            "records": records,
        }
        print(f"体检完成：草稿 {info['count']} 个（totalSize={info['total_size']}），"
              f"categoryId 为空 {len(empty_cat)} 个")

    # ---------- 2) dry-run 预演 ----------
    selected_ids = list(plan.keys())
    if not selected_ids:
        report["status"] = "ERROR"
        report["error"] = "没有候选商品（--product-ids/内置计划为空）"
        _write_json(output, report)
        print(f"ERROR: {report['error']}")
        return 1

    dryrun_ok: list[str] = []
    for pid in selected_ids:
        entry = plan.get(pid) or {}
        rec: dict = {"product_id": pid, "planned_category": str(entry.get("category_id") or "")}
        try:
            product = client.product_detail(pid)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"读取详情失败：{type(e).__name__}: {str(e)[:160]}"
            report["candidates"].append(rec)
            continue
        if not product:
            rec["error"] = "读不到商品（可能已删除/不属于当前店铺）"
            report["candidates"].append(rec)
            continue
        rec["before"] = _brief(product)
        ov = _build_overrides(client, product, entry, {})
        rec["overrides"] = {k: (sorted(v) if k == "attributes" else v) for k, v in ov.items()}
        # 默认 autofill 行为 vs 显式预设：用于暴露「first value 乱填」缺陷
        try:
            req = client.required_attributes(str(entry.get("category_id") or "")) \
                if entry.get("category_id") else []
            save_default = build_alibaba_save(product, cfg, {"category_id": entry.get("category_id"),
                                                             "full_cid": ov.get("full_cid")},
                                              attr_defs=req)
            save_preset = build_alibaba_save(product, cfg, ov, attr_defs=req)
            rec["autofill_default"] = save_default.get("_autofilled") or []
            rec["unresolved_default"] = save_default.get("_unresolved") or []
            rec["autofill_with_presets"] = save_preset.get("_autofilled") or []
            rec["unresolved_with_presets"] = save_preset.get("_unresolved") or []
        except Exception as e:  # noqa: BLE001
            rec["autofill_error"] = f"{type(e).__name__}: {str(e)[:140]}"
        try:
            res = _run_pipeline(pipeline, pid, cfg, client, True, ov)
        except Exception as e:  # noqa: BLE001
            rec["error"] = f"dry-run 异常：{type(e).__name__}: {str(e)[:160]}"
            report["candidates"].append(rec)
            continue
        rec["dry_run"] = {
            "ok": res.ok,
            "preview": res.preview,
            "saved": res.saved,
            "error": res.error,
            "issues": res.issues,
            "warnings": res.warnings,
            "category_id": res.category_id,
            "subject": res.subject,
            "subject_chars": len(res.subject),
            "keywords": res.keywords,
            "text_chars": res.text_chars,
            "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in res.steps],
        }
        if res.ok and not res.issues and res.preview and not res.saved:
            dryrun_ok.append(pid)
        report["candidates"].append(rec)
        print(f"  dry-run {pid} → ok={res.ok} issues={len(res.issues)} "
              f"category={res.category_id} {('✓通过' if pid in dryrun_ok else '✗未通过')}")
        if not res.ok or res.issues:
            for i in res.issues[:6]:
                print(f"    ISSUE: {i}")
            if res.error:
                print(f"    ERROR: {res.error}")

    if not args.save:
        if isinstance(client, ReadOnlyClient) and client.save_calls:
            report["status"] = "ERROR"
            report["error"] = "安全违规：dry-run 期间调用了 save_product"
        else:
            report["status"] = "OK" if dryrun_ok else "FAILED"
        report["summary"] = _summary(report, dryrun_ok, [], "dry-run")
        _write_json(output, report)
        print(f"\n状态：{report['status']} · 预演通过 {len(dryrun_ok)}/{len(selected_ids)}")
        print(f"报告已写入 {output}")
        return 0 if report["status"] == "OK" else 1

    # ---------- 3) 真实 op=1 保存（仅通过预演的） ----------
    to_save = [p for p in selected_ids if p in dryrun_ok][:MAX_SAVES]
    print(f"\n开始真实保存（op=1，最多 {MAX_SAVES} 个）：{to_save}")
    saves = []
    for pid in to_save:
        entry = plan.get(pid) or {}
        rec: dict = {"product_id": pid, "op": OP_SAVE}
        try:
            before_product = client.product_detail(pid)
            before = _snapshot(before_product)
        except Exception as e:  # noqa: BLE001
            rec.update({"ok": False, "error": f"保存前读取失败：{type(e).__name__}: {str(e)[:160]}"})
            saves.append(rec)
            continue
        rec["before"] = _brief(before_product)
        ov = _build_overrides(client, before_product, entry, {})
        rec["overrides"] = {k: (sorted(v) if k == "attributes" else v) for k, v in ov.items()}
        started = time.time()
        try:
            res = _run_pipeline(pipeline, pid, cfg, client, False, ov)
        except Exception as e:  # noqa: BLE001
            rec.update({"ok": False, "error": f"保存异常：{type(e).__name__}: {str(e)[:200]}"})
            saves.append(rec)
            continue
        rec["elapsed_sec"] = round(time.time() - started, 2)
        rec["result"] = {
            "ok": res.ok,
            "saved": res.saved,
            "preview": res.preview,
            "error": res.error,
            "issues": res.issues,
            "warnings": res.warnings,
            "save_result": res.save_result,
            "steps": [{"name": s.name, "ok": s.ok, "detail": s.detail} for s in res.steps],
        }
        time.sleep(max(0.0, args.sleep))
        try:
            after_product = client.product_detail(pid)
            after = _snapshot(after_product)
            rec["after"] = _brief(after_product)
            rec["changed_fields"] = _diff(before, after)
        except Exception as e:  # noqa: BLE001
            rec["changed_fields_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        rec["ok"] = bool(res.ok and res.saved)
        if not rec["ok"]:
            rec["failure_class"] = _classify_failure(res, rec.get("after"))
        saves.append(rec)
        mark = "✓" if rec["ok"] else "✗"
        print(f"  {mark} 保存 {pid}: ok={res.ok} saved={res.saved} "
              f"changed={list((rec.get('changed_fields') or {}).keys())}"
              + (f" error={res.error}" if not rec["ok"] else ""))

    report["saves"] = saves
    ok_saves = [s for s in saves if s.get("ok")]
    report["status"] = "OK" if ok_saves else "FAILED"
    report["summary"] = _summary(report, dryrun_ok, saves, "save")
    report["defects"] = _known_defects(report)
    _write_json(output, report)
    print(f"\n状态：{report['status']} · 真实保存成功 {len(ok_saves)}/{len(saves)}")
    print(f"报告已写入 {output}")
    return 0 if report["status"] == "OK" else 1


def _summary(report: dict, dryrun_ok: list[str], saves: list[dict], phase: str) -> dict:
    failures = [s for s in saves if not s.get("ok")]
    classes: dict[str, int] = {}
    for s in failures:
        key = str(s.get("failure_class") or "unknown").split("（")[0]
        classes[key] = classes.get(key, 0) + 1
    out = {
        "phase": phase,
        "candidates": len(report.get("candidates") or []),
        "dry_run_passed": len(dryrun_ok),
        "saved_attempted": len(saves),
        "saved_ok": len([s for s in saves if s.get("ok")]),
        "save_failed": len(failures),
        "failure_classes": classes,
        "inventory_drafts": (report.get("inventory") or {}).get("count"),
        "inventory_empty_category": (report.get("inventory") or {}).get("empty_category_count"),
        "protected_ids_not_touched": sorted(FORBIDDEN_IDS),
    }
    if phase == "save":
        out["conclusion"] = (
            f"真实 op=1 保存成功 {out['saved_ok']}/{out['saved_attempted']} 个；"
            f"失败 {out['save_failed']} 个"
            + (f"（{', '.join(f'{k}:{v}' for k, v in classes.items())}）" if classes else "")
        )
    else:
        out["conclusion"] = (
            f"只读体检草稿 {out['inventory_drafts']} 个（categoryId 空 {out['inventory_empty_category']} 个）；"
            f"dry-run 预演通过 {out['dry_run_passed']}/{out['candidates']} 个，未保存任何商品"
        )
    return out


def _known_defects(report: dict) -> list[dict]:
    """把实测观察到的缺陷/数据问题落成清单（file:line 为本轮定位结果）。"""
    defects: list[dict] = []
    empty = (report.get("inventory") or {}).get("empty_category_ids") or []
    if empty:
        defects.append({
            "id": "dirty-draft-category",
            "level": "P0-数据",
            "detail": f"{len(empty)} 个草稿 categoryId 为空/None，直接保存会被 validate 拦截"
                      "（采集箱导入未落类目）；必须显式选类目或批量补类目",
            "evidence": "inventory.records[].category_id",
        })
    # 默认 autofill 对 single_select 取第一个候选值（可能把 60V 车填成 48V）
    suspicious = []
    for c in report.get("candidates") or []:
        defaults = c.get("autofill_default") or []
        if any(("48 V" in x) or ("≤30km" in x) or ("< 500W" in x) or ("Shaft Drive" in x)
               or ("Felt" in x) for x in defaults):
            suspicious.append({"product_id": c.get("product_id"), "autofill_default": defaults})
    if suspicious:
        defects.append({
            "id": "autofill-first-value",
            "level": "P1-代码",
            "detail": "autofill_required_attributes 对缺失必填属性在无 Other/China 时取 values[0]，"
                      "会把电动 ATV 填成 48V/<500W/≤30km、网袋 Material 填成 Felt；"
                      "批量保存前应给 alibaba_attr_presets 或按类目预设，避免写入错误规格",
            "file_line": "src/aeclone/services/alibaba_payload.py:450-459",
            "evidence": suspicious,
        })
    # 关键词/详情是采集源数据，标题可能非英文
    dirty_kw = [c["product_id"] for c in (report.get("candidates") or [])
                if not str((c.get("before") or {}).get("subject") or "").strip()]
    if dirty_kw:
        defects.append({"id": "empty-subject", "level": "P1-数据",
                        "detail": "存在无标题草稿", "evidence": dirty_kw})
    if report.get("saves"):
        unchanged = [s["product_id"] for s in report["saves"]
                     if s.get("ok") and not (s.get("changed_fields") or {})]
        if unchanged:
            defects.append({
                "id": "no-visible-change",
                "level": "P2-观察",
                "detail": "保存成功但回读关键字段无变化：平台可能「无改动即成功」或回读接口缓存；"
                          "需结合平台返回 msg 判断",
                "evidence": unchanged,
            })
    return defects


# ---------------- 只读保存后校验（--post-check） ----------------
def _readback_record(client, pid: str, plan_entry: dict) -> dict:
    """只读回读一个商品：类目/标题/关键词/价格/SKU/图片/属性/交易信息。"""
    product = client.product_detail(pid)
    snap = _snapshot(product)
    attrs = {a["name"]: a["value"][:160] for a in _as_attr_rows(product)}
    expected_cid = str(plan_entry.get("category_id") or "")
    expected_full = snap["fullCid"]
    if expected_cid:
        try:
            expected_full = client.category_detail(expected_cid).get("nodePathId") or ""
            parts = [p.strip() for p in str(expected_full).split("/") if p.strip()]
            expected_full = ",".join(parts)
        except Exception:
            expected_full = ""
    return {
        "product_id": pid,
        "subject": snap["subject"][:160],
        "subject_chars": snap["subject_chars"],
        "keywords": snap["keywords"],
        "categoryId": snap["categoryId"],
        "fullCid": snap["fullCid"],
        "expected_category_id": expected_cid,
        "expected_full_cid": expected_full,
        "category_matches_plan": (snap["categoryId"] == expected_cid) if expected_cid else None,
        "priceType": snap["priceType"],
        "bulkDiscountPrices": snap["bulkDiscountPrices"][:300],
        "productSku_present": bool(snap["sku"]["present"]),
        "productSku_valid": snap["sku"]["valid"],
        "productSku_is_empty_array": str(product.get("productSku") or "").strip() in ("[]", ""),
        "mainImage_count": snap["mainImage_count"],
        "mainImage_first": snap["mainImage_first"],
        "textDesc_chars": snap["textDesc_chars"],
        "dxmState": snap["dxmState"],
        "attributes_count": snap["attributes_count"],
        "attributes": attrs,
        "wholesaleTrade": _trade_summary(product),
    }


def _preset_names_landed(report: dict, pid: str, plan_entry: dict) -> dict:
    """从体检记录推断：哪些预设属性是真正补写进去的、哪些是原属性已覆盖而不写。"""
    presets = list((plan_entry.get("attributes") or {}).keys())
    missing = []
    covered = []
    for rec in ((report.get("inventory") or {}).get("records") or []):
        if rec.get("product_id") != pid:
            continue
        cov = rec.get("required_coverage") or {}
        names = {str(n).strip().lower() for n in (cov.get("missing") or [])}
        have = {str(n).strip().lower() for n in (cov.get("covered") or [])}
        for name in presets:
            key = name.lower()
            if key in names:
                missing.append(name)
            elif key in have:
                covered.append(name)
        break
    return {"attrs_written_from_presets": missing, "attrs_preserved_existing": covered}


def _post_check_defects(report: dict, readbacks: dict, write_summary: dict) -> list[dict]:
    """保存后回读才能确认的缺陷/数据问题（与 _known_defects 合并去重）。"""
    defects = list(report.get("defects") or [])
    have = {str(d.get("id")) for d in defects}
    ok_rows = {pid: r for pid, r in readbacks.items() if "error" not in r}

    empty_sku = [pid for pid, r in ok_rows.items() if r.get("productSku_is_empty_array")]
    if empty_sku and "productsku-empty-array" not in have:
        defects.append({
            "id": "productsku-empty-array",
            "level": "P2-代码",
            "detail": "无 productSku 的 ladder 商品保存后 productSku 变成 []（present-but-empty），"
                      "因为 build_save_dict 把缺失值序列化成 \"[]\"；ladder_price 校验不拦，"
                      "但 SKU 编辑器/后续 sku_price 切换会看到「有 SKU 数据但为空」",
            "file_line": "src/aeclone/integrations/alibaba.py:223（j(product.get(\"productSku\"))）",
            "evidence": empty_sku,
        })

    clipped = []
    for pid, entry in write_summary.items():
        subj = entry.get("subject") or {}
        if subj.get("changed"):
            clipped.append({"product_id": pid, "before_chars": subj.get("before_chars"),
                            "after_chars": subj.get("after_chars"),
                            "after": str((ok_rows.get(pid) or {}).get("subject") or "")[:140]})
    if clipped and "subject-truncated" not in have:
        defects.append({
            "id": "subject-truncated",
            "level": "P2-数据/预期行为",
            "detail": "标题超过 Alibaba subject≤128 时被 clip_title 按词边界截断（ATV 147→122），"
                      "属预期保护，但语义被截断，发布前需人工确认标题可读性",
            "file_line": "src/aeclone/services/alibaba_pipeline.py:126-133",
            "evidence": clipped,
        })

    kw_rows = [{"product_id": pid, "keywords": r.get("keywords")}
               for pid, r in ok_rows.items()]
    if kw_rows and "keywords-from-title" not in have:
        defects.append({
            "id": "keywords-from-title",
            "level": "P1-数据",
            "detail": "全部草稿原 keywords 为空，保存时由标题提取 5 个词写入；非英文标题"
                      "（如 ATV 葡萄牙语）会生成非英文关键词（quadriciclo/off-road/sem/escovas/para），"
                      "面向英文站需人工替换后再发布",
            "file_line": "src/aeclone/services/alibaba_pipeline.py:358-375",
            "evidence": kw_rows,
        })

    normalized = [{"product_id": pid, "side_effects": entry.get("side_effect_fields")}
                  for pid, entry in write_summary.items() if entry.get("side_effect_fields")]
    if normalized and "field-normalization" not in have:
        defects.append({
            "id": "field-normalization",
            "level": "P3-预期归一化",
            "detail": "sku_price 商品 bulkDiscountPrices 由空串归一为 []；所有商品 wholesaleTrade "
                      "被重写为含 min_order_quantity/unit_type/sale_type 的规范结构（package_size/"
                      "weight/inventory/阶梯价等业务值均保留未改）；priceType/productSku/mainImage "
                      "未出现在 changed_fields",
            "file_line": "src/aeclone/services/alibaba_payload.py:336-390",
            "evidence": normalized,
        })
    return defects


def _run_post_check(args, cfg, client, plan: dict, output: Path) -> int:
    """只读回读已保存商品，把 post_check/write_summary/rollback_reference 写回 JSON。"""
    from aeclone.integrations.alibaba import AlibabaClient  # noqa: F401

    if not output.exists():
        print(f"ERROR: 找不到已有报告 {output}；先跑一次 dry-run/--save 再 --post-check")
        return 1
    try:
        report = json.loads(output.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: 读取已有报告失败：{type(e).__name__}: {e}")
        return 1
    pids = [str(s.get("product_id")) for s in (report.get("saves") or []) if s.get("product_id")]
    if not pids:
        pids = list(plan.keys())
    if not pids:
        print("ERROR: 报告里没有已保存商品，也没有候选计划")
        return 1

    readbacks = {}
    for pid in pids:
        entry = plan.get(pid) or {}
        try:
            readbacks[pid] = _readback_record(client, pid, entry)
            print(f"  回读 {pid}: cat={readbacks[pid]['categoryId']} "
                  f"plan_match={readbacks[pid]['category_matches_plan']} "
                  f"state={readbacks[pid]['dxmState']} attrs={readbacks[pid]['attributes_count']}")
        except Exception as e:  # noqa: BLE001
            readbacks[pid] = {"product_id": pid,
                              "error": f"{type(e).__name__}: {str(e)[:160]}"}

    # 回滚参考：保存前原始值（来自本轮体检 + 保存前快照）vs 实际写入值
    inventory = {r.get("product_id"): r for r in ((report.get("inventory") or {}).get("records") or [])}
    save_by_id = {s.get("product_id"): s for s in (report.get("saves") or [])}
    rollback, write_summary = {}, {}
    for pid in pids:
        before = inventory.get(pid) or {}
        save = save_by_id.get(pid) or {}
        after = readbacks.get(pid) or {}
        entry = plan.get(pid) or {}
        landed = _preset_names_landed(report, pid, entry)
        rollback[pid] = {
            "product_id": pid,
            "before": {
                "categoryId": str(before.get("category_id") or ""),
                "fullCid": str(before.get("full_cid") or ""),
                "subject": str(before.get("subject") or ""),
                "keywords": str(before.get("keywords_raw") or ""),
                "priceType": str(before.get("price_type") or ""),
                "bulkDiscountPrices": before.get("bulk_discount_prices"),
                "dxmState": str(before.get("dxm_state") or ""),
                "attributes_count": before.get("attributes_count"),
            },
            "after": {
                "categoryId": after.get("categoryId"),
                "fullCid": after.get("fullCid"),
                "subject": after.get("subject"),
                "keywords": after.get("keywords"),
                "priceType": after.get("priceType"),
                "bulkDiscountPrices": after.get("bulkDiscountPrices"),
                "dxmState": after.get("dxmState"),
                "attributes_count": after.get("attributes_count"),
            },
            "rollback_note": "把 before 里的 categoryId/keywords 清空（categoryId 置空）即可回到采集箱原始状态；"
                             "priceType/bulkDiscountPrices/productSku/mainImage 未被本工具修改",
        }
        changed = list((save.get("changed_fields") or {}).keys())
        expected_changes = {"categoryId", "fullCid", "keywords", "subject", "attributes_count"}
        side_effects = [k for k in changed if k not in expected_changes]
        write_summary[pid] = {
            "category_written": {"categoryId": after.get("categoryId"),
                                 "fullCid": after.get("fullCid")},
            "subject": {
                "changed": "subject" in changed,
                "before_chars": (before.get("subject_chars")
                                 or (save.get("before") or {}).get("subject_chars")),
                "after_chars": after.get("subject_chars"),
            },
            "keywords_written": after.get("keywords"),
            "attrs_written_from_presets": landed["attrs_written_from_presets"],
            "attrs_preserved_existing": landed["attrs_preserved_existing"],
            "autofilled_default_before_presets": next(
                (c.get("autofill_default") for c in (report.get("candidates") or [])
                 if c.get("product_id") == pid), None),
            "side_effect_fields": side_effects,
            "data_assumptions": ASSUMPTIONS.get(pid, []),
        }

    # 580（Lead 保留）与草稿总数校验
    protected = {}
    try:
        p580 = client.product_detail("220114598434958580")
        snap580 = _snapshot(p580)
        prev_path = ROOT / "docs" / "alibaba-real-save-verification.json"
        same_as_lead_save = None
        if prev_path.exists():
            try:
                prev = json.loads(prev_path.read_text(encoding="utf-8")).get("after") or {}
                same_as_lead_save = all(
                    str(snap580.get(k) or "") == str(prev.get(k) or "")
                    for k in ("subject", "categoryId", "fullCid", "priceType", "keywords"))
            except Exception:
                same_as_lead_save = None
        protected = {"product_id": "220114598434958580",
                     "subject": snap580["subject"], "categoryId": snap580["categoryId"],
                     "fullCid": snap580["fullCid"], "priceType": snap580["priceType"],
                     "keywords": snap580["keywords"], "dxmState": snap580["dxmState"],
                     "keywords_unchanged_vs_lead_save": same_as_lead_save}
    except Exception as e:  # noqa: BLE001
        protected = {"product_id": "220114598434958580",
                     "error": f"{type(e).__name__}: {str(e)[:160]}"}

    drafts = {}
    try:
        rows, info = _list_all_drafts(client, args.shop_id, args.page_size)
        empty = [str(r.get("id")) for r in rows if not str(r.get("categoryId") or "").strip()]
        drafts = {**info, "empty_category_count": len(empty),
                  "empty_category_ids": empty,
                  "filled_category_count": len(rows) - len(empty)}
    except Exception as e:  # noqa: BLE001
        drafts = {"error": f"{type(e).__name__}: {str(e)[:160]}"}

    changed_map = {str(s.get("product_id")): list((s.get("changed_fields") or {}).keys())
                   for s in (report.get("saves") or [])}
    all_products = [readbacks[p] for p in pids if "error" not in readbacks[p]]
    checks = {
        "all_categories_match_plan": all(r.get("category_matches_plan") for r in all_products) if all_products else None,
        "all_still_draft": all(r.get("dxmState") == "draft" for r in all_products) if all_products else None,
        "price_type_preserved": all(
            not (inventory.get(r["product_id"]) or {}).get("price_type")
            or str((inventory.get(r["product_id"]) or {}).get("price_type")) == str(r.get("priceType"))
            for r in all_products) if all_products else None,
        "main_image_not_in_changed_fields": all(
            "mainImage" not in (changed_map.get(r["product_id"]) or []) for r in all_products),
        "productSku_not_in_changed_fields": all(
            "productSku" not in (changed_map.get(r["product_id"]) or []) for r in all_products),
        "publish_state_verified": False,
        "publish_note": "本次只做 op=1 保存草稿；未发布、未提交审核，发布链路未验证",
    }

    readback_errors = [r for r in readbacks.values() if "error" in r]
    report["defects"] = _post_check_defects(report, readbacks, write_summary)
    report["post_check"] = {
        "readback_at": _stamp(),
        "mode": "read-only 回读（不发保存请求）",
        "checks": checks,
        "products": readbacks,
        "protected_580": protected,
        "drafts_after": drafts,
        "readback_errors": readback_errors,
    }
    report["write_summary"] = write_summary
    report["rollback_reference"] = rollback
    report["data_assumptions"] = {
        "general": GENERAL_ASSUMPTIONS,
        "per_product": ASSUMPTIONS,
        "note": "推断/占位值只用于打通保存链路；不代表真实规格，提交审核前必须人工复核",
    }
    report["status"] = "ERROR" if readback_errors else report.get("status", "OK")
    report["post_check_ok"] = not readback_errors
    _write_json(output, report)
    print(f"\npost-check：回读 {len(readbacks)} 个商品，错误 {len(readback_errors)} 个")
    print(f"  类目全部匹配计划：{checks['all_categories_match_plan']}；仍在草稿：{checks['all_still_draft']}；"
          f"priceType 保持：{checks['price_type_preserved']}")
    print(f"  580 未被改动：{protected.get('keywords_unchanged_vs_lead_save')}")
    print(f"  草稿数：{drafts.get('count')}，仍缺类目：{drafts.get('empty_category_count')}")
    print(f"报告已更新 {output}")
    return 0 if not readback_errors else 1


if __name__ == "__main__":
    sys.exit(main())

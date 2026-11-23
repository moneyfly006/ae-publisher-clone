#!/usr/bin/env python3
"""Alibaba（店小秘 ICBU 直连）刊登就绪度核验：真实只读 + 本地 dry-run。

安全边界（硬约束，脚本内不做任何写入）：
- 只读接口：商品列表/详情、类目/属性/schema、店铺信息；绝不调用 save_product /
  save_product_file / publish / batch_publish / 重试 / 删除，也不调用付费 AI。
- 不打印、不写盘 Cookie 或 api_key；JSON 只保留状态、计数与截断后的公开文案。
- 真实 dry-run：用 Publisher.run(product, dry_run=True) 与 build_alibaba_save /
  validate_alibaba_save 在本地构建校验，不提交保存。

用法：
  .venv/bin/python tools/verify_publish_readiness.py
      # 真实只读 + dry-run，写 docs/publish-readiness.json
  .venv/bin/python tools/verify_publish_readiness.py --offline
      # 完全离线（内置样例商品，仅验证代码链路，不联网）
  .venv/bin/python tools/verify_publish_readiness.py --strict
      # 存在 P0 阻断时返回非 0；默认仅在脚本自身执行失败时返回非 0

产物：docs/publish-readiness.json（--out 可改），严格区分
「只读已验证 / dry-run 已验证 / 真实保存未验证」。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_OUT = ROOT / "docs" / "publish-readiness.json"
STATE_LABELS = ("draft", "online", "offline", "publishFail")
# ALIBABA_RULES.md §3 的保存 JSON 字段清单（用于契约核对）
RULE_SAVE_FIELDS = (
    "op", "id", "productId", "productIdDecrypt", "isSmartEdit", "shopId",
    "categoryId", "publishType", "sourceUrl", "fullCid", "subject", "keywords",
    "groupId", "motorIds", "attributes", "mainImage", "draftImgUrl", "textDesc",
    "detailImage", "shipmentPlace", "warehouseType", "overseaCountryRegion",
    "priceType", "bulkDiscountPrices", "wholesaleTrade", "sourcingTrade",
    "productSku", "salePkgDesc", "logisticsProperty", "marketSample",
    "marketSamplingQuantity", "marketSamplingPrice",
)
_SECRET_KEYS = ("api_key", "manual_cookies", "alibaba_cookies", "license_token",
                "license_voucher", "license_signature", "image_host_key",
                "license_identity", "manual_cookie")


# ---------------------------------------------------------------- 通用工具
def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json_list(value) -> list:
    """把 edit.json 里的 JSON 字符串/数组统一成 list；坏数据返回 []。"""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str) and value.strip():
        try:
            data = json.loads(value)
        except Exception:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    return []


def _int(value, default=0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _digits(value) -> bool:
    return bool(re.fullmatch(r"\d+", str(value or "").strip()))


def _secret_values(cfg) -> list[str]:
    out = []
    for key in _SECRET_KEYS:
        value = getattr(cfg, key, None) if cfg is not None else None
        if isinstance(value, dict):
            out.extend(str(v) for v in value.values() if str(v))
        elif isinstance(value, str) and value:
            out.append(value)
    return [v for v in out if len(v) >= 4]


def redact(text, cfg=None) -> str:
    """把已知凭据从任意文本里抹掉（写报告/日志前统一调用）。"""
    out = str(text or "")
    for value in _secret_values(cfg):
        out = out.replace(value, "[redacted]")
    return out


# ---------------------------------------------------------------- 配置/凭据
def load_config():
    from aeclone.config import AppConfig
    return AppConfig.load()


def config_snapshot(cfg) -> dict:
    """可安全落盘的配置摘要：不包含任何凭据值。"""
    def has(key):
        value = getattr(cfg, key, None)
        if isinstance(value, dict):
            return bool(value)
        return bool(str(value or "").strip())

    return {
        "active_platform": str(getattr(cfg, "active_platform", "") or ""),
        "api_base": str(getattr(cfg, "api_base", "") or ""),
        "api_key_set": has("api_key"),
        "manual_cookies_set": has("manual_cookies"),
        "alibaba_cookies_set": has("alibaba_cookies"),
        "store_id": str(getattr(cfg, "store_id", "") or ""),
        "store_name": str(getattr(cfg, "store_name", "") or ""),
        "template_product_id": str(getattr(cfg, "template_product_id", "") or ""),
        "publish_action": str(getattr(cfg, "publish_action", "") or ""),
        "alibaba_shop_id": str(getattr(cfg, "alibaba_shop_id", "") or ""),
        "alibaba_test_product_id": str(getattr(cfg, "alibaba_test_product_id", "") or ""),
        "alibaba_category_id": str(getattr(cfg, "alibaba_category_id", "") or ""),
        "alibaba_category_name": str(getattr(cfg, "alibaba_category_name", "") or ""),
        "alibaba_full_cid": str(getattr(cfg, "alibaba_full_cid", "") or ""),
        "alibaba_price_type": str(getattr(cfg, "alibaba_price_type", "") or ""),
        "alibaba_op": getattr(cfg, "alibaba_op", None),
        "price_mode": str(getattr(cfg, "price_mode", "") or ""),
        "price_multiplier": str(getattr(cfg, "price_multiplier", "") or ""),
        "write_price_to_product": bool(getattr(cfg, "write_price_to_product", True)),
        "endpoint_overrides": sorted((getattr(cfg, "endpoint_overrides", None) or {}).keys()),
        "combo_enable": bool(getattr(cfg, "combo_enable", False)),
        "template_values_set": bool(str(getattr(cfg, "template_values", "") or "")),
    }


def cookie_sources(cfg) -> tuple[list[dict], object, str]:
    """按安全优先级找可用 Cookie：配置 alibaba_cookies → manual_cookies → Chrome。

    返回 (attempts, cookies, source)。cookies 可能是 dict 或 Cookie 字符串；
    attempts 只记录来源名与是否可用，不记录值。
    """
    attempts: list[dict] = []
    if str(getattr(cfg, "alibaba_cookies", "") or "").strip():
        raw = str(cfg.alibaba_cookies).strip()
        attempts.append({"source": "config.alibaba_cookies", "usable": True})
        return attempts, raw, "config.alibaba_cookies"
    attempts.append({"source": "config.alibaba_cookies", "usable": False})
    manual = getattr(cfg, "manual_cookies", None) or {}
    if isinstance(manual, dict) and manual:
        attempts.append({"source": "config.manual_cookies", "usable": True,
                         "cookie_count": len(manual)})
        return attempts, dict(manual), "config.manual_cookies"
    attempts.append({"source": "config.manual_cookies", "usable": False})
    try:
        chrome = importlib.import_module("aeclone.integrations.chrome_cookies")
        helper = getattr(chrome, "read_chrome_cookies", None)
        if callable(helper):
            found = helper(["dianxiaomi.com"])
            if found:
                attempts.append({"source": "chrome.read_chrome_cookies", "usable": True})
                return attempts, found, "chrome.read_chrome_cookies"
        attempts.append({
            "source": "chrome.read_cookies",
            "usable": callable(getattr(chrome, "read_cookies", None)),
            "note": "alibaba_publish.make_client 期望 read_chrome_cookies（该函数不存在）",
        })
        reader = getattr(chrome, "read_cookies", None)
        if callable(reader):
            found = reader("chrome", "dianxiaomi.com") or {}
            if found:
                attempts.append({"source": "chrome.read_cookies", "usable": True})
                return attempts, found, "chrome.read_cookies"
    except Exception as exc:  # noqa: BLE001
        attempts.append({"source": "chrome", "usable": False,
                         "error": redact(f"{type(exc).__name__}: {exc}", cfg)[:160]})
    return attempts, None, ""


def alibaba_client(cookies):
    from aeclone.integrations.alibaba import AlibabaClient
    return AlibabaClient(cookies)


def make_client_probe(cfg) -> dict:
    """按 UI/服务层真实入口探测 make_client 是否可用（不发网络请求）。"""
    try:
        from aeclone.services import alibaba_publish as AP
        client = AP.make_client(cfg)
        return {"ok": True, "detail": "make_client 可用（未发请求）",
                "client_type": type(client).__name__}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False,
                "error": redact(f"{type(exc).__name__}: {exc}", cfg)[:200],
                "impact": "UI「设置 → Alibaba 刊登 → 保存到 Alibaba」在此处直接失败，"
                          "不会产生保存请求"}


# ---------------------------------------------------------------- 本地纯函数（可离线测试）
def alibaba_dry_build(cfg, detail: dict, attr_defs: list | None = None,
                      schema_fields: list | None = None) -> dict:
    """本地构建 alibabaSave 并校验：不保存、不联网。"""
    from aeclone.services.alibaba_payload import build_alibaba_save, validate_alibaba_save
    save = build_alibaba_save(detail, cfg, attr_defs=attr_defs or [])
    issues = validate_alibaba_save(detail, save, cfg, attr_defs or [],
                                   schema_fields or [])
    attrs = _json_list(save.get("attributes"))
    detail_images = _json_list(save.get("detailImage"))
    try:
        trade = json.loads(save.get("wholesaleTrade") or "{}")
    except Exception:
        trade = {}
    return {
        "issues": issues,
        "issues_count": len(issues),
        "autofilled": save.get("_autofilled") or [],
        "unresolved": save.get("_unresolved") or [],
        "priceType": save.get("priceType"),
        "categoryId": save.get("categoryId"),
        "fullCid": save.get("fullCid"),
        "shopId": save.get("shopId"),
        "keywords": save.get("keywords"),
        "keyword_count": len([k for k in str(save.get("keywords") or "").split(",") if k.strip()]),
        "main_image_count": len([u for u in str(save.get("mainImage") or "").split(",") if u.strip()]),
        "detail_image_count": len(detail_images),
        "attribute_count": len(attrs),
        "textDesc_len": len(str(save.get("textDesc") or "")),
        "trade": {
            "min_order_quantity": trade.get("min_order_quantity"),
            "package_size": trade.get("package_size"),
            "weight": trade.get("weight"),
            "sale_type": trade.get("sale_type"),
            "tariffsHsCode": trade.get("tariffsHsCode"),
            "inventory": trade.get("inventory"),
        },
        "payload_op": save.get("op"),
    }


def map_to_publisher_product(detail: dict) -> dict:
    """把 ICBU edit.json 商品映射成 Publisher 需要的通用商品模型（仅本地）。"""
    bulk = [x for x in _json_list(detail.get("bulkDiscountPrices")) if isinstance(x, dict)]
    price = 0.0
    for row in bulk:
        try:
            price = float(row.get("price") or 0)
            break
        except Exception:
            continue
    images = [u.strip() for u in str(detail.get("mainImage") or "").split(",") if u.strip()]
    detail_images = [str(x.get("url")) for x in _json_list(detail.get("detailImage"))
                     if isinstance(x, dict) and x.get("url")]
    skus = []
    for row in _json_list(detail.get("productSku")):
        if isinstance(row, dict) and row.get("sku_code"):
            skus.append({"sku": str(row.get("sku_code")),
                         "price": row.get("price"), "stock": ""})
    attrs = [{"name": a.get("attribute_name"), "value": a.get("value_name")}
             for a in _json_list(detail.get("attributes")) if isinstance(a, dict)]
    status = str(detail.get("dxmState") or "")
    return {
        "id": str(detail.get("id") or ""),
        "title": str(detail.get("subject") or ""),
        "price": price,
        "images": images,
        "detail_images": detail_images,
        "skus": skus,
        "attributes": attrs,
        "target_attributes": [],
        "status": status,
        "status_type": status,
        "raw": {},
        "_source_sku_rows": [],
    }


def publisher_dry_run(cfg, product: dict) -> dict:
    """Publisher.run(dry_run=True)：只做本地规划，不保存、不调 AI。"""
    from aeclone.services.publisher import Publisher
    publisher = Publisher(cfg, dxm=None)
    result = publisher.run(product, dry_run=True, manage_progress=False)
    return {
        "ok": bool(result.ok),
        "error": redact(result.error, cfg),
        "preview": bool(result.preview),
        "completion_label": result.completion_label,
        "steps": [{"name": s.name, "ok": bool(s.ok), "detail": redact(s.detail, cfg)}
                  for s in result.steps],
    }


def evaluate_detail_offline(cfg, detail: dict, attr_defs: list | None = None,
                            schema_fields: list | None = None) -> dict:
    """给定商品详情，跑「Alibaba 本地构建校验 + Publisher dry-run」。"""
    product = map_to_publisher_product(detail)
    return {
        "product_id": str(detail.get("id") or ""),
        "alibaba_dry_run": alibaba_dry_build(cfg, detail, attr_defs, schema_fields),
        "publisher_dry_run": publisher_dry_run(cfg, product),
    }


def probe_known_gaps(cfg) -> dict:
    """本地探测已知代码缺口（只读、仅计算，用于报告与回归观察）。"""
    detail = _synthetic_detail()
    out: dict = {}

    # 1) 选类目是否写进保存 payload
    cfg2 = copy.deepcopy(cfg)
    cfg2.set("alibaba_category_id", "100010756")
    cfg2.set("alibaba_full_cid", "18,100005657,100005663,100010756")
    save2 = _build_save(cfg2, detail)
    out["category_selection_applied"] = {
        "expected": "选中类目应写入 payload.categoryId/fullCid",
        "actual": {"categoryId": save2.get("categoryId"),
                   "fullCid": save2.get("fullCid")},
        "gap": str(save2.get("categoryId")) == str(detail.get("categoryId")),
        "source": "services/alibaba_payload.py:168-276（build_alibaba_save 不注入 cfg 类目）",
    }

    # 2) FOB 模式是否清理继承的阶梯价/HS
    cfg3 = copy.deepcopy(cfg)
    cfg3.set("alibaba_price_type", "fob_price")
    cfg3.set("alibaba_fob_min_price", "100")
    cfg3.set("alibaba_fob_max_price", "200")
    save3 = _build_save(cfg3, detail)
    try:
        trade3 = json.loads(save3.get("wholesaleTrade") or "{}")
    except Exception:
        trade3 = {}
    try:
        tiers3 = json.loads(save3.get("bulkDiscountPrices") or "[]")
    except Exception:
        tiers3 = []
    out["fob_residual_fields"] = {
        "expected": "FOB 模式不应写 tariffsHsCode，也不应保留阶梯价",
        "actual": {"priceType": save3.get("priceType"),
                   "bulkDiscountPrices": save3.get("bulkDiscountPrices"),
                   "tariffsHsCode": trade3.get("tariffsHsCode")},
        "gap": bool(trade3.get("tariffsHsCode")) or bool(tiers3),
        "source": "services/alibaba_payload.py:278-346（FOB 分支应清理继承字段）",
    }

    # 3) sku_price 校验是否真的能看到 SKU 数据（productSku 是对象字符串）
    save4 = dict(save3)
    save4["priceType"] = "sku_price"
    save4["productSku"] = "not-a-sku-object"
    from aeclone.services.alibaba_payload import validate_alibaba_save
    issues4 = validate_alibaba_save(detail, save4, cfg, [], [])
    out["sku_price_validation"] = {
        "expected": "productSku 非有效 SKU 数据时应报「按规格定价时缺少 SKU 数据」",
        "actual": {"issues": issues4},
        "gap": not any("SKU" in str(i) for i in issues4),
        "source": "services/alibaba_payload.py:29-45,379-381（_as_list 对对象字符串误判为非空）",
    }

    # 4) keywords 是否覆盖商品原有值
    save5 = _build_save(cfg, detail)
    out["keywords_overwrite"] = {
        "expected": "未配置 alibaba_keywords/overrides 时应保留商品原有 keywords",
        "actual": {"product_keywords": detail.get("keywords"),
                   "saved_keywords": save5.get("keywords")},
        "gap": str(save5.get("keywords")) != str(detail.get("keywords")),
        "source": "services/alibaba_payload.py:89-104,179-180",
    }
    return out


def _build_save(cfg, detail):
    from aeclone.services.alibaba_payload import build_alibaba_save
    return build_alibaba_save(detail, cfg, attr_defs=[])


def _synthetic_detail() -> dict:
    """内置样例商品（离线模式/缺口探测用，非真实商品数据）。"""
    attrs = [
        {"attribute_id": "1", "attribute_name": "Place of Origin",
         "value_id": "100000458", "value_name": "China"},
        {"attribute_id": "191284229", "attribute_name": "Type",
         "value_id": "-1", "value_name": "Container swimming pool"},
        {"attribute_id": "3", "attribute_name": "Model Number",
         "value_id": "-1", "value_name": "JZX-001"},
        {"attribute_id": "-1", "attribute_name": "Color",
         "value_id": "-1", "value_name": "Blue"},
        {"attribute_id": "-1", "attribute_name": "Material",
         "value_id": "-1", "value_name": "Fiberglass"},
        {"attribute_id": "-1", "attribute_name": "Applicable People",
         "value_id": "-1", "value_name": "Other"},
    ]
    imgs = [f"https://example.invalid/main-{i}.jpg" for i in range(1, 7)]
    detail_imgs = [{"displayName": "default", "value": -2,
                    "url": f"https://example.invalid/detail-{i}.jpg", "text": ""}
                   for i in range(1, 4)]
    return {
        "id": "SYNTH-ICBU-1",
        "shopId": "9466975",
        "subject": "Customized Fiberglass Swimming Pool Container",
        "categoryId": "202107201",
        "fullCid": "18,100005657,100005663,202107201",
        "dxmState": "draft",
        "productStatusType": None,
        "keywords": "fiberglass pool, container pool, above ground pool",
        "mainImage": ",".join(imgs),
        "draftImgUrl": "|".join(imgs),
        "detailImage": json.dumps(detail_imgs, ensure_ascii=False),
        "textDesc": "<p>Fiberglass swimming pool container.</p>",
        "attributes": json.dumps(attrs, ensure_ascii=False),
        "priceType": "ladder_price",
        "bulkDiscountPrices": json.dumps([{"start_quantity": 1, "price": 4300.0}]),
        "wholesaleTrade": json.dumps({
            "unit_type": "Piece", "sale_type": "normal", "batch_number": None,
            "tariffsHsCode": [{"toCountry": "US", "hsCode": ""}],
            "min_order_quantity": 1,
            "inventory": [{"warehouseCode": "CN_LOCAL_01", "value": 10}],
            "package_size": "300X200X150", "weight": "300",
            "shipping_line_template_id": None, "deliver_periods": [],
        }, ensure_ascii=False),
        "sourcingTrade": "",
        "productSku": json.dumps({"exclude_skus": [], "special_skus": [], "attributes": []}),
    }


SYNTHETIC_REQUIRED_ATTRS = [
    {"attrNameId": "1", "nameEn": "Place of Origin", "required": 1,
     "values": [{"id": "100000458", "name_en": "China"}]},
    {"attrNameId": "2", "nameEn": "Color", "required": 1,
     "values": [{"id": "1", "name_en": "White"}, {"id": "2", "name_en": "Other"}]},
    {"attrNameId": "3", "nameEn": "Material", "required": 1,
     "values": [{"id": "5", "name_en": "Other"}]},
    {"attrNameId": "4", "nameEn": "Applicable People", "required": 1,
     "values": [{"id": "9", "name_en": "Other"}]},
    {"attrNameId": "5", "nameEn": "Type", "required": 1,
     "values": [{"id": "7", "name_en": "Container swimming pool"}]},
]
SYNTHETIC_SCHEMA_FIELDS = [{"fieldId": x} for x in
                           ("companyDesc", "companyFaqDesc", "companyImage",
                            "detailImage", "saleType", "scPrice", "tariffsHsCode",
                            "textDesc")]


# ---------------------------------------------------------------- 契约核对
def contract_checks() -> dict:
    """对照 docs/ALIBABA_RULES.md 的保存协议做静态契约核对（不联网）。"""
    from aeclone.integrations.alibaba import AlibabaClient, SAVE_NAME
    from aeclone.services import alibaba_publish as AP
    from aeclone.integrations.dianxiaomi import ENDPOINTS

    app_src = (ROOT / "src/aeclone/ui/app.py").read_text(encoding="utf-8", errors="ignore")
    publish_src = (ROOT / "src/aeclone/services/alibaba_publish.py").read_text(
        encoding="utf-8", errors="ignore")
    publisher_src = (ROOT / "src/aeclone/services/publisher.py").read_text(
        encoding="utf-8", errors="ignore")
    alibaba_src = (ROOT / "src/aeclone/integrations/alibaba.py").read_text(
        encoding="utf-8", errors="ignore")
    pagination_fixed = bool(re.search(r'"pageNo"\s*:\s*page\b', alibaba_src))
    payload_keys = sorted(AlibabaClient.build_save_dict({}).keys())
    missing = [key for key in RULE_SAVE_FIELDS if key not in payload_keys]
    default_save = AlibabaClient.build_save_dict({})
    do_save_src = app_src.split("def do_save", 1)[-1][:1600] if "def do_save" in app_src else ""
    alibaba_ui = _source_section(app_src, "def _p_alibaba_listing")
    if not alibaba_ui:
        alibaba_ui = _source_section(app_src, "def _alibaba_cards")
    return {
        "op_mapping": {
            "documented": {"save_only": "1", "publish": "2", "save_to_waiting": "3",
                           "schedule": "5"},
            "code": {"save_only": AP.OP_SAVE, "publish": AP.OP_PUBLISH,
                     "save_to_waiting": AP.OP_MOVE_PENDING,
                     "schedule": AP.OP_SCHEDULE},
            "match": all(str(getattr(AP, name)) == val for name, val in (
                ("OP_SAVE", "1"), ("OP_PUBLISH", "2"),
                ("OP_MOVE_PENDING", "3"), ("OP_SCHEDULE", "5"))),
        },
        "save_transport": {
            "documented": "POST /api/alibabaIcbuProduct/add.json, file=zip(alibabaSave.txt), op",
            "code_endpoint": "/alibabaIcbuProduct/add.json",
            "code_zip_name": SAVE_NAME,
            "match": SAVE_NAME == "alibabaSave.txt",
        },
        "payload_fields": {
            "documented_count": len(RULE_SAVE_FIELDS),
            "missing": missing,
            "match": not missing,
            "op_default": default_save.get("op"),
        },
        "ui_exposure": {
            "ui_alibaba_page_exists": "def _p_alibaba_listing" in app_src,
            "ui_calls_save_to_alibaba": "save_to_alibaba(" in app_src,
            "ui_passes_op": bool(re.search(r"save_to_alibaba\([^)]*\bop\s*=", app_src)),
            "ui_op5_exposed": bool(re.search(r"op=5|定时发布（op=5", alibaba_ui)),
            "ui_has_preview_before_save": "预演" in alibaba_ui,
            "ui_requires_confirm_before_save": "确认保存" in alibaba_ui,
            "ui_has_cookie_fallback_to_manual": ("manual_cookies" in alibaba_ui
                                                or "manual_cookies" in publish_src),
            "save_to_alibaba_default_op": "OP_SAVE",
            "source": "src/aeclone/ui/app.py 的 Alibaba 刊登页",
        },
        "smt_path_separation": {
            "publisher_save_endpoint": ENDPOINTS.get("product_save"),
            "publisher_zip_name": "smtEditSave.txt",
            "publisher_requires_template": True,
            "publisher_uses_alibaba_client": (
                "integrations.alibaba" in publisher_src
                or "AlibabaClient" in publisher_src),
            "note": "publisher.py 是速卖通(SMT)路径；ICBU 直连用 integrations/alibaba.py",
        },
        "list_pagination": {
            "client_param": "pageNo" if pagination_fixed else "page",
            "api_param": "pageNo",
            "match": bool(pagination_fixed),
            "documented_param": "page（docs/ALIBABA_RULES.md §2 已过时，以代码 pageNo 为准）",
            "source": "src/aeclone/integrations/alibaba.py:list_products",
        },
        "cookie_wiring": {
            "make_client_uses_manual_cookies": "manual_cookies" in publish_src,
            "make_client_browser_fallback": "read_cookies" in publish_src,
            "make_client_broken_import": "read_chrome_cookies" in publish_src,
            "source": "src/aeclone/services/alibaba_publish.py:make_client",
        },
    }


# ---------------------------------------------------------------- 真实只读
def _safe(client_call, label: str, cfg) -> dict:
    start = time.monotonic()
    try:
        value = client_call()
        return {"ok": True, "label": label, "value": value,
                "elapsed": round(time.monotonic() - start, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "label": label,
                "error": redact(f"{type(exc).__name__}: {exc}", cfg)[:220],
                "elapsed": round(time.monotonic() - start, 2)}


def live_readonly(cfg, client, source: str) -> dict:
    """真实只读：列表/详情/类目/属性/schema。绝不保存。"""
    out: dict = {"cookie_source": source, "calls": []}

    def call(label, fn):
        result = _safe(fn, label, cfg)
        out["calls"].append({"label": label, "ok": result["ok"],
                             "elapsed": result["elapsed"],
                             **({"error": result["error"]} if not result["ok"] else {})})
        return result

    first = call("list.all.page1", lambda: client.list_products(1, 1, None, None))
    shop_id = str(getattr(cfg, "alibaba_shop_id", "") or getattr(cfg, "store_id", "") or "")
    if first["ok"]:
        rows = ((first["value"].get("data") or {}).get("page") or {}).get("list") or []
        if rows and rows[0].get("shopId"):
            shop_id = str(rows[0]["shopId"])
    out["shop_id"] = shop_id

    counts = {}
    for state in STATE_LABELS:
        result = call(f"list.{state}.total",
                      lambda s=state: client.list_products(1, 1, s, shop_id))
        if result["ok"]:
            page = ((result["value"].get("data") or {}).get("page") or {})
            counts[state] = {"total": page.get("totalSize"),
                             "rows": len(page.get("list") or [])}
        else:
            counts[state] = {"error": result["error"]}
    out["counts"] = counts
    all_result = call("list.all.total",
                      lambda: client.list_products(1, 1, None, shop_id))
    out["all_total"] = (((all_result["value"].get("data") or {}).get("page") or {})
                        .get("totalSize")) if all_result["ok"] else None

    shops = call("shops", lambda: client.shops())
    out["shops_endpoint"] = {
        "ok": shops["ok"],
        "shop_count": len(shops["value"]) if shops["ok"] else None,
        "note": "Alibaba ICBU 的 shops 接口实测不返回 shopInfoList；店铺以商品上的 shopId 为准",
    }

    drafts = call("list.draft.page1",
                  lambda: client.list_products(1, 3, "draft", shop_id))
    items = ((drafts["value"].get("data") or {}).get("page") or {}).get("list") or [] \
        if drafts["ok"] else []
    out["draft_list"] = {
        "ok": drafts["ok"],
        "sample": [{"id": str(it.get("id")),
                    "subject": str(it.get("subject") or "")[:60],
                    "dxmState": it.get("dxmState"),
                    "categoryId": str(it.get("categoryId")),
                    "priceType": it.get("priceType")} for it in items],
    }

    # 分页参数核验：ICBU 列表接口只认 pageNo；旧版 list_products 误用 page。
    pagination_fixed = bool(re.search(
        r'"pageNo"\s*:\s*page\b',
        (ROOT / "src/aeclone/integrations/alibaba.py").read_text(
            encoding="utf-8", errors="ignore")))

    def _raw_list(page):
        return client._get("/alibabaIcbuProduct/list.json",
                           {"pageNo": page, "pageSize": 10,
                            "dxmState": "draft", "shopId": shop_id})

    page_probe = call("list.draft.page2.client",
                      lambda: client.list_products(2, 10, "draft", shop_id))
    raw_page2 = call("list.draft.page2.pageNo", lambda: _raw_list(2))
    page1 = call("list.draft.page1.pageNo", lambda: _raw_list(1))

    def _ids(result):
        page = ((result.get("value") or {}).get("data") or {}).get("page") or {}
        return [str(x.get("id")) for x in (page.get("list") or [])]

    ids_client_p2, ids_raw_p2, ids_raw_p1 = (_ids(page_probe), _ids(raw_page2),
                                             _ids(page1))
    client_page2_returns_page1 = bool(
        ids_client_p2 and ids_raw_p1 and ids_client_p2[0] == ids_raw_p1[0])
    out["pagination_probe"] = {
        "code_pagination_fixed": bool(pagination_fixed),
        "api_param": "pageNo",
        "client_list_products_page2_first_id": ids_client_p2[0] if ids_client_p2 else None,
        "raw_pageNo2_first_id": ids_raw_p2[0] if ids_raw_p2 else None,
        "raw_pageNo1_first_id": ids_raw_p1[0] if ids_raw_p1 else None,
        "client_page2_returns_page1": client_page2_returns_page1,
        "match": bool(pagination_fixed) and not client_page2_returns_page1,
        "note": "旧版 integrations/alibaba.py 用 params['page']，接口要 pageNo；"
                "文档 §2 的 page 参数以代码 pageNo 为准",
    }

    # 草稿数据体检：类目缺失/None、SKU 数据缺失、价格模型分布。
    # 修复后直接用 client.list_products；旧代码则用只读 _get + pageNo 直采绕过 bug。
    survey = []
    for page in (1, 2, 3):
        if pagination_fixed:
            result = call(f"list.draft.survey.p{page}",
                          lambda p=page: client.list_products(p, 10, "draft", shop_id))
        else:
            result = call(f"list.draft.survey.p{page}", lambda p=page: _raw_list(p))
        if not result["ok"]:
            break
        rows = ((result["value"].get("data") or {}).get("page") or {}).get("list") or []
        survey.extend(rows)
        if len(rows) < 10:
            break
    _seen_ids, _uniq = set(), []
    for it in survey:
        _pid = str(it.get("id") or "")
        if _pid and _pid in _seen_ids:
            continue
        _seen_ids.add(_pid)
        _uniq.append(it)
    survey = _uniq
    bad_category = [str(it.get("id")) for it in survey
                    if not _digits(it.get("categoryId"))]
    no_sku = [str(it.get("id")) for it in survey
              if not it.get("productSku")]
    price_types: dict[str, int] = {}
    for it in survey:
        key = str(it.get("priceType") or "")
        price_types[key] = price_types.get(key, 0) + 1
    out["draft_survey"] = {
        "checked": len(survey),
        "category_invalid_ids": bad_category[:10],
        "category_invalid_count": len(bad_category),
        "no_productSku_count": len(no_sku),
        "price_types": price_types,
        "note": "categoryId 为 None/空 的草稿直接保存会提交无效类目",
    }

    # 选一个「类目有效」的草稿做详情 + 类目/属性/schema
    candidates = [it for it in survey
                  if _digits(it.get("categoryId")) and it.get("productSku") is not None]
    pick = candidates[0] if candidates else (survey[0] if survey else None)
    if not pick:
        out["detail"] = {"ok": False, "error": "没有可读取的草稿"}
        return out
    pid = str(pick.get("id"))
    out["picked_product_id"] = pid
    detail = call("detail", lambda: client.product_detail(pid))
    if not detail["ok"]:
        out["detail"] = {"ok": False, "error": detail["error"]}
        return out
    product = detail["value"]
    attrs = _json_list(product.get("attributes"))
    sku_rows = _json_list(product.get("productSku"))
    sku_groups = _json_list(product.get("productSku"))
    sku_attr_count = sum(len(_json_list(g.get("attributes"))) for g in sku_groups
                         if isinstance(g, dict))
    out["detail"] = {
        "ok": True,
        "field_count": len(product),
        "subject": str(product.get("subject") or "")[:80],
        "categoryId": str(product.get("categoryId")),
        "fullCid": str(product.get("fullCid")),
        "dxmState": str(product.get("dxmState")),
        "productStatusType": product.get("productStatusType"),
        "priceType": product.get("priceType"),
        "keywords": str(product.get("keywords") or "")[:120],
        "main_image_count": len([u for u in str(product.get("mainImage") or "").split(",")
                                 if u.strip()]),
        "detail_image_count": len(_json_list(product.get("detailImage"))),
        "attribute_count": len(attrs),
        "required_attribute_names": [a.get("nameEn") for a in attrs if _int(a.get("required")) == 1],
        "has_productSku": bool(product.get("productSku")),
        "sku_attr_count": sku_attr_count,
        "textDesc_len": len(str(product.get("textDesc") or "")),
        "bulkDiscountPrices": str(product.get("bulkDiscountPrices") or "")[:200],
        "wholesaleTrade_keys": sorted(
            (json.loads(product.get("wholesaleTrade") or "{}")
             if isinstance(product.get("wholesaleTrade"), str)
             else (product.get("wholesaleTrade") or {})).keys()),
        "raw_sku_rows": len(sku_rows),
    }

    category_id = str(product.get("categoryId") or "")
    if _digits(category_id):
        cat = call("category.detail", lambda: client.category_detail(category_id, shop_id))
        attr_res = call("category.attributes",
                        lambda: client.category_attributes(category_id))
        schema_res = call("category.schema", lambda: client.category_schema(category_id))
        required = []
        attrs_total = 0
        if attr_res["ok"]:
            all_attrs = attr_res["value"].get("attributes") or []
            attrs_total = len(all_attrs)
            required = [a for a in all_attrs if _int(a.get("required")) == 1]
        schema_fields = schema_res["value"].get("fields") or [] if schema_res["ok"] else []
        out["category"] = {
            "ok": cat["ok"] and attr_res["ok"] and schema_res["ok"],
            "categoryId": category_id,
            "name": (cat["value"] if cat["ok"] else {}).get("nameZh"),
            "name_en": (cat["value"] if cat["ok"] else {}).get("nameEn"),
            "isleaf": (cat["value"] if cat["ok"] else {}).get("isleaf"),
            "level": (cat["value"] if cat["ok"] else {}).get("level"),
            "nodePathId": (cat["value"] if cat["ok"] else {}).get("nodePathId"),
            "attributes_total": attrs_total,
            "required_count": len(required),
            "required_names": [a.get("nameEn") for a in required],
            "schema_fields": [f.get("fieldId") for f in schema_fields],
            "required_attrs_compact": [
                {"attrNameId": a.get("attrNameId"), "nameEn": a.get("nameEn"),
                 "required": a.get("required"),
                 "value_count": len(_json_list(a.get("values")))}
                for a in required],
            "_category_raw": {"required": required, "schema": schema_fields},
        }
        out["dry_run"] = evaluate_detail_offline(cfg, product, required, schema_fields)
        out["_detail_raw"] = product
        # 校验类目选择是否真的会进 payload（真实配置下）
        try:
            cfg2 = copy.deepcopy(cfg)
            cfg2.set("alibaba_category_id", "100010756")
            cfg2.set("alibaba_full_cid", "18,100005657,100005663,100010756")
            save2 = _build_save(cfg2, product)
            out["category_selection_probe"] = {
                "selected_category_id": "100010756",
                "payload_categoryId": str(save2.get("categoryId")),
                "payload_fullCid": str(save2.get("fullCid")),
                "applied": str(save2.get("categoryId")) == "100010756",
            }
        except Exception as exc:  # noqa: BLE001
            out["category_selection_probe"] = {"error": redact(str(exc), cfg)[:160]}
    return out


# ---------------------------------------------------------------- 结论汇总
def build_verdict(cfg, config: dict, live: dict | None, offline: bool,
                  contract: dict | None = None, gate: dict | None = None,
                  make_probe: dict | None = None,
                  real_evidence: dict | None = None) -> dict:
    contract = contract or {}
    gate = gate or {}
    make_probe = make_probe or {}
    blockers: list[dict] = []
    if not make_probe.get("ok", False):
        blockers.append({
            "rank": 1, "class": "代码缺陷", "severity": "P0",
            "title": "make_client 拿不到任何 Cookie 来源（UI 保存入口不可用）",
            "evidence": "alibaba_publish.make_client 探测失败："
                        + str(make_probe.get("error") or "")[:160],
            "fix": "配置 alibaba_cookies 或 manual_cookies（店铺与登录），或本机 Chrome 登录店小秘",
        })
    if not config["template_product_id"]:
        blockers.append({
            "rank": 6, "class": "模板", "severity": "P1(仅 SMT 路径)",
            "title": "template_product_id 为空：publisher.py 真实保存会直接中止",
            "evidence": "services/publisher.py:2335-2337 无模板时 _save_via_api 返回 None，"
                        "run() raise「未选择产品模板」；ICBU 直连链路不依赖模板",
            "fix": "SMT 路径在商品设置选模板；ICBU 路径无需模板",
        })
    survey = (live or {}).get("draft_survey") or {}
    if survey.get("category_invalid_count"):
        blockers.append({
            "rank": 4, "class": "商品数据", "severity": "P0(该商品)",
            "title": f"草稿数据脏：{survey['category_invalid_count']}/{survey.get('checked')} "
                     "个草稿 categoryId 非数字（None/空）",
            "evidence": "只读列表体检；validate_alibaba_save 目前不阻断无效/非叶子类目",
            "fix": "保存前按类目接口校验 isleaf + required；脏类目商品先人工选类目",
        })
    if not offline and live:
        detail = live.get("detail") or {}
        if detail.get("ok"):
            issues = ((live.get("dry_run") or {}).get("alibaba_dry_run") or {}).get("issues")
            if issues:
                blockers.append({
                    "rank": 3, "class": "商品数据", "severity": "P1",
                    "title": f"真实草稿本地校验有 {len(issues)} 项问题",
                    "evidence": "；".join(str(i) for i in issues[:5]),
                    "fix": "按 validate_alibaba_save 输出补齐后再保存",
                })
        elif live.get("ok"):
            blockers.append({
                "rank": 3, "class": "商品数据", "severity": "P1",
                "title": "无法抽检真实草稿详情",
                "evidence": str(live.get("detail") or {}).get("error") or "未知",
                "fix": "检查草稿可读性与账号权限",
            })
    pagination = contract.get("list_pagination") or {}
    probe = ((live or {}).get("pagination_probe")) or {}
    if pagination and pagination.get("match") is False:
        blockers.append({
            "rank": 2, "class": "代码缺陷", "severity": "P1",
            "title": "ICBU 列表分页参数写错：client 发 page，接口要 pageNo",
            "evidence": "live_readonly.pagination_probe：client 第2页与原始第1页 id 相同；"
                        "integrations/alibaba.py:list_products",
            "fix": "params['pageNo']=page（pageSize 保留）；文档 §2 以代码 pageNo 为准",
        })
    gaps = probe_known_gaps(cfg)
    open_gap_names = [name for name, item in gaps.items() if item.get("gap")]
    if open_gap_names:
        blockers.append({
            "rank": 2, "class": "代码缺陷", "severity": "P1",
            "title": "本地探测仍有未修复的载荷缺口",
            "evidence": "、".join(open_gap_names),
            "fix": "见 known_gaps 每项的 source；修复后本项自动消失",
        })
    ui = contract.get("ui_exposure") or {}
    gate_ready = bool(gate.get("ready_to_attempt_real_save"))
    real_ev = real_evidence or {}
    answer = (
        "两条链路分开看：ICBU 直连对「已有商品保存」"
        + ("已具备进入真实保存阶段的条件（配置/类目/必填/载荷本地校验通过）"
           if gate_ready else "尚未具备进入真实保存阶段的条件")
        + ("；op=1 保存已在用户授权下真实验证成功（商品 220114598434958580，"
           "平台返回「您的产品编辑成功！」，回读 changed_fields=0）"
           if real_ev.get("available") and str(real_ev.get("op")) == "1"
           else "；真实保存仍未验证")
        + "；op=2/3/5 发布/待发布/定时本轮未验证；"
        + ("UI 已暴露 op=" + ("1/3/2" if ui.get("ui_passes_op") else "1")
           + (("，未暴露 op=5" if not ui.get("ui_op5_exposed") else "，含 op=5"))
           if ui.get("ui_alibaba_page_exists") or ui.get("ui_calls_save_to_alibaba")
           else "UI 未暴露保存动作")
        + "。SMT（publisher.py）链路仍指向速卖通 add.json/smtEditSave.txt，"
          "且 store_id=9466975 在 SMT 列表为空，不能用于 Alibaba 刊登。"
    )
    verdict = {
        "chains": {
            "icbu_direct": {
                "entry": "integrations/alibaba.py + services/alibaba_payload.py + "
                         "services/alibaba_publish.py；UI「Alibaba 刊登」页",
                "cookie_sources": ["config.alibaba_cookies", "config.manual_cookies",
                                   "本机 Chrome read_cookies"],
                "op_semantics": {
                    "1": "已接入；真实保存已验证（用户授权一次）",
                    "2": "已接入 UI 下拉（提交审核/发布）；真实行为未验证"
                         if ui.get("ui_passes_op") else "UI 未暴露",
                    "3": "已接入 UI 下拉（保存并移入待发布）；真实行为未验证"
                         if ui.get("ui_passes_op") else "UI 未暴露",
                    "5": "未暴露" if not ui.get("ui_op5_exposed") else "已接入（未验证）",
                    "real_evidence": "op=1：docs/alibaba-real-save-verification.json；"
                                     "op=2/3/5：未验证",
                },
                "gate_ready_for_real_save_stage": gate_ready,
            },
            "smt_publisher": {
                "entry": "services/publisher.py → integrations/dianxiaomi.py（速卖通 SMT）",
                "save_endpoint": (contract.get("smt_path_separation") or {})
                                 .get("publisher_save_endpoint"),
                "store_id_in_use": config.get("store_id"),
                "store_empty_in_smt": True,
                "usable_for_alibaba": False,
                "risk": "Alibaba 平台复用这 12 页时，发布队列/发布动作走 SMT 接口；"
                        "若 store 指向有速卖通商品的店铺，存在写错平台/端点的风险",
            },
        },
        "icbu_direct_save_op1": {
            "status": "已验证（真实写入 op=1，用户授权一次）",
            "detail": "商品 220114598434958580 走 run_alibaba_product(dry_run=False, op='1', "
                      "use_ai=False)：平台返回「您的产品编辑成功！」，回读 changed_fields=0，"
                      "草稿总数 28→28，仍在草稿；证据 docs/alibaba-real-save-verification.json。"
                      "只证明「编辑已有草稿不破坏数据」，不代表发布/审核通过",
            "can_publish_now": True,
        },
        "icbu_publish_op2": {
            "status": "op=2/3 已进 UI 下拉但真实行为未验证；op=5 未暴露",
            "detail": "services/alibaba_publish.py 定义 op=1/2/3/5；UI 传 op 参数；"
                      "真实发布/审核语义无本轮证据",
            "can_publish_now": False,
        },
        "icbu_new_listing": {
            "status": "未实现/未验证",
            "detail": "save_to_alibaba 需要已有 ICBU 商品 id；AI/图片/变种流水线（Publisher）未接入 "
                      "alibaba_payload；无新建商品路径",
            "can_publish_now": False,
        },
        "smt_publisher_path": {
            "status": "不可用于 Alibaba；在 Alibaba 平台下有写错端点的风险",
            "detail": "publisher.py 保存走 DianxiaomiClient → /api/smtProduct/add.json + "
                      "smtEditSave.txt（速卖通），且 store_id=9466975 在 SMT 列表为空，"
                      "Alibaba 平台复用这 12 页时发布队列显示空",
            "can_publish_now": False,
        },
        "answer": answer,
        "blockers": sorted(blockers, key=lambda b: b["rank"]),
    }
    return verdict


def _load_detail_for_probe(live: dict) -> dict:
    raw = live.get("_detail_raw")
    return dict(raw) if isinstance(raw, dict) and raw else {}


def collect(cfg, offline: bool = False, shop_hint: str = "") -> dict:
    started = _now()
    started_mono = time.monotonic()
    report: dict = {
        "schema": "alibaba-publish-readiness/1",
        "generated_at": started,
        "mode": "offline-fixture" if offline else "live-readonly+dry-run",
        "read_only": True,
        "writes_performed": 0,
        "ai_calls": 0,
        "secret_policy": "Cookie/api_key 不打印、不落盘；本文件只含状态与计数",
        "config": config_snapshot(cfg),
        "repo": repo_snapshot(),
        "real_save_evidence": real_save_evidence(),
        "op_semantics": op_semantics(),
        "snapshot_note": "真实店铺数据在变化（在线商品数会波动）；本文件是生成时刻的快照，"
                         "不构成「此刻实时」保证",
    }
    if shop_hint:
        report["shop_hint"] = shop_hint
    report["contract"] = contract_checks()
    report["known_gaps"] = probe_known_gaps(cfg)
    report["make_client_probe"] = make_client_probe(cfg)
    report["new_pipeline"] = pipeline_probe()

    live = None
    live_detail_raw = None
    live_category_raw = None
    if offline:
        detail = _synthetic_detail()
        report["offline"] = {
            "fixture": "synthetic icbu draft（非真实商品数据）",
            "evaluation": evaluate_detail_offline(
                cfg, detail, SYNTHETIC_REQUIRED_ATTRS, SYNTHETIC_SCHEMA_FIELDS),
        }
        report["new_pipeline"]["dry_run_probe"] = pipeline_dry_run_probe(
            cfg, detail, SYNTHETIC_REQUIRED_ATTRS, SYNTHETIC_SCHEMA_FIELDS)
        real_ev = report["real_save_evidence"]
        report["verification_levels"] = {
            "read_only_verified": [],
            "dry_run_verified": ["合成商品：build_alibaba_save + validate_alibaba_save",
                                 "合成商品：Publisher.run(dry_run=True) 步骤语义",
                                 "合成商品：新 alibaba_pipeline 桩客户端 dry-run（不保存）"],
            "real_write_verified": (["op=1 保存（用户授权一次）— " + real_ev["path"]]
                                    if real_ev.get("available")
                                    and str(real_ev.get("op")) == "1" else []),
            "not_verified_real_write": ["op=2 发布 / op=3 移入待发布 / op=5 定时",
                                        "审核状态拉取与审核失败原因",
                                        "新建 ICBU 商品（当前无代码路径）"],
        }
    else:
        attempts, cookies, source = cookie_sources(cfg)
        report["cookie_sources"] = attempts
        if not cookies:
            report["live_readonly"] = {"ok": False,
                                       "error": "没有可用 Cookie（只读核验无法进行）"}
        else:
            client = alibaba_client(cookies)
            try:
                live = live_readonly(cfg, client, source)
                # 真实商品详情只用于本地 pipeline 探针，不整份落盘（含大量外部 URL）
                live_detail_raw = live.pop("_detail_raw", None)
                _cat_live = live.get("category") or {}
                live_category_raw = _cat_live.pop("_category_raw", None)
                report["live_readonly"] = live
            except Exception as exc:  # noqa: BLE001
                live_detail_raw = None
                live = {"ok": False,
                        "error": redact(f"{type(exc).__name__}: {exc}", cfg)[:220]}
                report["live_readonly"] = live
        report["verification_levels"] = {
            "read_only_verified": [
                "ICBU 商品列表/详情（110 字段）",
                "类目详情/必填属性/schema",
                "草稿数据体检（类目/SKU/价格模型分布）",
            ] if live and live.get("detail", {}).get("ok") else [],
            "dry_run_verified": [
                "alibaba_payload：build_alibaba_save + validate_alibaba_save（不保存）",
                "Publisher.run(dry_run=True)：标准化/价格/图片计划/未保存标记",
            ] if live and live.get("dry_run") else [],
            "real_write_verified": (
                ["op=1 保存（用户授权一次）— docs/alibaba-real-save-verification.json"]
                if (report.get("real_save_evidence") or {}).get("available")
                and str((report.get("real_save_evidence") or {}).get("op")) == "1" else []),
            "not_verified_real_write": [
                "publish(op=2) / 移入待发布(op=3) / 定时(op=5) 真实行为",
                "审核状态拉取与失败原因",
                "新建 ICBU 商品（当前无代码路径）",
            ],
        }
    if live and (live.get("dry_run") or {}).get("product_id"):
        detail_for_pipeline = dict(live_detail_raw or {})
        if detail_for_pipeline:
            cat_raw = dict(live_category_raw or {})
            report["new_pipeline"]["dry_run_probe"] = pipeline_dry_run_probe(
                cfg, detail_for_pipeline,
                cat_raw.get("required") or [],
                cat_raw.get("schema") or [])
    report["readiness_gate"] = readiness_gate(cfg, report["contract"], live, offline)
    report["verdict"] = build_verdict(
        cfg, report["config"], live, offline, contract=report["contract"],
        gate=report["readiness_gate"], make_probe=report["make_client_probe"],
        real_evidence=report["real_save_evidence"])
    report["data_blockers"] = _data_blockers(report)
    report["generated_elapsed_sec"] = round(time.monotonic() - started_mono, 2)
    return report


def _data_blockers(report: dict) -> list[dict]:
    """把只读发现的商品数据问题单独列出（不是代码缺陷，但同样会卡保存）。"""
    out: list[dict] = []
    survey = ((report.get("live_readonly") or {}).get("draft_survey")) or {}
    if survey:
        out.append({
            "id": "draft-category-invalid",
            "severity": "P0" if survey.get("category_invalid_count") else "OK",
            "count": survey.get("category_invalid_count"),
            "checked": survey.get("checked"),
            "detail": "草稿 categoryId 不是数字（None/空）。validate 现已能阻断（需代码修复生效），"
                      "但这些商品必须先人工选类目/修复数据才能保存；真实店铺快照已记录",
        })
        if survey.get("no_productSku_count"):
            out.append({
                "id": "draft-no-productsku",
                "severity": "P1",
                "count": survey.get("no_productSku_count"),
                "checked": survey.get("checked"),
                "detail": "草稿没有 productSku 数据；priceType=sku_price 的商品无法形成有效 SKU 报价",
            })
    detail = ((report.get("live_readonly") or {}).get("detail")) or {}
    if detail.get("ok") and not _digits(detail.get("categoryId")):
        out.append({
            "id": "picked-product-category-invalid",
            "severity": "P0",
            "detail": f"被抽检商品 {detail.get('subject')} 的 categoryId="
                      f"{detail.get('categoryId')!r}，不能安全保存",
        })
    return out


REAL_SAVE_EVIDENCE = ROOT / "docs/alibaba-real-save-verification.json"


def real_save_evidence() -> dict:
    """读取「用户授权一次的真实 op=1 保存」证据（只读该 JSON，不重新提交）。"""
    rel = "docs/alibaba-real-save-verification.json"
    if not REAL_SAVE_EVIDENCE.exists():
        return {"available": False, "path": rel,
                "note": "本轮无真实写入；op=1 状态按未验证处理"}
    try:
        data = json.loads(REAL_SAVE_EVIDENCE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "path": rel,
                "error": f"{type(exc).__name__}: {exc}"[:160]}
    pipeline = data.get("pipeline") or {}
    save_result = pipeline.get("save_result") or {}
    inner = save_result.get("data") if isinstance(save_result.get("data"), dict) else {}
    steps = pipeline.get("steps") or []
    return {
        "available": True,
        "path": rel,
        "generated_at": data.get("generated_at"),
        "product_id": str(data.get("product_id") or ""),
        "op": str(data.get("op") or ""),
        "authorized_by_user": str(data.get("authorized_by_user") or "")[:120],
        "save_returned": {
            "code": save_result.get("code"),
            "msg": save_result.get("msg"),
            "inner_msg": inner.get("msg"),
            "productId": inner.get("productId"),
        },
        "changed_fields_count": len(data.get("changed_fields") or {}),
        "draft_total_before": data.get("draft_total_before"),
        "draft_total_after": data.get("draft_total_after"),
        "after_state": (data.get("after") or {}).get("dxmState"),
        "pipeline_ok": bool(pipeline.get("ok")),
        "saved": bool(pipeline.get("saved")),
        "preview": bool(pipeline.get("preview")),
        "elapsed_sec": pipeline.get("elapsed_sec"),
        "steps_ok": sum(1 for x in steps if x.get("ok")),
        "steps_total": len(steps),
        "scope": "仅 op=1 保存（不提交审核）；op=2/3/5 仍未验证",
        "note": "该写入由 Lead 在用户授权下一次执行；本工具只读证据文件，绝不重复提交",
    }


def pipeline_probe() -> dict:
    """探测新增的 AI→Alibaba 流水线模块（存在则记录能力，不改代码）。"""
    path = ROOT / "src/aeclone/services/alibaba_pipeline.py"
    if not path.exists():
        return {"exists": False}
    src = path.read_text(encoding="utf-8", errors="ignore")
    out = {
        "exists": True,
        "has_run_entry": bool(re.search(r"def run\w*\(", src)),
        "has_dry_run": "dry_run" in src,
        "refuses_env_save": "AURORA_NO_SAVE" in src and "AURORA_TEST" in src,
        "imports_payload_build": "build_alibaba_save" in src,
        "imports_validate": "validate_alibaba_save" in src,
        "source_sha256_16": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
    }
    try:
        import importlib
        module = importlib.import_module("aeclone.services.alibaba_pipeline")
        entries = [name for name in dir(module)
                   if not name.startswith("_") and "run" in name.lower()
                   and callable(getattr(module, name))]
        out["importable"] = True
        out["entries"] = entries
    except Exception as exc:  # noqa: BLE001
        out["importable"] = False
        out["import_error"] = f"{type(exc).__name__}: {exc}"[:160]
    return out


def repo_snapshot() -> dict:
    """记录代码版本，便于区分「修复前/修复后」的报告。只读。"""
    def head():
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        except Exception:
            return ""
    files = {}
    for rel in ("src/aeclone/services/alibaba_payload.py",
                "src/aeclone/services/alibaba_publish.py",
                "src/aeclone/integrations/alibaba.py"):
        path = ROOT / rel
        try:
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        except Exception:
            files[rel] = ""
    return {"git_head": head(), "source_sha256_16": files}


def _source_section(text: str, marker: str, size: int = 12000) -> str:
    """取源码中从 marker 到下一个类方法定义之间的片段（用于 UI 埋点核对）。"""
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[-1][:size]
    cut = tail.find("\n    def ")
    return tail if cut < 0 else tail[:cut]


def op_semantics() -> dict:
    """op 语义：文档/代码/真实证据分开列，缺证据就写未验证。"""
    from aeclone.services import alibaba_publish as AP
    app_src = (ROOT / "src/aeclone/ui/app.py").read_text(encoding="utf-8", errors="ignore")
    alibaba_ui = _source_section(app_src, "def _p_alibaba_listing")
    if not alibaba_ui:
        alibaba_ui = _source_section(app_src, "def _alibaba_cards") + app_src
    ui_passes_op = bool(re.search(r"save_to_alibaba\([^)]*\bop\s*=", app_src))
    op5_in_ui = bool(re.search(r"op=5|定时发布（op=5", alibaba_ui))
    return {
        "documented": {
            "1": "保存（编辑/草稿）；返回「您的产品编辑成功！」",
            "2": "发布（立即）/在线更新",
            "3": "保存并移入待发布",
            "5": "定时发布",
            "source": "docs/ALIBABA_RULES.md §3（前端 JS 逆向 + 历史实测）",
        },
        "code": {
            "OP_SAVE": AP.OP_SAVE, "OP_PUBLISH": AP.OP_PUBLISH,
            "OP_MOVE_PENDING": AP.OP_MOVE_PENDING, "OP_SCHEDULE": AP.OP_SCHEDULE,
            "ui_passes_op": ui_passes_op,
            "ui_op5_exposed": op5_in_ui,
            "source": "src/aeclone/services/alibaba_publish.py:36-49；"
                      "src/aeclone/ui/app.py 的 Alibaba 刊登页",
        },
        "live_evidence": {
            "op1": "历史文档记录 2026-11-21 商品 220114598434958580 保存成功；本轮未复验",
            "op2": "未验证（本轮禁止写入，无真实调用证据）",
            "op3": "未验证（本轮禁止写入，无真实调用证据）",
            "op5": "未验证（本轮禁止写入，无真实调用证据）",
            "note": "店小秘返回的 data.msg 只在真实提交后可取得；不得用推测代替",
        },
    }


def pipeline_dry_run_probe(cfg, detail: dict, attr_defs: list | None = None,
                           schema_fields: list | None = None) -> dict:
    """用桩客户端跑新增 AI→Alibaba 流水线的 dry-run：不联网、不调 AI、不保存。"""
    try:
        from aeclone.services import alibaba_pipeline as pipeline
    except Exception as exc:  # noqa: BLE001
        return {"available": False,
                "error": f"{type(exc).__name__}: {exc}"[:160]}
    entry = getattr(pipeline, "run_alibaba_product", None)
    if not callable(entry):
        return {"available": False, "error": "未找到 run_alibaba_product"}

    class _StubClient:
        def __init__(self):
            self.saved = []
            self.calls = []

        def product_detail(self, pid):
            self.calls.append("product_detail")
            return dict(detail)

        def required_attributes(self, cid):
            self.calls.append("required_attributes")
            return list(attr_defs or [])

        def category_schema(self, cid):
            self.calls.append("category_schema")
            return {"fields": list(schema_fields or [])}

        def check_banned_words(self, text):
            self.calls.append("check_banned_words")
            return {"code": 0, "data": []}

        def save_product(self, *a, **kw):
            self.saved.append((a, kw))
            raise AssertionError("dry_run 不应调用 save_product")

    stub = _StubClient()
    try:
        result = entry(str(detail.get("id") or ""), cfg, dry_run=True, op="1",
                       use_ai=False, client=stub)
    except Exception as exc:  # noqa: BLE001
        return {"available": True, "ok": False,
                "error": redact(f"{type(exc).__name__}: {exc}", cfg)[:220],
                "save_called": bool(stub.saved)}
    return {
        "available": True,
        "ok": bool(getattr(result, "ok", False)),
        "error": redact(getattr(result, "error", "") or "", cfg)[:200],
        "preview": bool(getattr(result, "preview", False)),
        "saved": bool(getattr(result, "saved", False)),
        "save_called": bool(stub.saved),
        "issues": [redact(x, cfg)[:160] for x in (getattr(result, "issues", None) or [])[:8]],
        "warnings": [redact(x, cfg)[:160] for x in (getattr(result, "warnings", None) or [])[:5]],
        "subject": str(getattr(result, "subject", "") or "")[:80],
        "keyword_count": len(getattr(result, "keywords", None) or []),
        "category_id": str(getattr(result, "category_id", "") or ""),
        "steps": [{"name": getattr(s, "name", ""), "ok": bool(getattr(s, "ok", False)),
                   "detail": redact(getattr(s, "detail", "") or "", cfg)[:140]}
                  for s in (getattr(result, "steps", None) or [])],
        "client_calls": stub.calls,
    }


def readiness_gate(cfg, contract: dict, live: dict | None, offline: bool) -> dict:
    """给出「配置正确 + 选好类目 + 商品数据完整」时能否进入真实保存阶段的判定。"""
    make_ok = make_client_probe(cfg)["ok"]
    criteria: list[dict] = []

    def add(key, ok, expected, evidence):
        criteria.append({"key": key, "ok": bool(ok),
                         "expected": expected, "evidence": evidence})

    add("cookie_source_usable", make_ok,
        "UI 保存入口能拿到店小秘 Cookie（alibaba_cookies 或 Chrome 兜底）",
        "services/alibaba_publish.py:8-18")
    add("ui_save_entry_exists",
        bool((contract.get("ui_exposure") or {}).get("ui_calls_save_to_alibaba")),
        "UI 存在 Alibaba 刊登保存入口",
        (contract.get("ui_exposure") or {}).get("source"))
    real_ev = real_save_evidence()
    add("op1_real_save_verified",
        bool(real_ev.get("available")) and str(real_ev.get("op")) == "1",
        "op=1 至少有一次用户授权的真实保存证据（只读证据文件）",
        real_ev.get("path"))

    detail = (live or {}).get("detail") or {}
    dry = (live or {}).get("dry_run") or {}
    if offline:
        synthetic = _synthetic_detail()
        build = alibaba_dry_build(cfg, synthetic, SYNTHETIC_REQUIRED_ATTRS,
                                  SYNTHETIC_SCHEMA_FIELDS)
        add("category_valid", _digits(synthetic.get("categoryId")),
            "类目 ID 为数字且是叶子类目", "合成商品")
        add("required_attributes_resolvable",
            not any("必填属性缺失" in i for i in build["issues"]),
            "类目 required 属性可补齐/已存在", "合成商品 build+validate")
        add("payload_validation_ok", build["issues_count"] == 0,
            "build_alibaba_save + validate_alibaba_save 0 问题",
            f"issues={build['issues']}")
        add("product_data_complete",
            build["main_image_count"] >= 1 and build["textDesc_len"] > 0,
            "主图/详情/文字描述齐全", "合成商品")
        gate_source = "合成样例（离线，不代表真实商品）"
    elif detail.get("ok"):
        cat = (live.get("category") or {})
        gate_category = _digits(detail.get("categoryId")) and str(
            cat.get("isleaf")) in ("1", "True", "true")
        add("category_valid_leaf", gate_category,
            "商品类目为数字且 isleaf=1",
            f"categoryId={detail.get('categoryId')} isleaf={cat.get('isleaf')}")
        issues = (dry.get("alibaba_dry_run") or {}).get("issues") or []
        add("required_attributes_resolvable",
            not any("必填属性缺失" in str(i) for i in issues),
            "required 属性全部存在（或可自动补齐）",
            f"issues={issues[:5]}")
        add("payload_validation_ok", not issues,
            "build_alibaba_save + validate_alibaba_save 0 问题",
            f"issues={issues[:5]}")
        has_sku_problem = (str(detail.get("priceType")) == "sku_price"
                           and not (cat.get("sku_attr_count") or detail.get("sku_attr_count")))
        add("product_data_complete",
            (detail.get("main_image_count") or 0) >= 1
            and (detail.get("textDesc_len") or 0) > 0
            and not has_sku_problem,
            "主图/详情文字齐全；sku_price 必须有真实 SKU 数据",
            f"main_images={detail.get('main_image_count')} "
            f"textDesc_len={detail.get('textDesc_len')} sku_price_missing={has_sku_problem}")
        add("op1_save_exposed", True, "UI 只能触发 op=1 保存", "UI do_save 未传 op")
        add("op2_publish_exposed", False, "UI 可触发 op=2 提交发布",
            "UI 未暴露 op=2/3/5")
        gate_source = f"真实商品 {detail.get('product_id') or live.get('picked_product_id')}"
    else:
        gate_source = "无可用真实商品"
        add("category_valid_leaf", False, "需要真实商品详情", "live detail 不可用")
        add("payload_validation_ok", False, "需要真实商品详情", "live detail 不可用")

    gate_by_key = {c["key"]: c for c in criteria}
    save_ready_keys = ("cookie_source_usable", "ui_save_entry_exists",
                       "category_valid_leaf" if not offline else "category_valid",
                       "required_attributes_resolvable", "payload_validation_ok",
                       "product_data_complete")
    missing = [k for k in save_ready_keys
               if k in gate_by_key and not gate_by_key[k]["ok"]]
    return {
        "source": gate_source,
        "criteria": criteria,
        "ready_to_attempt_real_save": not missing,
        "missing_criteria": missing,
        "hypothetical_correct_config": {
            "assumptions": [
                "Cookie 来源可用（make_client 修复或 alibaba_cookies 已配置）",
                "商品类目为有效叶子类目",
                "required 属性齐全，build+validate 0 问题",
                "主图/详情文字/价格模型与 SKU 数据完整",
            ],
            "can_enter_real_save_stage": (not missing) if make_ok else
                (not [k for k in missing if k != "cookie_source_usable"]),
            "note": "这只代表「具备进入真实保存阶段的条件」，真实 op=1/2/3 提交与平台回读本轮未验证；"
                    + ("UI 已暴露 op=1/3/2" if (contract.get("ui_exposure") or {}).get("ui_passes_op")
                       else "UI 仅暴露 op=1")
                    + ("，未暴露 op=5" if not (contract.get("ui_exposure") or {}).get("ui_op5_exposed")
                       else "，含 op=5"),
        },
    }


def _write(report: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Alibaba 刊登就绪度只读核验")
    parser.add_argument("--offline", action="store_true",
                        help="完全离线：只用内置样例商品做本地构建与 dry-run")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSON 输出路径")
    parser.add_argument("--shop", default="", help="只读核验用的店铺 ID（默认取商品上的 shopId）")
    parser.add_argument("--strict", action="store_true",
                        help="存在 P0 阻断时返回非 0（默认只在脚本自身失败时返回非 0）")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
        report = collect(cfg, offline=args.offline, shop_hint=args.shop)
        if args.shop:
            report["config"]["alibaba_shop_id"] = args.shop
        _write(report, Path(args.out).expanduser())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False,
                          "error": redact(f"{type(exc).__name__}: {exc}")[:400],
                          "trace": redact(traceback.format_exc())[-1200:]},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    summary = {
        "ok": True,
        "mode": report["mode"],
        "out": str(Path(args.out).expanduser()),
        "answer": report["verdict"]["answer"],
        "p0_blockers": len([b for b in report["verdict"]["blockers"]
                            if b["severity"].startswith("P0")]),
        "blockers": [b["title"] for b in report["verdict"]["blockers"]],
    }
    print(json.dumps(summary, ensure_ascii=False))
    if args.strict and summary["p0_blockers"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Alibaba 刊登：读取商品 → 构建保存结构 → 必填校验 → 保存（草稿/编辑）。"""
from __future__ import annotations

from ..integrations.alibaba import AlibabaClient
from .alibaba_payload import build_alibaba_save, validate_alibaba_save


def make_client(cfg) -> AlibabaClient:
    raw = str(getattr(cfg, "alibaba_cookies", "") or "").strip()
    if not raw:
        try:
            from ..integrations.chrome_cookies import read_chrome_cookies
            raw = read_chrome_cookies(["dianxiaomi.com"]) or ""
        except Exception:
            raw = ""
    if not raw:
        raise RuntimeError("没有 Alibaba/店小秘 Cookie：请在「设置 → Alibaba 刊登」粘贴")
    return AlibabaClient(raw)


def prepare_save(product_id: str, cfg, overrides: dict | None = None,
                 client: AlibabaClient | None = None) -> tuple[dict, dict, list, list]:
    """返回 (product, save, required_attrs, issues)。"""
    c = client or make_client(cfg)
    product = c.product_detail(str(product_id))
    if not product:
        raise RuntimeError(f"读不到商品 {product_id}")
    cid = str(getattr(cfg, "alibaba_category_id", "") or product.get("categoryId") or "")
    req = c.required_attributes(cid) if cid else []
    schema = c.category_schema(cid)["fields"] if cid else []
    save = build_alibaba_save(product, cfg, overrides, attr_defs=req)
    issues = validate_alibaba_save(product, save, cfg, req, schema)
    return product, save, req, issues


OP_SAVE = "1"          # 保存（编辑/草稿）
OP_PUBLISH = "2"       # 立即发布（或在线更新）
OP_MOVE_PENDING = "3"  # 保存并移入待发布
OP_SCHEDULE = "5"      # 定时发布


def save_to_alibaba(product_id: str, cfg, overrides: dict | None = None,
                    allow_invalid: bool = False, op: str = OP_SAVE) -> dict:
    """执行保存；必填不完整时默认拒绝，allow_invalid=True 时仅警告。"""
    c = make_client(cfg)
    product, save, req, issues = prepare_save(product_id, cfg, overrides, client=c)
    if issues and not allow_invalid:
        raise ValueError("保存前校验未通过：" + "；".join(issues))
    res = c.save_product(product, save, op=op)
    return {"result": res, "issues": issues, "autofilled": save.get("_autofilled") or [],
            "product_id": product.get("id")}

"""Alibaba 刊登：读取商品 → 构建保存结构 → 必填校验 → 保存（草稿/编辑）。"""
from __future__ import annotations

from ..integrations.alibaba import AlibabaClient
from .alibaba_payload import build_alibaba_save, resolve_category, validate_alibaba_save


def _cfg_value(cfg, key: str, default=None):
    """cfg 可能是 AppConfig、dict 或 None（测试/脚本）。"""
    if cfg is None:
        return default
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _normalize_cookies(raw) -> dict | str:
    """Cookie 可能是「a=1; b=2」字符串，也可能是 dict。"""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return str(raw or "").strip()


def _browser_cookies() -> dict:
    """本机浏览器兜底（已登录的店小秘/Chrome）。"""
    # 旧代码这里 import 的 read_chrome_cookies 根本不存在（ImportError 被吞掉，
    # 于是「Chrome 已登录」也报没有 Cookie）。这里改用真实存在的读取函数。
    try:
        from ..integrations.chrome_cookies import read_cookies
        ck = read_cookies("chrome", "dianxiaomi.com") or {}
        if ck:
            return {str(k): str(v) for k, v in ck.items()}
    except Exception:
        pass
    try:
        from ..integrations.browser_cookies import read_best
        src = read_best("dianxiaomi.com")
        if src.ok and src.cookies:
            return {str(k): str(v) for k, v in src.cookies.items()}
    except Exception:
        pass
    return {}


def make_client(cfg) -> AlibabaClient:
    """Cookie 顺序：cfg.alibaba_cookies → cfg.manual_cookies → 本机浏览器。

    只读 Chrome 会让「已经粘贴/读取过店小秘 Cookie」的用户仍被报「没有 Cookie」，
    所以手动配置的 Cookie 必须优先（也便于无浏览器环境）。
    """
    raw = _normalize_cookies(_cfg_value(cfg, "alibaba_cookies", "") or "")
    if not raw:
        raw = _normalize_cookies(_cfg_value(cfg, "manual_cookies", None) or {})
    if not raw:
        raw = _browser_cookies()
    if not raw:
        raise RuntimeError("没有 Alibaba/店小秘 Cookie：请在「设置 → Alibaba 刊登」粘贴 Cookie，"
                           "或在「店铺与登录」读取/粘贴店小秘 Cookie")
    return AlibabaClient(raw)


def prepare_save(product_id: str, cfg, overrides: dict | None = None,
                 client: AlibabaClient | None = None) -> tuple[dict, dict, list, list]:
    """返回 (product, save, required_attrs, issues)。校验与保存使用同一类目上下文。"""
    c = client or make_client(cfg)
    product = c.product_detail(str(product_id))
    if not product:
        raise RuntimeError(f"读不到商品 {product_id}")
    cid, _full_cid = resolve_category(product, cfg, overrides)
    req = c.required_attributes(cid) if cid else []
    schema = c.category_schema(cid)["fields"] if cid else []
    save = build_alibaba_save(product, cfg, overrides, attr_defs=req)
    issues = validate_alibaba_save(product, save, cfg, req, schema)
    return product, save, req, issues


OP_SAVE = "1"          # 保存（编辑/草稿）
OP_PUBLISH = "2"       # 立即发布（或在线更新）
OP_MOVE_PENDING = "3"  # 保存并移入待发布
OP_SCHEDULE = "5"      # 定时发布


def _forced_preview() -> bool:
    """验收/测试模式（AURORA_NO_SAVE / AURORA_TEST）禁止真实写入。"""
    import os
    return bool(os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"))


def _require_license(cfg) -> None:
    """真实保存前的核心层授权校验（AURORA_SKIP_LICENSE=1 由 guard 内部放行）。"""
    try:
        from ..core.guard import require as _require
    except Exception:
        return
    _require(cfg)


def save_to_alibaba(product_id: str, cfg, overrides: dict | None = None,
                    allow_invalid: bool = False, op: str = OP_SAVE) -> dict:
    """执行保存；必填不完整时默认拒绝，allow_invalid=True 时仅警告。

    这是所有真实写入的唯一收口：预演/验收环境与授权不通过时都必须在这里拦住，
    UI 上的「保存」按钮不能绕过（AI 流水线路径另有同类护栏）。
    """
    if _forced_preview():
        raise RuntimeError("当前是预演/验收模式（AURORA_NO_SAVE/AURORA_TEST），已拒绝真实保存")
    _require_license(cfg)
    c = make_client(cfg)
    product, save, req, issues = prepare_save(product_id, cfg, overrides, client=c)
    if issues and not allow_invalid:
        raise ValueError("保存前校验未通过：" + "；".join(issues))
    res = c.save_product(product, save, op=op)
    return {"result": res, "issues": issues, "autofilled": save.get("_autofilled") or [],
            "product_id": product.get("id")}

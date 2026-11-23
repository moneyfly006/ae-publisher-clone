"""Amazon 刊登保存收口（真实写入的唯一入口）。

安全边界（docs §7）
------------------
- ``AURORA_NO_SAVE`` / ``AURORA_TEST`` 存在 → 直接拒绝（RuntimeError）；
- ``core.guard.require(cfg)`` 授权校验（``AURORA_SKIP_LICENSE=1`` 放行，测试用）；
- 保存前校验 issues 非空且 ``allow_invalid=False`` → 拒绝（ValueError）；
- 真正写只有 ``AmazonClient.save_product``（ZIP: amazonSave.txt + multipart file/op）。

UI 与 ``amazon_pipeline.run_amazon_product(dry_run=False)`` 都必须走这里。
"""
from __future__ import annotations

import os

from ..integrations.amazon import AmazonClient, AmazonError

OP_SAVE = "1"           # 保存（采集箱内保存/编辑）
OP_PUBLISH = "2"        # 保存并发布（v1 不测）
OP_MOVE_PENDING = "3"   # 保存并移入待发布
OP_HANDLED = (OP_SAVE, OP_MOVE_PENDING, OP_PUBLISH)


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


def make_client(cfg) -> AmazonClient:
    """Cookie 顺序：cfg.amazon_cookies → cfg.manual_cookies → 本机浏览器。"""
    raw = _normalize_cookies(_cfg_value(cfg, "amazon_cookies", "") or "")
    if not raw:
        raw = _normalize_cookies(_cfg_value(cfg, "manual_cookies", None) or {})
    if not raw:
        raw = _browser_cookies()
    if not raw:
        raise RuntimeError(
            "没有 Amazon/店小秘 Cookie：请在「Amazon 刊登」粘贴 Cookie，"
            "或在「店铺与登录」读取/粘贴店小秘 Cookie")
    return AmazonClient(raw)


def _forced_preview() -> bool:
    """验收/测试模式（AURORA_NO_SAVE / AURORA_TEST）禁止真实写入。"""
    return bool(os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"))


def _require_license(cfg) -> None:
    """真实保存前的核心层授权校验（AURORA_SKIP_LICENSE=1 由 guard 内部放行）。"""
    from ..core.guard import require as _require
    _require(cfg)


def save_to_amazon(product_id, cfg, overrides=None, allow_invalid=False,
                   op=OP_SAVE, client=None, before_submit=None) -> dict:
    """执行 Amazon 保存；返回 ``{"result","issues","warnings","payload","product_id"}``。

    这是所有真实写入的唯一收口：预演/验收环境、授权不通过、校验 issues 都会在这里拦住。

    ``client`` 非空时**必须使用传入的客户端**（测试/脚本注入桩），不得再调 ``make_client``；
    这样 pipeline 把步骤①创建的 client 传下来后，不会再新建真实客户端。
    """
    if _forced_preview():
        raise RuntimeError("当前是预演/验收模式（AURORA_NO_SAVE/AURORA_TEST），已拒绝真实保存")
    _require_license(cfg)
    pid = str(product_id or "").strip()
    if not pid:
        raise ValueError("缺少 product_id")
    op = str(op or OP_SAVE)
    if op not in OP_HANDLED:
        raise ValueError(f"未知操作 op={op}（v1 仅支持 1/2/3）")

    c = client if client is not None else make_client(cfg)
    # 延迟 import：避免 amazon_pipeline ↔ amazon_publish 模块级循环依赖
    from .amazon_pipeline import prepare_save
    detail, payload, issues, warnings = prepare_save(pid, cfg, overrides, client=c, op=op)
    if issues and not allow_invalid:
        raise ValueError("保存前校验未通过：" + "；".join(issues))
    if before_submit:
        before_submit()
    try:
        result = c.save_product(payload, op=op)
    except AmazonError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AmazonError(f"保存失败：{str(exc)[:200]}") from exc
    return {
        "result": result,
        "issues": list(issues),
        "warnings": list(warnings),
        "payload": payload,
        "product_id": str(payload.get("id") or pid),
        "op": op,
    }


__all__ = [
    "OP_SAVE", "OP_PUBLISH", "OP_MOVE_PENDING", "OP_HANDLED",
    "make_client", "save_to_amazon", "AmazonClient", "AmazonError",
]

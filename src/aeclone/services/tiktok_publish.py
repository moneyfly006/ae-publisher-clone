"""TikTok Shop 刊登保存收口（真实写入的**唯一**入口）。

安全边界（docs/TIKTOK_DESIGN.md §1.7 / §7）
------------------------------------------
- ``AURORA_NO_SAVE`` / ``AURORA_TEST`` 存在 → 直接拒绝（RuntimeError），
  且在**创建客户端之前**就拒绝（不会因为读 Cookie/建 Session 产生副作用）；
- ``confirm_write=True`` 是硬门槛：默认 ``False``，脚本误调也不会写；
- ``core.guard.require(cfg)`` 核心层授权校验（``AURORA_SKIP_LICENSE=1`` 放行，测试用）；
- 保存前校验 issues 非空且 ``allow_invalid=False`` → 拒绝（ValueError）；
- 真正写只有 ``TikTokClient.save_product``（ZIP: tiktokSave.txt + multipart
  file/op[/saveMode]）与批量接口 ``publish_products``；
- 写接口**零自动重试**（客户端 ``/add.json`` 也在写路径白名单里强制不重试）；
- 注入 ``client``（测试/脚本）时**必须复用**，绝不内部新建真实客户端
  （Amazon 曾因违反此条发生未授权写入，见 docs/AMAZON_VERIFICATION.md §3.3）。

UI 与 ``tiktok_pipeline.run_tiktok_product(dry_run=False, confirm_write=True)``
都必须走这里。
"""
from __future__ import annotations

import copy
import os

from ..integrations.tiktok import (
    OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, OP_SCHEDULE, SAVE_MODE_AS_DRAFT,
    TikTokClient, TikTokError, TikTokNotReady,
)

# 可真实写的 op（v1）：1=保存、3=保存并移入待发布、2=发布（可选 AS_DRAFT）。
# 5=定时发布需要 dxmScheduleTimeStr，v1 不支持 → 显式拒绝而不是悄悄降级。
OP_HANDLED = (OP_SAVE, OP_MOVE_PENDING, OP_PUBLISH)
PUBLISH_OPS = (OP_PUBLISH,)
SAVE_MODES = ("", SAVE_MODE_AS_DRAFT)


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
    """本机浏览器兜底（已登录的店小秘 Chrome/Edge/Brave）。"""
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


def make_client(cfg) -> TikTokClient:
    """Cookie 顺序：``cfg.tiktok_cookies`` → ``cfg.manual_cookies`` → 本机浏览器。

    Cookie 只进内存；错误消息里不打印 Cookie 内容。
    """
    raw = _normalize_cookies(_cfg_value(cfg, "tiktok_cookies", "") or "")
    if not raw:
        raw = _normalize_cookies(_cfg_value(cfg, "manual_cookies", None) or {})
    if not raw:
        raw = _browser_cookies()
    if not raw:
        raise RuntimeError(
            "没有 TikTok/店小秘 Cookie：请在「TikTok 刊登」粘贴 Cookie，"
            "或在「店铺与登录」读取/粘贴店小秘 Cookie")
    return TikTokClient(raw)


def _forced_preview() -> bool:
    """验收/测试模式（AURORA_NO_SAVE / AURORA_TEST）禁止真实写入。"""
    return bool(os.environ.get("AURORA_NO_SAVE") or os.environ.get("AURORA_TEST"))


def _require_license(cfg) -> None:
    """真实保存前的核心层授权校验（AURORA_SKIP_LICENSE=1 由 guard 内部放行）。"""
    from ..core.guard import require as _require
    _require(cfg)


def _normalize_op(op) -> str:
    value = str(op or OP_SAVE).strip() or OP_SAVE
    if value == OP_SCHEDULE:
        raise ValueError("定时发布（op=5）v1 未支持：需要 dxmScheduleTimeStr 预约时间")
    if value not in OP_HANDLED:
        raise ValueError(f"未知操作 op={value}（v1 仅支持 1/2/3）")
    return value


def _normalize_save_mode(save_mode, op: str) -> str:
    value = str(save_mode or "").strip()
    if value not in SAVE_MODES:
        raise ValueError(f"未知 saveMode={value}（仅支持空或 AS_DRAFT）")
    if value == SAVE_MODE_AS_DRAFT and op != OP_PUBLISH:
        raise ValueError("saveMode=AS_DRAFT 仅用于发布动作（op=2）")
    return value


def _finalize_payload(payload: dict, op: str) -> dict:
    """defensive 收尾：深拷贝后删 ``variationBos``，``op`` 写 int（服务端要数字）。

    深拷贝保证不修改调用方/预演用的 payload；``variationBos`` 是前端实测必须
    删除的字段（docs §1.5 末尾）。
    """
    out = copy.deepcopy(dict(payload or {}))
    out.pop("variationBos", None)
    try:
        out["op"] = int(str(op).strip())
    except Exception:
        out["op"] = 1
    return out


def _ids_list(ids) -> list[str]:
    """ids 兼容 list/tuple/set/逗号字符串（批量写接口用）。"""
    if isinstance(ids, (list, tuple, set)):
        return [str(item).strip() for item in ids if str(item).strip()]
    raw = str(ids or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def save_to_tiktok(product_id, cfg, overrides=None, allow_invalid=False,
                   op=OP_SAVE, save_mode="", client: TikTokClient | None = None,
                   confirm_write: bool = False, before_submit=None) -> dict:
    """执行 TikTok 保存；返回 ``{"result","issues","warnings","payload",...}``。

    这是所有真实写入的唯一收口：预演/验收环境、缺少 ``confirm_write``、
    授权不通过、校验 issues 都会在这里拦住，且都发生在**创建客户端之前**
    （注入 client 时则发生在任何写调用之前）。

    ``client`` 非空时**必须使用传入的客户端**（测试/脚本注入桩），不得再调
    ``make_client``；pipeline 把步骤①创建的 client 传下来后，不会再新建真实客户端。
    """
    # 1) 安全护栏（全部在创建客户端之前）
    if _forced_preview():
        raise RuntimeError(
            "当前是预演/验收模式（AURORA_NO_SAVE/AURORA_TEST），已拒绝真实保存")
    if not confirm_write:
        raise RuntimeError(
            "真实保存需要显式 confirm_write=True（防脚本误写）；预演请用 dry_run=True")
    op = _normalize_op(op)
    save_mode = _normalize_save_mode(save_mode, op)
    _require_license(cfg)

    pid = str(product_id or "").strip()
    if not pid:
        raise ValueError("缺少 product_id")

    # 2) 注入 client 必须复用；只有未注入时才新建（真实客户端）
    c = client if client is not None else make_client(cfg)

    # 延迟 import：避免 tiktok_pipeline ↔ tiktok_publish 模块级循环依赖
    from .tiktok_pipeline import prepare_save
    detail, payload, issues, warnings = prepare_save(pid, cfg, overrides, client=c, op=op)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("保存包为空：拒绝调用保存接口")
    if issues and not allow_invalid:
        raise ValueError("保存前校验未通过：" + "；".join(issues))
    from .image_preflight import preflight_field
    _img_errors = preflight_field(payload, "mainImage", "|", min_count=1, max_count=9, client=c)
    if _img_errors:
        raise ValueError("；".join(_img_errors))

    # 3) defensive 收尾（删 variationBos / op 写 int），再调唯一写接口
    final_payload = _finalize_payload(payload, op)
    if before_submit is not None:
        before_submit()
    try:
        result = c.save_product(final_payload, op=op, save_mode=save_mode)
    except TikTokError:
        raise  # TikTokNotReady 也是 TikTokError 子类，原样带中文可操作提示
    except Exception as exc:  # noqa: BLE001
        raise TikTokError(f"保存失败：{str(exc)[:200]}") from exc

    return {
        "result": result,
        "issues": list(issues),
        "warnings": list(warnings),
        "payload": final_payload,
        "product_id": str(final_payload.get("id") or pid),
        "op": op,
        "save_mode": save_mode,
    }


def publish_tiktok(ids, cfg, shop_ids="", client: TikTokClient | None = None,
                   confirm_write: bool = False) -> dict:
    """批量发布（``batchPublish.json``）；真实写收口之一，写接口不自动重试。

    同样要求 ``confirm_write=True``，且在创建客户端之前拦截验收/测试环境。
    """
    if _forced_preview():
        raise RuntimeError(
            "当前是预演/验收模式（AURORA_NO_SAVE/AURORA_TEST），已拒绝真实发布")
    if not confirm_write:
        raise RuntimeError(
            "真实发布需要显式 confirm_write=True（防脚本误写）")
    _require_license(cfg)
    id_list = _ids_list(ids)
    if not id_list:
        raise ValueError("缺少商品 id（ids 为空）")

    c = client if client is not None else make_client(cfg)
    try:
        result = c.publish_products(id_list, shop_ids=shop_ids)
    except TikTokError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise TikTokError(f"发布失败：{str(exc)[:200]}") from exc
    return {"result": result, "ids": id_list, "shop_ids": str(shop_ids or "")}


__all__ = [
    "OP_SAVE", "OP_PUBLISH", "OP_MOVE_PENDING", "OP_SCHEDULE",
    "SAVE_MODE_AS_DRAFT", "OP_HANDLED", "PUBLISH_OPS", "SAVE_MODES",
    "make_client", "save_to_tiktok", "publish_tiktok",
    "TikTokClient", "TikTokError", "TikTokNotReady",
]

"""浏览器会话管理：按实例使用独立端口与 profile，支持后台运行。"""
from __future__ import annotations

from .cdp import launch_browser, hide_window


def ensure_browser(cfg, force_show: bool = False) -> dict:
    """确保有可用的 CDP 浏览器；返回 {ws, port}。

    - 每个实例用独立端口（cdp_port）与独立 profile 目录，避免多开冲突
    - 默认连接后最小化窗口（后台运行），只有传 force_show=True 才保持可见
    """
    port = int(getattr(cfg, "cdp_port", 9333) or 9333)
    inst = getattr(cfg, "instance_name", "") or ""
    profile = None
    if inst:
        from ..config import config_dir
        profile = str(config_dir().parent / "AuroraListInstances" / inst / "chrome-profile")
    info = launch_browser("chrome", port=port, user_data_dir=profile,
                          url="https://www.dianxiaomi.com/web/smt/smtProductList/offline")
    if getattr(cfg, "browser_hide", True) and not force_show:
        try:
            hide_window(info.get("ws", ""))
        except Exception:
            pass
    return info

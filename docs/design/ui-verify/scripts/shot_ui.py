"""临时：起单个页面预览服务（只读沙箱），供 playwright 截图。

env:
  SHOT_PAGE     页面号（默认 0）
  SHOT_TAB      页内 Tab 序号（默认 0，仅对 workspace_tabs 页面有效）
  SHOT_THEME    dark / light（默认 dark）
  SHOT_PLATFORM alibaba / aliexpress（默认 alibaba）
  SHOT_PORT     端口（默认 8891）
  AURORA_LOCK   =1 时模拟未授权平台（只留 alibaba listing）用于锁定页截图
"""
import os
import sys
from pathlib import Path

ROOT = Path("/Users/apple/Projects/alibaba-lister")
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import ROOT as ASSET_ROOT, build_app, sandbox  # noqa: E402


def target(page):
    theme = os.environ.get("SHOT_THEME", "dark")
    tab = os.environ.get("SHOT_TAB", "0")
    platform = os.environ.get("SHOT_PLATFORM", "alibaba")
    os.environ["AURORA_THEME"] = theme
    os.environ["AURORA_TAB"] = tab
    app = build_app(page)
    if platform != "alibaba":
        app._switch_platform(platform)
    if os.environ.get("AURORA_LOCK") == "1":
        from aeclone.licensing import load_entitlements
        app.cfg.set("license_platforms", "alibaba:listing")
        app.entitlements = load_entitlements(app.cfg)
        app._rebuild_nav()
    try:
        app._switch(int(os.environ.get("SHOT_PAGE", "0")))
    except Exception as ex:  # noqa: BLE001
        print("switch failed:", ex)
    try:
        page.update()
    except Exception:
        pass
    return app


if __name__ == "__main__":
    port = int(os.environ.get("SHOT_PORT", "8891"))
    with sandbox(threads=True):
        ft.app(target=target, view=ft.AppView.WEB_BROWSER, host="127.0.0.1", port=port,
               assets_dir=str(ASSET_ROOT / "assets"),
               web_renderer=ft.WebRenderer.CANVAS_KIT)

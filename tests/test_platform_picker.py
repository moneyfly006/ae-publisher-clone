"""各平台商品选择器：分页、全选、保存选中 ID。"""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILED.append(name); print(f"  ✗ {name} {detail}")


def main():
    print("=" * 60)
    with sandbox(threads=True):
        app = build_app()
        app._switch_platform("amazon")
        app._switch(21)
        btn = next(c for c in walk(app.content.controls) if getattr(c, "text", "") == "选择商品")
        rows = [{"id": f"P{i}", "title": f"Product {i}"} for i in range(1, 121)]
        with patch("aeclone.services.collect_automation.client_for", return_value=object()), \
             patch("aeclone.services.collect_automation.snapshot", return_value=rows):
            btn.on_click(None)
            time.sleep(1)
            dlg = app._dlg
            root = dlg.content
            texts = [str(getattr(c, "value", "") or "") for c in walk([root]) if isinstance(c, ft.Text)]
            check("有分页信息", any("第 1/3 页" in t for t in texts), texts[-3:])
            check("有全选筛选结果按钮", any(getattr(c, "text", "") == "全选筛选结果"
                                             for c in walk([root]) if isinstance(c, ft.TextButton)))
            next(c for c in walk([root]) if isinstance(c, ft.TextButton)
                 and getattr(c, "text", "") == "全选筛选结果").on_click(None)
            dlg.actions[1].on_click(None)
            import json
            got = json.loads(app.cfg.amazon_collect_selected_ids)
            check("保存选中 ID", len(got) == 120, len(got))
    with sandbox(threads=True):
        app = build_app()
        rows = [{"id": "A1", "title": "Alpha"}, {"id": "A2", "title": "Beta"}]
        from aeclone.ui.collect_workflow import RUN_PAGE
        for platform in ("alibaba", "amazon", "tiktok", "shopee"):
            app._switch_platform(platform)
            app._switch(RUN_PAGE[platform])
            btn = next((c for c in walk(app.content.controls)
                        if getattr(c, "text", "") == "选择商品"), None)
            check(f"{platform} 运行页有选择商品", btn is not None)
            if btn is None:
                continue
            with patch("aeclone.services.collect_automation.client_for", return_value=object()), \
                 patch("aeclone.services.collect_automation.snapshot", return_value=rows):
                btn.on_click(None)
                time.sleep(0.5)
                check(f"{platform} 选择器打开", bool(app._dlg))
                if app._dlg:
                    app._close_dlg(app._dlg)

    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())

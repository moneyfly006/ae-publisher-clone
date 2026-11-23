"""顶栏「实时日志」：点击展开实时日志面板，而不是跳转页面。"""
import sys
import time
from pathlib import Path

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
    with sandbox():
        app = build_app()
        btns = [b for b in walk(app.page.controls)
                if isinstance(b, (ft.ElevatedButton, ft.OutlinedButton, ft.TextButton))
                and str(getattr(b, "text", "")) == "实时日志"]
        check("顶栏有实时日志按钮", bool(btns), [getattr(b, "text", "") for b in walk(app.page.controls)])
        if not btns:
            print("FAILED"); return 1
        btns[0].on_click(None)
        time.sleep(0.3)
        check("点击后打开日志面板（不跳页）", bool(getattr(app, "_dlg", None)), app._page_idx)
        _title_texts = [str(getattr(c, "value", "") or "")
                        for c in walk([getattr(app, "_dlg", None).title])
                        if isinstance(c, ft.Text)]
        check("面板标题是运行日志", any("运行日志" in t for t in _title_texts), _title_texts)
        app._log("实时日志测试 123", "INFO")
        app._render_log_panel()
        rows = [str(getattr(c, "value", "")) for c in app.log_panel_list.controls
                if isinstance(c, ft.Text)]
        check("面板显示实时日志", any("实时日志测试 123" in x for x in rows), rows[-2:])
        app._close_log_panel()
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())

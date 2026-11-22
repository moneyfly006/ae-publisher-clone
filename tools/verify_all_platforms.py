"""全平台基础验证：页面/导航/Tab/平台切换/侧栏按钮守卫。

不调用真实店铺写接口；只做 UI 结构与回调路由验证（AI/图片/视频另见证据文件）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402

from aeclone.platforms import PLATFORMS  # noqa: E402


def _buttons(controls):
    return [c for c in controls if isinstance(c, (ft.ElevatedButton, ft.OutlinedButton,
                                                  ft.TextButton))]


def verify_platform(pid: str) -> dict:
    pf = PLATFORMS[pid]
    out = {"platform": pid, "pages": [], "problems": []}
    with sandbox():
        app = build_app()
        app._switch_platform(pid)
        # 1) 页面构建 + Tab 全部切换
        for item in pf.pages:
            try:
                app._switch(item.index)
                controls = list(walk(app.content.controls))
                tabs = [c for c in controls if isinstance(c, ft.Tabs)]
                tab_names = [t.text for c in tabs for t in c.tabs]
                for tab in tabs:
                    for i in range(len(tab.tabs)):
                        tab.selected_index = i
                app._safe_update()
                out["pages"].append({"index": item.index, "label": item.label,
                                     "controls": len(controls), "tabs": tab_names})
            except Exception as ex:  # noqa: BLE001
                out["problems"].append(f"page {item.index} {item.label}: {type(ex).__name__}: {ex}")
        # 2) 左侧导航按钮逐个点击
        for idx, btn in app.nav_buttons:
            try:
                btn.on_click(NS(control=btn, data=""))
                if app._page_idx != idx:
                    out["problems"].append(f"nav {idx} 点击后页面={app._page_idx}")
            except Exception as ex:  # noqa: BLE001
                out["problems"].append(f"nav {idx}: {type(ex).__name__}: {ex}")
        # 3) 平台切换（逐个平台切换后回到本平台）
        for other in PLATFORMS:
            if other == pid:
                continue
            try:
                app._switch_platform(other)
                app._switch_platform(pid)
            except Exception as ex:  # noqa: BLE001
                out["problems"].append(f"switch {other}: {type(ex).__name__}: {ex}")
        # 4) 侧栏运行按钮守卫（非速卖通平台不应启动 SMT 管线）
        app._running = False
        try:
            app._switch(pf.pages[0].index)
            app.btn_run.on_click(NS(control=app.btn_run, data=""))
            if pid != "aliexpress" and app._running:
                out["problems"].append("非速卖通平台点了「启动任务」却启动 SMT 管线")
        except Exception as ex:  # noqa: BLE001
            out["problems"].append(f"run button: {type(ex).__name__}: {ex}")
        # 5) 主题切换
        try:
            app._toggle_theme()
        except Exception as ex:  # noqa: BLE001
            out["problems"].append(f"theme: {type(ex).__name__}: {ex}")
    out["ok"] = not out["problems"]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=Path("docs/platform-verification.json"))
    args = parser.parse_args()
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "platforms": []}
    for pid, pf in PLATFORMS.items():
        if not pf.implemented:
            continue
        report["platforms"].append(verify_platform(pid))
    for p in report["platforms"]:
        print(f"{p['platform']}: pages={len(p['pages'])} problems={len(p['problems'])}")
        for x in p["problems"][:6]:
            print("  -", x)
    report["ok"] = all(p["ok"] for p in report["platforms"])
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print("RESULT:", "PASS" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

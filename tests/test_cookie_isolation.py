"""每个平台 Cookie 独立存放，粘贴一个平台不覆盖其他平台。"""
import sys
from pathlib import Path
from types import SimpleNamespace

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
        app.cfg.set("manual_cookies", {})
        app._switch_platform("aliexpress")
        app._paste_cookie()
        dlg = app._dlg
        field = next(c for c in walk([dlg]) if isinstance(c, ft.TextField))
        field.value = "dxm_i=AE-ONLY; dxm_t=AE-T"
        dlg.actions[1].on_click(None)
        check("速卖通写入 aliexpress_cookies",
              "AE-ONLY" in str(app.cfg.aliexpress_cookies), app.cfg.aliexpress_cookies)
        check("速卖通不写共享 Cookie", not app.cfg.manual_cookies, app.cfg.manual_cookies)
        app._switch_platform("alibaba")
        app._paste_cookie()
        dlg2 = app._dlg
        field2 = next(c for c in walk([dlg2]) if isinstance(c, ft.TextField))
        field2.value = "dxm_i=ALI-ONLY; dxm_t=ALI-T"
        dlg2.actions[1].on_click(None)
        check("阿里写入 alibaba_cookies", "ALI-ONLY" in str(app.cfg.alibaba_cookies))
        check("阿里不覆盖速卖通 Cookie", "AE-ONLY" in str(app.cfg.aliexpress_cookies),
              app.cfg.aliexpress_cookies)
        check("阿里不写共享 Cookie", not app.cfg.manual_cookies)
        app._switch_platform("amazon")
        app._paste_cookie()
        dlg3 = app._dlg
        f3 = next(c for c in walk([dlg3]) if isinstance(c, ft.TextField))
        f3.value = "dxm_i=AMZ-ONLY"
        dlg3.actions[1].on_click(None)
        check("Amazon 独立", "AMZ-ONLY" in str(app.cfg.amazon_cookies))
        check("三个平台互不覆盖",
              "AE-ONLY" in str(app.cfg.aliexpress_cookies)
              and "ALI-ONLY" in str(app.cfg.alibaba_cookies)
              and "AMZ-ONLY" in str(app.cfg.amazon_cookies))
    print("=" * 60)
    if FAILED:
        print(f"FAILED: {len(FAILED)} 项"); return 1
    print("PASS: 全部通过"); return 0


if __name__ == "__main__":
    sys.exit(main())

"""task-5：应用壳层控件验证（导航/平台栏/顶栏/侧栏/快捷键）。只读沙箱。"""
import json
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path("/Users/apple/Projects/alibaba-lister")
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, label, sandbox, walk  # noqa: E402
from aeclone.platforms import ALIBABA  # noqa: E402

RESULT = []


def rec(name, ok, detail=""):
    RESULT.append(dict(check=name, ok=bool(ok), detail=str(detail)[:500]))
    print(("PASS " if ok else "FAIL ") + name + (" :: " + str(detail)[:180] if detail else ""))


def ev(app, ctl=None, data="", **kw):
    return SimpleNamespace(control=ctl, data=data, page=app.page, **kw)


def toast(app):
    sb = getattr(app.page, "snack_bar", None)
    if sb is not None and getattr(sb, "open", False):
        return str(getattr(getattr(sb, "content", None), "value", "") or "")
    return ""


def s_nav():
    with sandbox(threads=True):
        app = build_app()
        rec("导航按钮数=13（Alibaba）", len(app.nav_buttons) == 13, len(app.nav_buttons))
        ok = True
        details = []
        for idx, btn in sorted(app.nav_buttons):
            item = ALIBABA.page(idx)
            btn.on_click(ev(app, btn))
            hit = app._page_idx
            good = hit == item.index
            ok &= good
            good_bc = item.label in (app.breadcrumb.value or "")
            ok &= good_bc
            details.append(f"{item.index}:{item.label}->{hit}/{('bc-ok' if good_bc else 'bc-✗')}")
        rec("逐个点击 13 个导航项→切到对应页且面包屑正确", ok, "; ".join(details))


def s_nav_search():
    with sandbox(threads=True):
        app = build_app()
        app._switch(0)
        app.nav_search.value = "库存"
        app._filter_navigation("库存")
        vis = [i for i, b in app.nav_buttons if getattr(b, "visible", True)]
        rec("导航搜索「库存」只剩相关页", vis == [7], vis)
        app._open_navigation_match()
        rec("搜索后回车→打开首个匹配页(7)", app._page_idx == 7, app._page_idx)
        rec("搜索框回车后清空", (app.nav_search.value or "") == "", app.nav_search.value)
        app.nav_search.value = "不存在的功能xyz"
        app._filter_navigation("不存在的功能xyz")
        rec("无匹配→显示空状态提示", getattr(app.nav_empty, "visible", False) is True,
            getattr(app.nav_empty, "value", None) or label(app.nav_empty))
        rec("无匹配→所有导航项隐藏", not any(getattr(b, "visible", True) for _, b in app.nav_buttons))


def s_topbar():
    with sandbox(threads=True):
        app = build_app()
        app._switch(0)
        # 选择店铺 → 页1
        app.store_shortcut.on_click(ev(app, app.store_shortcut))
        rec("顶栏「选择店铺」→店铺与模板(1)", app._page_idx == 1, app._page_idx)
        # 顶栏日志按钮（构建在 shell 内，label 用 tooltip 定位）
        log_btns = [c for c in walk(app.page.controls)
                    if isinstance(c, ft.IconButton) and c.tooltip == "查看任务与日志"]
        rec("顶栏「查看任务与日志」按钮存在", len(log_btns) == 1, len(log_btns))
        if log_btns:
            log_btns[0].on_click(ev(app, log_btns[0]))
            rec("顶栏日志按钮→任务与日志(10)", app._page_idx == 10, app._page_idx)
        # 主题切换
        app._switch(4)
        before_mode = str(app.page.theme_mode)
        before_theme = str(getattr(app.cfg, "ui_theme", ""))
        app.theme_btn.on_click(ev(app, app.theme_btn))
        after_mode = str(app.page.theme_mode)
        after_theme = str(getattr(app.cfg, "ui_theme", ""))
        rec("顶栏主题按钮切换主题并落配置",
            before_theme != after_theme and before_mode != after_mode,
            f"{before_theme}/{before_mode} -> {after_theme}/{after_mode}")
        rec("主题切换 toast 提示", "已切换到" in toast(app), toast(app))
        rec("主题切换后页面仍可渲染并可切页", app._page_idx == 4, app._page_idx)
        app._switch(0)
        rec("主题切换后导航仍可用", len(app.nav_buttons) == 13, len(app.nav_buttons))


def s_sidebar():
    with sandbox(threads=True):
        app = build_app()
        app._switch(0)
        # 日志监控：打开/关闭
        app._open_log_panel()
        dlg = getattr(app, "log_panel_dlg", None)
        rec("侧栏「日志监控」打开实时进度弹窗", dlg is not None and getattr(dlg, "open", False),
            label(dlg) if dlg else None)
        app._close_log_panel()
        rec("关闭日志弹窗后状态复位", getattr(app, "log_panel_open", False) is False,
            getattr(app, "log_panel_open", None))
        app._open_log_panel()
        rec("可再次打开日志弹窗", getattr(app, "log_panel_open", False) is True)
        close_btn = next((c for c in walk([app.log_panel_dlg])
                          if isinstance(c, ft.TextButton) and c.text == "关闭"), None)
        rec("日志弹窗有「关闭」按钮", close_btn is not None)
        if close_btn:
            close_btn.on_click(ev(app, close_btn))
            rec("点「关闭」后弹窗关闭", not getattr(app.log_panel_dlg, "open", False))
        # 侧栏按钮文字/tooltip 与 Alibaba 行为是否一致
        rec("侧栏按钮文案=「启动任务」、tooltip 仍写启动/停止",
            app.btn_run_text.value == "启动任务" and "启动 / 停止" in (app.btn_run.tooltip or ""),
            f"text={app.btn_run_text.value} tip={app.btn_run.tooltip}")
        app._switch(0)
        app.btn_run.on_click(ev(app, app.btn_run))
        rec("Alibaba 下点侧栏按钮→跳 12 号页并有 toast",
            app._page_idx == 12 and "Alibaba 商品请在" in toast(app),
            f"page={app._page_idx} toast={toast(app)}")


def s_keys():
    with sandbox(threads=True):
        app = build_app()
        app._switch(0)

        def key(k, ctrl=False, meta=False):
            app._on_key(SimpleNamespace(key=k, ctrl=ctrl, meta=meta))

        key("l", meta=True)
        rec("⌘L → 任务与日志(10)", app._page_idx == 10, app._page_idx)
        key("f", meta=True)
        dlg = getattr(app, "_dlg", None)
        rec("⌘F → 打开全局搜索弹窗", dlg is not None and getattr(dlg, "open", False),
            label(dlg) if dlg else None)
        try:
            app.page.close_dialog()
        except Exception:
            pass
        key("escape", meta=True)
        rec("⌘⇧Escape 不崩溃（esc 分支）", True)
        # ⌘R 在 Alibaba 的守卫（已在平台脚本验证，这里只确认 key 绑定存在）
        app._switch(0)
        key("r", meta=True)
        rec("⌘R 在 Alibaba → 12 号页（守卫生效）", app._page_idx == 12, app._page_idx)
        rec("⌘R 在 Alibaba → toast 提示", "Alibaba 商品请在" in toast(app), toast(app))
        # 非 ctrl 按键不应触发
        app._switch(0)
        key("r", ctrl=False, meta=False)
        rec("无修饰键 R 不触发启动", app._page_idx == 0, app._page_idx)
        # 切到速卖通后 ⌘R 不跳 12
        app._switch_platform("aliexpress")
        app._switch(0)
        key("r", meta=True)
        rec("速卖通平台 ⌘R 不被 Alibaba 守卫拦截（走 10 号页/授权拦截）",
            app._page_idx == 10, app._page_idx)


def s_platform_rail():
    with sandbox(threads=True):
        app = build_app()
        badges = [c for c in walk([app.platform_rail])
                  if isinstance(c, ft.Container) and getattr(c, "on_click", None)
                  and getattr(c, "tooltip", "")]
        rec("平台栏 5 个可点击徽标", len(badges) == 5, [b.tooltip for b in badges])
        from aeclone.platforms import PLATFORMS
        order = list(PLATFORMS.keys())
        ok = True
        details = []
        for b, pid in zip(badges, order):
            label_txt = b.tooltip.split(" · ")[0]
            b.on_click(ev(app, b))
            good = app.active_platform.id == pid
            ok &= good
            details.append(f"{label_txt}->{app.active_platform.id}" + ("" if good else " ✗"))
        rec("逐个点击平台徽标→active_platform 正确切换", ok, "; ".join(details))
        if app.active_platform.id != "alibaba":
            app._switch_platform("alibaba")
        rec("切回 Alibaba 后导航恢复 13 项", len(app.nav_buttons) == 13, len(app.nav_buttons))


def main():
    for fn in (s_nav, s_nav_search, s_topbar, s_sidebar, s_keys, s_platform_rail):
        try:
            fn()
        except Exception:
            rec(f"场景 {fn.__name__} 异常", False, traceback.format_exc()[-1200:])
    fails = [r for r in RESULT if not r["ok"]]
    Path("/tmp/ui-shell.json").write_text(
        json.dumps(dict(total=len(RESULT), fails=len(fails), results=RESULT),
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(RESULT)} checks, {len(fails)} FAIL")
    for f in fails:
        print("  FAIL:", f["check"], "::", f["detail"][:400])
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())

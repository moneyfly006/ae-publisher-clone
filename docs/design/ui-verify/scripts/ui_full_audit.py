"""task-5 全页面全控件审计 harness（临时脚本，只读沙箱）。

用法:
  .venv/bin/python /tmp/ui_full_audit.py --out /tmp/ui-audit-full.json [--pages 0,1,...]

对 13 个页面在 dark/light 两主题下遍历控件树；对每个可安全触发的
on_click/on_change/on_submit 在独立 sandbox 里调用并记录：
异常 / 弹窗 / toast / 页面跳转 / 后台线程 / 文件选择器 / 外链 / 配置写入 / 无反馈。
"""
import argparse
import inspect
import json
import os
import signal
import sys
import threading
import time
import traceback
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import flet as ft

TOOLS = Path("/Users/apple/Projects/alibaba-lister/tools")
sys.path.insert(0, str(TOOLS))
from ui_sandbox import build_app, label, sandbox, walk  # noqa: E402

BUTTON_TYPES = {
    "ElevatedButton", "TextButton", "OutlinedButton", "IconButton", "FilledButton",
    "FilledTonalButton", "CupertinoButton", "CupertinoFilledButton",
    "FloatingActionButton", "PopupMenuButton", "MenuItemButton", "SubmenuButton",
}
TOGGLE_TYPES = {"Switch", "Checkbox", "Radio"}
DROPDOWN_TYPES = {"Dropdown", "DropdownMenu"}
INPUT_TYPES = {"TextField", "SearchBar"}
EVENTS = ("on_click", "on_change", "on_submit")

PAGE_ORDER = [0, 1, 2, 6, 7, 8, 12, 3, 5, 4, 10, 9, 11]
APP = str(TOOLS.parent / "src" / "aeclone" / "ui" / "app.py")


def rel_file(path):
    try:
        return str(Path(path).resolve().relative_to(TOOLS.parent))
    except Exception:
        return str(path)


def cb_site(cb):
    """回调定义位置 file:line（lambda/闭包/绑定方法都适用）。"""
    fn = cb
    depth = 0
    while depth < 4:
        depth += 1
        if hasattr(fn, "func"):        # functools.partial
            fn = fn.func
            continue
        if hasattr(fn, "function") and not inspect.isfunction(fn):  # bound method __func__
            fn = fn.__func__
            continue
        break
    code = getattr(fn, "__code__", None)
    if code is not None:
        return f"{rel_file(code.co_filename)}:{code.co_firstlineno}"
    try:
        return f"{rel_file(inspect.getsourcefile(fn))}:{inspect.getsourcelines(fn)[1]}"
    except Exception:
        return ""


def classify(control):
    name = type(control).__name__
    if name in BUTTON_TYPES:
        return "button"
    if name in TOGGLE_TYPES:
        return "toggle"
    if name in DROPDOWN_TYPES:
        return "dropdown"
    if name in INPUT_TYPES:
        return "input"
    if name == "Tabs":
        return "tabs"
    return "other"


def ui_snapshot(controls):
    """页面可观察状态快照：文本/输入值/容器子项数/表格行数。"""
    snap = []
    for c in walk(controls):
        n = type(c).__name__
        if n in ("Text", "TextField", "Dropdown", "Switch", "Checkbox"):
            snap.append((n, str(getattr(c, "value", ""))[:80]))
        elif n in ("Column", "Row", "ListView", "ResponsiveRow"):
            snap.append((n + "-len", len(getattr(c, "controls", []) or [])))
        elif n == "DataTable":
            snap.append(("DataTable-rows", len(getattr(c, "rows", []) or [])))
    return snap


def control_specs(controls):
    out = []
    for i, control in enumerate(walk(controls)):
        events = [ev for ev in EVENTS if callable(getattr(control, ev, None))]
        out.append(dict(position=i, kind=classify(control), type=type(control).__name__,
                        label=label(control)[:160], disabled=bool(getattr(control, "disabled", False)),
                        visible=bool(getattr(control, "visible", True)),
                        events=events,
                        cb_site=cb_site(getattr(control, events[0])) if events else ""))
    return out


def structure_for(page, theme, populated=False):
    with sandbox() as cfg:
        os.environ["AURORA_THEME"] = theme
        os.environ["AURORA_TAB"] = "0"
        app = build_app()
        if populated:
            populate(app, page)
        app._switch(page)
        specs = control_specs(app.content.controls)
        shell = control_specs(app.page.controls)
        theme_mode = str(app.page.theme_mode)
    counts = dict(buttons=0, toggles=0, dropdowns=0, inputs=0, tabs=0,
                  callbacks=0, empty_buttons=0, empty_toggles=0, empty_dropdowns=0,
                  empty_inputs=0, disabled=0)
    for s in specs:
        key = s["kind"] + "s" if s["kind"] != "tabs" else "tabs"
        if key in counts:
            counts[key] += 1
        counts["callbacks"] += len(s["events"])
        if s["disabled"]:
            counts["disabled"] += 1
        if s["kind"] == "input" and not s["events"]:
            counts["empty_inputs"] += 1
        if s["kind"] in ("button", "toggle", "dropdown") and not s["events"]:
            k = "empty_" + ("buttons" if s["kind"] == "button" else
                            "toggles" if s["kind"] == "toggle" else "dropdowns")
            counts[k] += 1
    return dict(page=page, theme=theme, theme_mode=theme_mode,
                counts=counts, controls=specs, shell_controls=shell,
                shell_counts=dict(callbacks=sum(len(s["events"]) for s in shell),
                                  buttons=sum(s["kind"] == "button" for s in shell)))


_noop_setitimer = getattr(signal, "setitimer", None)


class CallbackTimeout(Exception):
    pass


def invoke(page, position, event, populated, app_dir=None):
    """在全新 sandbox 里调用一个回调；返回结果 dict。"""
    deferred, native, external = [], [], []
    before_cfg = None

    def on_alarm(signum, frame):
        raise CallbackTimeout("回调超过 6s 未返回")

    old_handler = None
    if _noop_setitimer is not None:
        old_handler = signal.signal(signal.SIGALRM, on_alarm)
    result = dict(page=page, position=position, event=event,
                  scenario="populated" if populated else "default")
    try:
        with sandbox(external_calls=external), ExitStack() as stack:
            stack.enter_context(patch.object(threading.Thread, "start", lambda thread:
                                             deferred.append(getattr(thread._target, "__name__",
                                                                     "background"))))
            for name in ("pick_files", "save_file", "get_directory_path"):
                stack.enter_context(patch.object(ft.FilePicker, name,
                                                 lambda *a, _name=name, **kw: native.append(_name)))
            stack.enter_context(patch.object(os, "_exit", lambda code=0: (_ for _ in ()).throw(
                RuntimeError("os._exit 被沙箱拦截"))))
            app = build_app()
            if populated:
                populate(app, page)
            app._switch(page)
            deferred.clear()
            external.clear()
            app.page.launched_urls.clear()
            before_cfg = dict(app.cfg.as_dict())
            before_logs = len(getattr(app, "logs", []))
            before_page = getattr(app, "_page_idx", None)
            before_ui = ui_snapshot(app.content.controls)
            control = list(walk(app.content.controls))[position]
            callback = getattr(control, event)
            if event == "on_change" and isinstance(control, (ft.Switch, ft.Checkbox)):
                control.value = not bool(control.value)
            elif event == "on_change" and isinstance(control, ft.Dropdown):
                _vals = [o.key for o in (control.options or [])]
                _new = [v for v in _vals if v != str(control.value or "")]
                if _new:
                    control.value = _new[0]
            elif event == "on_change" and isinstance(control, ft.TextField):
                # 用不同值触发真实写入路径（同值等于没改，测不出语义）
                _old = str(getattr(control, "value", "") or "")
                control.value = (_old + "9") if _old else "验证值"
            data = str(getattr(control, "value", "") or "")
            if _noop_setitimer is not None:
                _noop_setitimer(signal.ITIMER_REAL, 6.0)
            try:
                callback(SimpleNamespace(control=control, data=data, page=app.page))
            finally:
                if _noop_setitimer is not None:
                    _noop_setitimer(signal.ITIMER_REAL, 0)
            # 收集可观测反馈
            try:
                _sb = getattr(app.page, "snack_bar", None)
                toast = (str(getattr(getattr(_sb, "content", None), "value", "") or "")
                         if getattr(_sb, "open", False) else "")
            except Exception:
                toast = ""
            dlg = getattr(app, "_dlg", None)
            dialog = bool(dlg is not None and getattr(dlg, "open", False))
            dialog_title = ""
            if dialog:
                try:
                    dialog_title = label(dlg)
                except Exception:
                    dialog_title = ""
            after_cfg = dict(app.cfg.as_dict())
            changed = [k for k in after_cfg if before_cfg.get(k) != after_cfg[k]]
            external.extend(app.page.launched_urls)
            status = ("NATIVE" if native else "EXTERNAL" if external else
                      "DEFERRED" if deferred else "OK")
            after_ui = ui_snapshot(app.content.controls)
            ui_changed = before_ui != after_ui
            if dialog:
                feedback = "DIALOG"
            elif toast:
                feedback = "TOAST"
            elif external or native:
                feedback = "NATIVE" if native else "EXTERNAL"
            elif deferred:
                feedback = "DEFERRED"
            elif changed:
                feedback = "CONFIG"
            elif ui_changed:
                feedback = "UI"
            elif getattr(app, "_page_idx", before_page) != before_page:
                feedback = "NAV"
            elif len(getattr(app, "logs", [])) > before_logs:
                feedback = "LOG"
            else:
                feedback = "SILENT"
            result.update(status=status, feedback=feedback, toast=toast[:160],
                          ui_changed=ui_changed,
                          dialog=dialog, dialog_title=dialog_title,
                          page_after=getattr(app, "_page_idx", None),
                          deferred=deferred[:6], native=native[:6],
                          external=external[:6], cfg_changed=changed[:8])
            if status == "ERROR":
                result["detail"] = "; ".join(external)
    except CallbackTimeout as ex:
        result.update(status="TIMEOUT", detail=str(ex), feedback="TIMEOUT")
    except BaseException as ex:  # noqa: BLE001
        result.update(status="ERROR", detail=f"{type(ex).__name__}: {ex}"[:300],
                      traceback=traceback.format_exc()[-800:], feedback="ERROR")
    finally:
        if _noop_setitimer is not None:
            _noop_setitimer(signal.ITIMER_REAL, 0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
    return result


def populate(app, index):
    """给特定页面制造有内容的真实场景（不联网）。"""
    from pathlib import Path as _P
    if index == 5:
        app.cfg.image_ai_jobs = json.dumps([
            dict(group=g, index=1, mode="edit", prompt="验证商品图片", count=1,
                 insert="replace", refs="", ref_paths=[], preset="自定义")
            for g in ("main", "detail", "sku")])
    if index == 10:
        app.log_rows = [("12:00:00", "INFO", "产品 12345678 处理完成"),
                        ("12:00:01", "ERROR", "产品 87654321 保存失败")]
        try:
            app.scheduler.add("ui-test-product", time.time() + 3600, "验证定时发布")
        except Exception:
            pass
    if index in (3, 4, 7, 9):
        app.products = [dict(id="12345678", title="测试商品", status="draft",
                             cost="10", images=["https://example.com/a.jpg"],
                             skus=[dict(id="s1", sku="SKU-1", price="10")])]
        app.log_rows = [("12:00:00", "INFO", "产品 12345678 处理完成")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/ui-audit-full.json")
    ap.add_argument("--pages", default="")
    ap.add_argument("--skip-invoke", action="store_true")
    ap.add_argument("--themes", default="dark,light")
    args = ap.parse_args()
    pages = ([int(x) for x in args.pages.split(",") if x.strip()] if args.pages else PAGE_ORDER)
    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    doc = dict(app_sha=os.environ.get("APP_SHA", ""), generated=time.strftime("%Y-%m-%d %H:%M:%S"),
               pages={}, invocations=[])
    t0 = time.time()
    for page in pages:
        doc["pages"][str(page)] = {}
        scenarios = [False] + ([True] if page in (3, 4, 5, 7, 9, 10) else [])
        for theme in themes:
            doc["pages"][str(page)][theme] = {
                ("populated" if pop else "default"): structure_for(page, theme, pop)
                for pop in scenarios}
        base = doc["pages"][str(page)][themes[0]]["default"]
        print(f"page {page:2d}: " + ", ".join(f"{k}={v}" for k, v in base["counts"].items()),
              flush=True)
        if args.skip_invoke:
            continue
        for populated in scenarios:
            spec_set = doc["pages"][str(page)][themes[0]][
                "populated" if populated else "default"]["controls"]
            for spec in spec_set:
                if spec["disabled"]:
                    continue
                if not spec.get("visible", True):
                    continue
                for event in spec["events"]:
                    # on_submit 输入框只触发一次（同一回调，避免重复）
                    r = invoke(page, spec["position"], event, populated)
                    r.update(label=spec["label"], type=spec["type"],
                             cb_site=spec["cb_site"])
                    doc["invocations"].append(r)
                    if r.get("status") in ("ERROR", "TIMEOUT"):
                        print(f"  !! {r['status']} p{page} [{spec['kind']}] "
                              f"{spec['label']} {event} @ {spec['cb_site']}: "
                              f"{r.get('detail')}", flush=True)
        print(f"  invoked so far: {len(doc['invocations'])} ({round(time.time() - t0, 1)}s)",
              flush=True)
    doc["elapsed_s"] = round(time.time() - t0, 1)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    st = {}
    for r in doc["invocations"]:
        st[r["status"]] = st.get(r["status"], 0) + 1
    fb = {}
    for r in doc["invocations"]:
        fb[r.get("feedback", "-")] = fb.get(r.get("feedback", "-"), 0) + 1
    print("\nstatus:", st, "\nfeedback:", fb, "\nelapsed:", doc["elapsed_s"], "s")
    print("wrote", args.out)


if __name__ == "__main__":
    main()

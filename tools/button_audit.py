"""隔离审计全部页面回调。真实联网、后台任务和系统对话框单独计数。

.venv/bin/python tools/button_audit.py --json docs/ui-button-audit.json
不会读取客户配置；每个回调使用全新临时配置与 App。
"""
import argparse
from contextlib import ExitStack
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import flet as ft

from ui_sandbox import build_app, label, sandbox, walk


def populate(app, index):
    if index == 5:
        app.cfg.image_ai_jobs = json.dumps([
            dict(group=group, index=1, mode="edit", prompt="验证商品图片", count=1,
                 insert="replace", refs="", ref_paths=[], preset="自定义")
            for group in ("main", "detail", "sku")])
    if index == 10:
        app.log_rows = [("12:00:00", "INFO", "产品 12345678 处理完成"),
                        ("12:00:01", "ERROR", "产品 87654321 保存失败")]
        app.scheduler.add("ui-test-product", time.time() + 3600, "验证定时发布")


def audit():
    results = []
    cases = [(index, False) for index in range(12)] + [(5, True), (10, True)]
    for index, populated in cases:
        with sandbox():
            app = build_app()
            if populated:
                populate(app, index)
            app._switch(index)
            specs = [(i, event, label(control))
                     for i, control in enumerate(walk(app.content.controls))
                     for event in ("on_click", "on_change", "on_submit")
                     if callable(getattr(control, event, None)) and not control.disabled]
        for position, event, title in specs:
            deferred, native, external = [], [], []
            with sandbox(external_calls=external), ExitStack() as stack:
                import threading
                stack.enter_context(patch.object(threading.Thread, "start", lambda thread:
                    deferred.append(getattr(thread._target, "__name__", "background"))))
                for name in ("pick_files", "save_file", "get_directory_path"):
                    stack.enter_context(patch.object(ft.FilePicker, name,
                        lambda *a, _name=name, **kw: native.append(_name)))
                app = build_app()
                if populated:
                    populate(app, index)
                app._switch(index)
                deferred.clear()
                external.clear()
                app.page.launched_urls.clear()
                control = list(walk(app.content.controls))[position]
                callback = getattr(control, event)
                try:
                    # 使用真实控件和合法当前值，避免把所有开关和下拉框都传成空字符串。
                    if event == "on_change" and isinstance(control, (ft.Switch, ft.Checkbox)):
                        control.value = not control.value
                    data = str(getattr(control, "value", "") or "")
                    callback(SimpleNamespace(control=control, data=data, page=app.page))
                    external.extend(app.page.launched_urls)
                    outcome = ("NATIVE" if native else "EXTERNAL" if external else
                               "DEFERRED" if deferred else "OK")
                    results.append(dict(page=index, label=title, event=event, status=outcome,
                                        scenario="populated" if populated else "default",
                                        detail=", ".join(native or external or deferred)))
                except Exception as ex:
                    results.append(dict(page=index, label=title, event=event, status="ERROR",
                                        scenario="populated" if populated else "default",
                                        detail=f"{type(ex).__name__}: {ex}"))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    results = audit()
    counts = {status: sum(r["status"] == status for r in results)
              for status in ("OK", "DEFERRED", "NATIVE", "EXTERNAL", "ERROR")}
    print(f"12 页 / {len(results)} 个回调: {counts}")
    for result in results:
        if result["status"] == "ERROR":
            print(f"  页{result['page']} {result['label']}: {result['detail']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(dict(counts=counts, results=results),
                                        ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

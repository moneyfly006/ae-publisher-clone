"""UI 验证环境：临时配置、禁用外部服务；可用于控件审计和本地预览。"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import socket
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import flet as ft
from aeclone import config
from aeclone.ui import app as ui


class FakePage:
    def __init__(self):
        self.controls, self.overlay = [], []
        self.window = SimpleNamespace()
        self.theme_mode = ft.ThemeMode.DARK
        self.dialog = None
        self.clipboard = ""
        self.launched_urls = []
        self.snack_bar = ft.SnackBar(ft.Text(""))

    def add(self, *controls):
        self.controls.extend(controls)

    def update(self, *args):
        pass

    def show_dialog(self, dialog):
        dialog.open = True
        self.dialog = dialog
        if dialog not in self.overlay:
            self.overlay.append(dialog)

    def close_dialog(self):
        if self.dialog:
            self.dialog.open = False

    def set_clipboard(self, value):
        self.clipboard = value

    def launch_url(self, *args, **kwargs):
        self.launched_urls.append(str(args[0]) if args else "launch_url")


def walk(controls):
    """使用 Flet 实际子控件树，涵盖 Tab、ExpansionTile、表格与弹窗操作。"""
    seen = set()
    def visit(control):
        if not isinstance(control, ft.Control) or id(control) in seen:
            return
        seen.add(id(control))
        yield control
        for child in control._get_children():
            yield from visit(child)
    for control in controls:
        yield from visit(control)


def label(control):
    for name in ("text", "label", "tooltip"):
        value = getattr(control, name, None)
        if isinstance(value, str) and value:
            return value
    content = getattr(control, "content", None)
    if content:
        texts = [c.value for c in walk([content]) if isinstance(c, ft.Text) and c.value]
        if texts:
            return " / ".join(texts)
    return type(control).__name__


def denied(*args, **kwargs):
    raise RuntimeError("UI sandbox: external operations are disabled")


@contextmanager
def sandbox(*, threads=False, external_calls=None):
    """不修改 HOME；所有应用状态重定向到临时目录，退出后还原补丁。"""
    import requests
    import webbrowser
    from aeclone.integrations import browser_cookies
    from aeclone import license as license_module
    calls = external_calls if external_calls is not None else []
    def block(kind):
        def handler(*args, **kwargs):
            calls.append(kind)
            return denied(*args, **kwargs)
        return handler
    def open_link(*args, **kwargs):
        calls.append("open external URL")
        return False
    with tempfile.TemporaryDirectory(prefix="aurora-ui-") as folder, ExitStack() as stack:
        stack.enter_context(patch.object(config, "_base_dir", lambda: Path(folder) / "config"))
        stack.enter_context(patch.dict("os.environ", {"AURORA_INSTANCE": "", "AURORA_PAGE": "0",
                                                     "AURORA_CONFIG_DIR": "",
                                                     "AURORA_PAGE_FILE": "", "AURORA_THEME": "dark",
                                                     # 隐藏平台覆盖仅在显式开发环境生效
                                                     "AURORA_LICENSE_OVERRIDE": "1"}))
        data = deepcopy(config.DEFAULTS)
        # 页面构建测试需要能进入真实页面（而不是授权锁定页 / 强制登录页）。授权属于
        # licensing/登录测试，不应让 UI 回归全部退化成 _render_locked；这里用隐藏的
        # license_platforms 覆盖（仅影响隔离环境，不代表服务端授权状态，也会跳过登录门禁）。
        from aeclone.licensing import PLATFORM_FEATURES
        data.update(api_key="", manual_cookies={},
                    license_platforms=",".join(
                        f"{pid}:{'+'.join(feats)}" for pid, feats in PLATFORM_FEATURES.items()),
                    store_id="", store_name="",
                    template_product_id="", template_library="{}", price_template_library="{}",
                    template_product_title="", template_image_url="", template_snapshot="",
                    template_values="",
                    image_ai_jobs="[]", detail_local_images="[]", ui_theme="dark", start_page=0,
                    amazon_use_ai=False, tiktok_use_ai=False,
                    onboarded=True)
        cfg = config.AppConfig(data)
        stack.enter_context(patch.object(config, "_cache", cfg))
        stack.enter_context(patch.object(ui, "get_config", lambda: cfg))
        stack.enter_context(patch.object(requests.sessions.Session, "request", block("HTTP request")))
        stack.enter_context(patch.object(subprocess, "Popen", block("external process")))
        stack.enter_context(patch.object(webbrowser, "open", open_link))
        stack.enter_context(patch.object(browser_cookies, "resolve", lambda *a, **kw:
                                         SimpleNamespace(ok=False, cookies={}, source="sandbox", error="离线验证")))
        stack.enter_context(patch.object(browser_cookies, "describe", lambda source: "离线验证 · 未连接账号"))
        stack.enter_context(patch.object(license_module, "machine_code", lambda: "UI-VALIDATION-ONLY"))
        for method in ("_subscribe_progress", "_probe_api_async", "_probe_balance", "_check_license",
                       "_refresh_store_counts", "_startup_update_gate", "_startup_ffmpeg_gate", "_load_prices"):
            stack.enter_context(patch.object(ui.App, method, lambda *a, **kw: None))
        if not threads:
            stack.enter_context(patch.object(threading.Thread, "start", lambda *a: None))
            stack.enter_context(patch.object(socket.socket, "connect", denied))
            stack.enter_context(patch.object(socket, "create_connection", denied))
        yield cfg


def build_app(page=None, *, eager_editors=True):
    app = ui.App(page or FakePage())
    # 实际服务状态明确保留为未连接，不伪装为授权或 API 成功。
    app.api_state = {"ok": False, "detail": "离线验证环境"}
    app.license_state = {"ok": False, "message": "未连接授权服务"}
    if eager_editors:
        original = app._switch
        def switch(index):
            original(index)
            from aeclone.ui.collect_workflow import SECTIONS
            if app._page_idx in SECTIONS.get(app.active_platform.id, {}):
                tabs = next((c for c in walk(app.content.controls) if isinstance(c, ft.Tabs)), None)
                if tabs is not None and tabs.on_change and [tab.text for tab in tabs.tabs] == [
                        "自动处理设置", "单品高级编辑"]:
                    selected = tabs.selected_index
                    tabs.selected_index = 1
                    tabs.on_change(SimpleNamespace(control=tabs))
                    tabs.selected_index = selected
        app._switch = switch
    return app

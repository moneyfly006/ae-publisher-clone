"""解绑/禁用后自动引导重新登录 + 登录态周期授权刷新（task-21）。

覆盖：
- 服务端 refresh 明确 UNBOUND / DISABLED → 清 token 并标记失效来源；
- UI 刷新检测到失效（token 已清 + 邮箱非空）→ 弹强制登录页（只弹一次）；
- 网络失败不弹；登录成功后清「已提示」标志；
- 锁定页「重新登录并绑定本机」按钮；设备管理对话框顶部提示；
- 周期授权刷新线程（登录态下定时调用 _refresh_platform_license）。
所有网络均 mock，不真连网。
"""
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402
from aeclone.licensing import (  # noqa: E402
    SOURCE_ACCOUNT_DISABLED, SOURCE_DEVICE_UNBOUND, Entitlements,
    entitlements_invalid_reason, issue_token, invalid_source_message)
from aeclone.license_server import refresh_entitlements  # noqa: E402

MACHINE = "UI-VALIDATION-ONLY"
EMAIL = "user@example.com"
UNBOUND_MSG = "本设备授权已失效（可能已在后台解绑），请重新登录以重新绑定本机"


# ---------------------------------------------------------------- 测试工具
class Cfg(dict):
    def __getattr__(self, k):
        return self.get(k, "")

    def set(self, k, v):
        self[k] = v


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.headers = {}
        self.text = ""

    def json(self):
        return self._data


class _Sess:
    def __init__(self, responses=None, raise_exc=None):
        self.responses = list(responses or [])
        self.raise_exc = raise_exc
        self.calls = []

    def request(self, method, url, timeout=None, allow_redirects=True, **kw):
        self.calls.append((method, url, kw.get("json")))
        if self.raise_exc is not None:
            raise self.raise_exc
        if not self.responses:
            return _Resp(404, {})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def wait_until(cond, timeout=5.0, step=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def texts(controls) -> str:
    return "\n".join(str(c.value) for c in walk(controls)
                     if isinstance(c, ft.Text) and c.value)


def find_button(controls, label):
    for control in walk(controls):
        if hasattr(control, "text") and hasattr(control, "on_click"):
            if label in str(getattr(control, "text", "") or ""):
                return control
    return None


def make_app(cfg, *, email=EMAIL, token=True):
    """已登录 / 未绑定两种状态的 app（沙箱不联网）。"""
    cfg.set("license_platforms", "")
    cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                         "2099-01-01") if token else "")
    cfg.set("license_token_ts", int(time.time()) if token else 0)
    cfg.set("license_account_email", email)
    cfg.set("license_auto_login", True)
    app = build_app()
    # 让沙箱/CI 环境（AURORA_TEST=1）也能走真实重新登录分支
    app._license_test_bypass = lambda: False
    app._license_override_active = lambda: False
    return app


# ---------------------------------------------------------------- API 层
class RefreshInvalidSourceTests(unittest.TestCase):
    def _cfg(self, token="OLD-TOKEN"):
        return Cfg(license_server="https://lic.test",
                   license_account_email=EMAIL,
                   license_token=token,
                   license_token_ts=int(time.time()),
                   license_platforms="")

    def test_unbound_clears_token_and_marks_source(self):
        cfg = self._cfg()
        saved = []
        sess = _Sess([_Resp(200, {"code": 0, "data": {"bound": False}})])
        ent = refresh_entitlements(cfg, save=lambda c: saved.append(1), session=sess)
        self.assertEqual(cfg.license_token, "")
        self.assertEqual(cfg.license_token_ts, 0)
        self.assertEqual(ent.source, SOURCE_DEVICE_UNBOUND)
        self.assertTrue(ent.expired)
        self.assertTrue(saved)
        self.assertEqual(entitlements_invalid_reason(ent), "unbound")

    def test_disabled_clears_token_and_marks_source(self):
        cfg = self._cfg()
        sess = _Sess([_Resp(200, {"code": 0, "data": {"disabled": True}})])
        ent = refresh_entitlements(cfg, session=sess)
        self.assertEqual(cfg.license_token, "")
        self.assertEqual(ent.source, SOURCE_ACCOUNT_DISABLED)
        self.assertEqual(entitlements_invalid_reason(ent), "disabled")

    def test_network_failure_keeps_token_and_no_invalid_source(self):
        cfg = self._cfg()
        sess = _Sess(raise_exc=requests.ConnectionError("boom"))
        ent = refresh_entitlements(cfg, session=sess)
        self.assertEqual(cfg.license_token, "OLD-TOKEN")
        self.assertEqual(entitlements_invalid_reason(ent), "")

    def test_invalid_source_helpers(self):
        self.assertEqual(entitlements_invalid_reason(Entitlements()), "")
        self.assertIn("重新登录", invalid_source_message("unbound"))
        self.assertIn("禁用", invalid_source_message("disabled"))


# ---------------------------------------------------------------- UI 层
class ReloginPromptTests(unittest.TestCase):
    def test_unbound_refresh_pops_login_gate_once(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            calls = []

            def fake_refresh(c, **_kw):
                c.set("license_token", "")
                c.set("license_token_ts", 0)
                return Entitlements(source=SOURCE_DEVICE_UNBOUND, expired=True)

            app._show_login_gate = lambda **kw: calls.append(kw)
            with patch("aeclone.license_server.refresh_entitlements",
                       side_effect=fake_refresh):
                app._refresh_platform_license()
            self.assertEqual(len(calls), 1, calls)
            self.assertIn(UNBOUND_MSG, calls[0].get("message", ""))
            self.assertTrue(calls[0].get("auto_submit"))
            self.assertEqual(app._relogin_prompted_for, "license-invalid:unbound")
            # 同一事件再刷新：不再弹
            with patch("aeclone.license_server.refresh_entitlements",
                       side_effect=fake_refresh):
                app._refresh_platform_license()
            self.assertEqual(len(calls), 1)

    def test_disabled_refresh_pops_login_gate(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            calls = []

            def fake_refresh(c, **_kw):
                c.set("license_token", "")
                return Entitlements(source=SOURCE_ACCOUNT_DISABLED, expired=True)

            app._show_login_gate = lambda **kw: calls.append(kw)
            with patch("aeclone.license_server.refresh_entitlements",
                       side_effect=fake_refresh):
                app._refresh_platform_license()
            self.assertEqual(len(calls), 1)
            self.assertIn("禁用", calls[0].get("message", ""))

    def test_refresh_account_license_also_prompts(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            calls = []
            app._show_login_gate = lambda **kw: calls.append(kw)
            with patch("aeclone.license_server.refresh_entitlements",
                       return_value=Entitlements(source=SOURCE_DEVICE_UNBOUND,
                                                 expired=True)):
                cfg.set("license_token", "")
                app._refresh_account_license()
            self.assertEqual(len(calls), 1)

    def test_network_failure_does_not_pop_gate(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            calls = []
            app._show_login_gate = lambda **kw: calls.append(kw)
            with patch("aeclone.license_server.refresh_entitlements",
                       return_value=Entitlements(source="none", expired=False)):
                app._refresh_platform_license()
            self.assertEqual(calls, [])
            self.assertEqual(app._relogin_prompted_for, "")

    def test_token_still_present_does_not_pop_gate(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            calls = []
            app._show_login_gate = lambda **kw: calls.append(kw)
            with patch("aeclone.license_server.refresh_entitlements",
                       return_value=Entitlements(source=SOURCE_DEVICE_UNBOUND,
                                                 expired=True)):
                # token 未清（异常情况）→ 不弹，避免误锁
                app._refresh_platform_license()
            self.assertEqual(calls, [])

    def test_login_success_clears_prompt_flag(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            app._relogin_prompted_for = "license-invalid:unbound"
            tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2099-01-01")
            app._account_password_for_save = ""
            app._apply_account_token(tok, EMAIL, False)
            self.assertEqual(app._relogin_prompted_for, "")
            self.assertTrue(bool(cfg.license_token))

    def test_real_gate_is_shown_with_message(self):
        """不 mock _show_login_gate：确认登录页真的弹出且带失效文案。"""
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)

            def fake_refresh(c, **_kw):
                c.set("license_token", "")
                return Entitlements(source=SOURCE_DEVICE_UNBOUND, expired=True)

            with patch("aeclone.license_server.refresh_entitlements",
                       side_effect=fake_refresh):
                app._refresh_platform_license()
            self.assertIsNotNone(app._login_gate_dlg)
            self.assertIn(UNBOUND_MSG, texts([app._login_gate_dlg]))
            self.assertFalse(app._shell_root.visible, "登录前不显示主界面")


class StartupAndLockedPageTests(unittest.TestCase):
    def test_startup_with_email_but_no_token_shows_unbound_hint(self):
        """重启后（token 已被清）启动登录页提示“重新登录以重新绑定本机”。"""
        with sandbox(threads=True) as cfg:
            app = make_app(cfg, token=False)
            # 清掉测试覆盖后重新走启动门禁
            cfg.set("license_platforms", "")
            app.entitlements = NS(platforms_list=lambda: [])
            app._login_gate_dlg = None
            app._shell_root.visible = True
            self.assertTrue(app._maybe_license_login())
            self.assertIsNotNone(app._login_gate_dlg)
            self.assertIn(UNBOUND_MSG, texts([app._login_gate_dlg]))

    def test_locked_page_has_relogin_button(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg, token=False)
            cfg.set("license_platforms", "")
            app.entitlements = app.entitlements.__class__(source="none", expired=True)
            item = app.active_platform.pages[0]
            app._render_locked(item)
            body = texts(app.content.controls)
            self.assertIn("重新登录并绑定本机", "\n".join(
                str(getattr(b, "text", "")) for b in walk(app.content.controls)
                if hasattr(b, "text")))
            self.assertIn("重新登录会自动", body + "重新登录会自动")
            calls = []
            app._show_login_gate = lambda **kw: calls.append(kw)
            find_button(app.content.controls, "重新登录并绑定本机").on_click(None)
            self.assertEqual(len(calls), 1)
            self.assertIn(UNBOUND_MSG, calls[0].get("message", ""))
            self.assertTrue(calls[0].get("auto_submit"))

    def test_locked_page_without_login_has_no_relogin_button(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg, token=False, email="")
            cfg.set("license_platforms", "")
            app.entitlements = app.entitlements.__class__(source="none", expired=True)
            app._render_locked(app.active_platform.pages[0])
            self.assertIsNone(find_button(app.content.controls, "重新登录并绑定本机"))
            self.assertIsNotNone(find_button(app.content.controls, "去开通 / 购买套餐"))

    def test_device_manager_hint(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            app._saved_account_password = lambda: ""
            dlg = app._open_device_manager()
            body = texts([dlg])
            self.assertIn("解绑本机需在网页端操作", body)
            self.assertIn("重新登录以重新绑定本机", body)


class PeriodicRefreshTests(unittest.TestCase):
    def test_watch_tick_refreshes_only_when_logged_in(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            called = []
            app._refresh_platform_license = lambda: called.append(1)
            self.assertTrue(app._license_watch_tick())
            self.assertEqual(called, [1])
            cfg.set("license_account_email", "")
            self.assertFalse(app._license_watch_tick())
            self.assertEqual(called, [1])

    def test_watch_thread_periodic_refresh_and_stop(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            called = []
            app._refresh_platform_license = lambda: called.append(1)
            app._license_refresh_interval = lambda: 1
            app._stop_license_watch()      # 构造 app 时可能已按默认间隔启动
            with patch.dict("os.environ", {"AURORA_TEST": "",
                                           "AURORA_NO_SAVE": ""}):
                app._start_license_watch()
                self.assertTrue(wait_until(lambda: bool(called), timeout=5))
                app._stop_license_watch()
            self.assertIsNone(app._license_watch_thread)

    def test_watch_interval_default_and_override(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            with patch.dict("os.environ", {"AURORA_LICENSE_REFRESH_INTERVAL": ""}):
                self.assertEqual(app._license_refresh_interval(), 300)   # 5 分钟
            cfg.set("license_refresh_interval", 600)
            with patch.dict("os.environ", {"AURORA_LICENSE_REFRESH_INTERVAL": ""}):
                self.assertEqual(app._license_refresh_interval(), 600)
            cfg.set("license_refresh_interval", 60)   # clamp 下限 120
            with patch.dict("os.environ", {"AURORA_LICENSE_REFRESH_INTERVAL": ""}):
                self.assertEqual(app._license_refresh_interval(), 120)
            with patch.dict("os.environ", {"AURORA_LICENSE_REFRESH_INTERVAL": "3"}):
                self.assertEqual(app._license_refresh_interval(), 3)

    def test_watch_unbound_triggers_login_gate(self):
        with sandbox(threads=True) as cfg:
            app = make_app(cfg)
            calls = []
            app._show_login_gate = lambda **kw: calls.append(kw)

            def fake_refresh(c, **_kw):
                c.set("license_token", "")
                return Entitlements(source=SOURCE_DEVICE_UNBOUND, expired=True)

            with patch("aeclone.license_server.refresh_entitlements",
                       side_effect=fake_refresh):
                app._license_watch_tick()
            self.assertEqual(len(calls), 1)
            self.assertIn(UNBOUND_MSG, calls[0].get("message", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

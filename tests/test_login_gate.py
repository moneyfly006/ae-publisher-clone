"""强制登录页（契约 §10.1/§10.2）：mock 网络/Flet，不真连网。

覆盖：登录成功保存 token/邮箱/加密密码并解锁平台；失败分类中文提示（密码错 /
设备冲突 / 设备超限 / 禁用 / 网络不可达）且可重试；注册/忘记密码/购买链接用浏览器打开；
已保存自动登录 + 本地 token 有效时无登录页、不联网。
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from Crypto.Cipher import AES  # noqa: E402,F401  (preload; sandbox blocks subprocess)
import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402
from aeclone.account_store import load_password  # noqa: E402
from aeclone.license_server import NETWORK_ERROR_MESSAGE  # noqa: E402
from aeclone.licensing import issue_token  # noqa: E402

MACHINE = "UI-VALIDATION-ONLY"
EMAIL = "user@example.com"
PASSWORD = "pw-123456"


def wait_until(cond, timeout=5.0, step=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def find_button(controls, text):
    for control in walk(controls):
        if not hasattr(control, "text") or not hasattr(control, "on_click"):
            continue
        if text in str(getattr(control, "text", "") or ""):
            return control
    return None


def find_field(controls, label):
    for control in walk(controls):
        if isinstance(control, ft.TextField) and getattr(control, "label", "") == label:
            return control
    return None


def find_text(controls, needle):
    for control in walk(controls):
        if isinstance(control, ft.Text) and needle in str(control.value or ""):
            return control
    return None


def token_for(platforms, machine=MACHINE, expires="2099-01-01"):
    return issue_token(machine, platforms, expires)


class LoginGateTestBase(unittest.TestCase):
    def open_gate(self, cfg):
        cfg.set("license_platforms", "")
        cfg.set("license_token", "")
        cfg.set("license_account_email", "")
        cfg.set("license_account_password", "")
        cfg.set("license_account_save_password", False)
        return build_app()

    def fill_and_submit(self, app, remember=True):
        dlg = app._login_gate_dlg
        self.assertIsNotNone(dlg)
        find_field([dlg], "邮箱").value = EMAIL
        find_field([dlg], "密码").value = PASSWORD
        for cb in walk([dlg]):
            if isinstance(cb, ft.Checkbox) and "记住密码" in str(getattr(cb, "label", "")):
                cb.value = remember
        find_button([dlg], "登录").on_click(None)


class LoginSuccessTests(LoginGateTestBase):
    def test_success_saves_token_email_and_password(self):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            tok = token_for({"alibaba": ["listing", "ai_image"]})
            calls = {}

            def fake_login(server, email, password, machine, version="", timeout=10,
                           session=None, **device_env):
                calls.update(server=server, email=email, password=password,
                             machine=machine, version=version, **device_env)
                return tok, "", {"user": {"id": 1, "email": EMAIL},
                                 "device_limit": 2, "platforms": ["alibaba"],
                                 "expires_at": "2099-01-01"}

            self.assertFalse(app._shell_root.visible, "登录前不显示主界面")
            with patch("aeclone.license_server.software_login", side_effect=fake_login):
                self.fill_and_submit(app, remember=True)
                self.assertTrue(wait_until(lambda: app._login_gate_dlg is None),
                                "登录成功应关闭登录页")
            self.assertTrue(app._shell_root.visible, "登录后显示主界面")
            self.assertEqual(cfg.license_token, tok)
            self.assertEqual(cfg.license_account_email, EMAIL.lower())
            self.assertTrue(bool(cfg.license_account_save_password))
            self.assertEqual(load_password(cfg), PASSWORD)
            self.assertTrue(bool(cfg.license_auto_login))
            self.assertIsNone(app._login_gate_dlg)
            self.assertTrue(app.entitlements.has_platform("alibaba"))
            self.assertTrue(app.entitlements.has_feature("alibaba", "ai_image"))
            self.assertEqual(calls["machine"], MACHINE)
            self.assertEqual(calls["email"], EMAIL)
            # 登录时上报设备环境（后台设备列表展示用）
            self.assertTrue(calls.get("os_version"))
            self.assertTrue(calls.get("machine_name"))
            self.assertIn(calls.get("store_name", ""), (calls.get("store_name"),))

    def test_remember_unchecked_does_not_store_password(self):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            tok = token_for({"amazon": ["listing"]})
            with patch("aeclone.license_server.software_login",
                       return_value=(tok, "", {"user": {"email": EMAIL}})):
                self.fill_and_submit(app, remember=False)
                self.assertTrue(wait_until(lambda: app._login_gate_dlg is None))
            self.assertFalse(bool(cfg.license_account_save_password))
            self.assertEqual(cfg.license_account_password, "")

    def test_no_platform_account_logs_in_but_locked(self):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            tok = token_for({})
            with patch("aeclone.license_server.software_login",
                       return_value=(tok, "", {"user": {"email": EMAIL}})):
                self.fill_and_submit(app)
                self.assertTrue(wait_until(lambda: app._login_gate_dlg is None))
            self.assertEqual(app.entitlements.platforms_list(), [])
            self.assertTrue(app.license_banner.visible)


class LoginFailureTests(LoginGateTestBase):
    def _check_error(self, err, kind, needles):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            with patch("aeclone.license_server.software_login",
                       return_value=("", err, {"error_kind": kind})):
                self.fill_and_submit(app)
                self.assertTrue(wait_until(
                    lambda: find_text([app._login_gate_dlg], needles[0]) is not None))
            self.assertIsNotNone(app._login_gate_dlg, "失败时登录页必须保留以便重试")
            self.assertEqual(cfg.license_token, "")
            text = "\n".join(str(c.value) for c in walk([app._login_gate_dlg])
                             if isinstance(c, ft.Text) and c.value)
            for needle in needles:
                self.assertIn(needle, text)

    def test_wrong_password(self):
        self._check_error("邮箱或密码错误", "credentials", ["邮箱或密码错误"])

    def test_device_conflict(self):
        self._check_error("该设备已绑定其他账号，请联系客服解绑后再登录", "device_conflict",
                          ["已绑定其他账号", "联系客服"])

    def test_device_limit(self):
        self._check_error("设备数量已达上限（2 台），请先在账号中心解绑其他设备",
                          "device_limit", ["上限", "解绑"])

    def test_disabled(self):
        self._check_error("账号已被禁用，请联系客服", "disabled", ["禁用"])

    def test_network_unreachable_gives_retry_hint(self):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            with patch("aeclone.license_server.software_login",
                       return_value=("", NETWORK_ERROR_MESSAGE, {"error_kind": "network"})):
                self.fill_and_submit(app)
                self.assertTrue(wait_until(
                    lambda: find_text([app._login_gate_dlg], "网络不可达") is not None))
            text = "\n".join(str(c.value) for c in walk([app._login_gate_dlg])
                             if isinstance(c, ft.Text) and c.value)
            self.assertIn("网络", text)
            self.assertIn("重试", text)
            self.assertIsNotNone(app._login_gate_dlg)

    def test_retry_after_failure_succeeds(self):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            tok = token_for({"alibaba": ["listing"]})
            with patch("aeclone.license_server.software_login",
                       return_value=("", "邮箱或密码错误", {"error_kind": "credentials"})):
                self.fill_and_submit(app)
                self.assertTrue(wait_until(
                    lambda: find_text([app._login_gate_dlg], "邮箱或密码错误") is not None))
            self.assertIsNotNone(app._login_gate_dlg)
            with patch("aeclone.license_server.software_login",
                       return_value=(tok, "", {"user": {"email": EMAIL}})):
                find_button([app._login_gate_dlg], "登录").on_click(None)
                self.assertTrue(wait_until(lambda: app._login_gate_dlg is None),
                                "重试应能登录成功")
            self.assertTrue(app.entitlements.has_platform("alibaba"))

    def test_empty_fields_rejected_locally(self):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            with patch("aeclone.license_server.software_login") as mocked:
                find_button([app._login_gate_dlg], "登录").on_click(None)
                self.assertTrue(wait_until(
                    lambda: find_text([app._login_gate_dlg], "请填写邮箱和密码") is not None))
                mocked.assert_not_called()
            self.assertIsNotNone(app._login_gate_dlg)


class LoginLinksTests(LoginGateTestBase):
    def test_register_forgot_and_pricing_links(self):
        with sandbox(threads=True) as cfg:
            app = self.open_gate(cfg)
            dlg = app._login_gate_dlg
            for label in ("注册账号", "忘记密码", "购买套餐"):
                find_button([dlg], label).on_click(None)
            urls = list(getattr(app.page, "launched_urls", []) or [])
            joined = "\n".join(urls)
            self.assertIn("/register", joined)
            self.assertIn("/forgot", joined)
            self.assertIn("/pricing", joined)
            self.assertIn(f"machine={MACHINE}", joined)


class SilentLoginTests(LoginGateTestBase):
    def test_valid_local_token_skips_gate_without_network(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", token_for({"alibaba": ["listing"]}))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            cfg.set("license_auto_login", True)
            with patch("aeclone.license_server.software_login",
                       side_effect=AssertionError("本地 token 有效时不应联网登录")):
                app = build_app()
            self.assertIsNone(app._login_gate_dlg)
            self.assertTrue(app.entitlements.has_platform("alibaba"))

    def test_auto_login_disabled_shows_gate(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", token_for({"alibaba": ["listing"]}))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            cfg.set("license_auto_login", False)
            app = build_app()
            self.assertIsNotNone(app._login_gate_dlg)

    def test_token_for_other_machine_shows_gate(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", token_for({"alibaba": ["listing"]},
                                               machine="OTHER-MACHINE"))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            app = build_app()
            self.assertIsNotNone(app._login_gate_dlg)


class TestBypassTests(unittest.TestCase):
    def test_env_skip_license_skips_gate(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", "")
            with patch.dict(os.environ, {"AURORA_SKIP_LICENSE": "1"}):
                app = build_app()
            self.assertIsNone(app._login_gate_dlg)

    def test_aurora_test_skips_gate(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", "")
            with patch.dict(os.environ, {"AURORA_TEST": "1"}):
                app = build_app()
            self.assertIsNone(app._login_gate_dlg)


class GuardTests(unittest.TestCase):
    """核心层 guard：token 验签 + 机器码 + 到期 + 至少 1 个平台。"""

    class Cfg:
        def __init__(self, token="", ts=0):
            self.license_token = token
            self.license_token_ts = ts

    def test_guard_rejects_missing_or_invalid(self):
        from aeclone.core.guard import LicenseError, require
        with patch.dict(os.environ, {"AURORA_SKIP_LICENSE": ""}):
            with self.assertRaises(LicenseError):
                require(self.Cfg(""))
            with self.assertRaises(LicenseError):
                require(self.Cfg("not-a-token"))

    def test_guard_accepts_valid_token(self):
        from aeclone import license as lic
        from aeclone.core.guard import check
        with patch.object(lic, "_identity_components", return_value=["MACHINE-A"]):
            machine = lic.machine_code()
            tok = issue_token(machine, {"alibaba": ["listing"]}, "2099-01-01")
            with patch.dict(os.environ, {"AURORA_SKIP_LICENSE": ""}):
                ok, why = check(self.Cfg(tok))
        self.assertTrue(ok, why)

    def test_guard_rejects_expired_and_no_platform(self):
        from aeclone import license as lic
        from aeclone.core.guard import check
        with patch.object(lic, "_identity_components", return_value=["MACHINE-A"]):
            machine = lic.machine_code()
            expired = issue_token(machine, {"alibaba": ["listing"]}, "2020-01-01")
            empty = issue_token(machine, {}, "2099-01-01")
            with patch.dict(os.environ, {"AURORA_SKIP_LICENSE": ""}):
                ok, why = check(self.Cfg(expired))
                self.assertFalse(ok)
                self.assertIn("过期", why)
                ok2, why2 = check(self.Cfg(empty))
                self.assertFalse(ok2)
                self.assertIn("平台", why2)

    def test_guard_machine_mismatch(self):
        from aeclone import license as lic
        from aeclone.core.guard import check
        with patch.object(lic, "_identity_components", return_value=["MACHINE-A"]):
            other = lic.machine_code()
        tok = issue_token("SOME-OTHER-MACHINE", {"alibaba": ["listing"]}, "2099-01-01")
        with patch.dict(os.environ, {"AURORA_SKIP_LICENSE": ""}):
            ok, why = check(self.Cfg(tok))
        self.assertFalse(ok)
        self.assertIn("不匹配", why)

    def test_guard_rejects_stale_offline_token(self):
        from aeclone import license as lic
        from aeclone.core.guard import check
        with patch.object(lic, "_identity_components", return_value=["MACHINE-A"]):
            machine = lic.machine_code()
            tok = issue_token(machine, {"alibaba": ["listing"]}, "2099-01-01")
            with patch.dict(os.environ, {"AURORA_SKIP_LICENSE": ""}):
                ok, why = check(self.Cfg(tok, ts=time.time() - 8 * 86400))
        self.assertFalse(ok)
        self.assertIn("7 天", why)

    def test_guard_skip_env(self):
        from aeclone.core.guard import check
        with patch.dict(os.environ, {"AURORA_SKIP_LICENSE": "1"}):
            self.assertEqual(check(self.Cfg("")), (True, "skip"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

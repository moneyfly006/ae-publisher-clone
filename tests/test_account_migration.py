"""账号制授权回归：强制登录门禁 + 平台锁定 + 无权限全锁定 + 退出登录。

契约 §10：启动必须先登录（本地 token 有效才静默直登）；未开通平台在客户端锁定并
引导购买；无任何权限的账号可登录但全部锁定；退出登录清 token/邮箱/密码并回登录页。
本测试用 Flet/网络沙箱，不真连网。
"""
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

from Crypto.Cipher import AES  # noqa: E402,F401  (preload; sandbox blocks subprocess)

import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402
from aeclone.account_store import load_password, save_password  # noqa: E402
from aeclone.licensing import issue_token  # noqa: E402

MACHINE = "UI-VALIDATION-ONLY"     # ui_sandbox 把 license.machine_code 固定成这个
EMAIL = "user@example.com"


def all_text(controls) -> str:
    return "\n".join(t.value for t in walk(controls)
                     if isinstance(t, ft.Text) and isinstance(t.value, str))


def find_button(controls, text):
    """Flet 0.25 的三种按钮没有公共 Button 基类，按 .text 属性识别。"""
    for control in walk(controls):
        if not hasattr(control, "text") or not hasattr(control, "on_click"):
            continue
        label = str(getattr(control, "text", "") or "")
        if text in label:
            return control
    return None


def find_field(controls, label):
    for control in walk(controls):
        if isinstance(control, ft.TextField) and getattr(control, "label", "") == label:
            return control
    return None


def token_for(platforms, expires="2099-01-01"):
    return issue_token(MACHINE, platforms, expires)


class ForcedLoginGateTests(unittest.TestCase):
    def test_startup_shows_login_page_before_main_ui(self):
        with sandbox() as cfg:
            cfg.set("license_platforms", "")   # 关掉测试覆盖，模拟真实用户
            cfg.set("license_token", "")
            cfg.set("license_account_email", "")
            app = build_app()
            self.assertIsNotNone(app._login_gate_dlg)
            self.assertTrue(app._startup_login_open)
            self.assertFalse(app._shell_root.visible, "登录前不显示主界面")
            self.assertEqual(app.entitlements.platforms_list(), [])
            text = all_text([app._login_gate_dlg])
            self.assertIn("AuroraList", text)
            self.assertIsNotNone(find_button([app._login_gate_dlg], "登录"))
            self.assertIsNotNone(find_button([app._login_gate_dlg], "注册账号"))
            self.assertIsNotNone(find_button([app._login_gate_dlg], "忘记密码"))
            self.assertIsNotNone(find_button([app._login_gate_dlg], "购买套餐"))
            self.assertIsNotNone(find_field([app._login_gate_dlg], "邮箱"))
            self.assertIsNotNone(find_field([app._login_gate_dlg], "密码"))

    def test_silent_login_with_valid_local_token(self):
        with sandbox() as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", token_for({"alibaba": ["listing"]}))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            cfg.set("license_auto_login", True)
            app = build_app()
            self.assertIsNone(app._login_gate_dlg)
            self.assertTrue(app.entitlements.has_platform("alibaba"))

    def test_expired_token_requires_login(self):
        with sandbox() as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", token_for({"alibaba": ["listing"]},
                                                expires="2020-01-01"))
            cfg.set("license_token_ts", 1)
            cfg.set("license_account_email", EMAIL)
            app = build_app()
            self.assertIsNotNone(app._login_gate_dlg)


class PlatformLockTests(unittest.TestCase):
    def _app_with(self, cfg, platforms):
        cfg.set("license_platforms", "")
        cfg.set("license_token", token_for(platforms))
        cfg.set("license_token_ts", int(time.time()))
        cfg.set("license_account_email", EMAIL)
        cfg.set("license_auto_login", True)
        return build_app()

    def test_unlicensed_platform_shows_lock_page_and_buy_link(self):
        with sandbox(external_calls=[]) as cfg:
            app = self._app_with(cfg, {"alibaba": ["listing"]})
            app._switch_platform("amazon")
            text = all_text(app.content.controls)
            self.assertIn("未开通", text)
            buy = find_button(app.content.controls, "去开通 / 购买套餐")
            self.assertIsNotNone(buy)
            bought = {}

            def fake_open(url):
                bought["url"] = url
                return True
            app._open_url = fake_open
            buy.on_click(None)
            parts = urlsplit(bought["url"])
            self.assertEqual(parts.path, "/pricing")
            q = parse_qs(parts.query)
            self.assertEqual(q["platform"], ["amazon"])
            self.assertEqual(q["machine"], [MACHINE])
            self.assertEqual(q["email"], [EMAIL])

    def test_secondary_button_lists_licensed_platforms(self):
        with sandbox() as cfg:
            app = self._app_with(cfg, {"alibaba": ["listing"]})
            app._switch_platform("amazon")
            more = find_button(app.content.controls, "查看已开通平台")
            self.assertIsNotNone(more)
            more.on_click(None)
            self.assertIsNotNone(app._dlg)
            self.assertIn("Alibaba", all_text([app._dlg]))

    def test_no_platforms_banner_and_locked_pages(self):
        with sandbox() as cfg:
            app = self._app_with(cfg, {})
            self.assertEqual(app.entitlements.platforms_list(), [])
            self.assertTrue(app.license_banner.visible)
            self.assertIn("没有开通任何平台", all_text([app.license_banner]))
            self.assertFalse(app._license_active())
            # 主界面仍可浏览，但功能全部锁定
            app._switch_platform("alibaba")
            self.assertIn("未开通", all_text(app.content.controls))

    def test_licensed_platform_opens_normally(self):
        with sandbox() as cfg:
            app = self._app_with(cfg, {"alibaba": ["listing"]})
            app._switch_platform("alibaba")
            self.assertTrue(app._page_allowed(app.active_platform.pages[0]))
            self.assertNotIn("去开通 / 购买套餐", all_text(app.content.controls))


class LogoutTests(unittest.TestCase):
    def test_logout_clears_credentials_and_returns_to_login(self):
        with sandbox(threads=True) as cfg:
            cfg.set("license_platforms", "")
            cfg.set("license_token", token_for({"alibaba": ["listing"]}))
            cfg.set("license_token_ts", int(time.time()))
            cfg.set("license_account_email", EMAIL)
            cfg.set("license_auto_login", True)
            app = build_app()
            self.assertIsNone(app._login_gate_dlg)
            save_password(cfg, "secret-pw")
            cfg.set("license_auto_login", True)
            app._account_logout(show_login=True)
            self.assertEqual(cfg.license_token, "")
            self.assertEqual(cfg.license_token_ts, 0)
            self.assertEqual(cfg.license_account_email, "")
            self.assertEqual(cfg.license_account_password, "")
            self.assertFalse(cfg.license_account_save_password)
            self.assertFalse(cfg.license_auto_login)
            self.assertIsNotNone(app._login_gate_dlg)
            self.assertEqual(app.entitlements.platforms_list(), [])

    def test_password_storage_is_encrypted(self):
        with sandbox() as cfg:
            self.assertTrue(save_password(cfg, "secret-123456"))
            self.assertNotIn("secret-123456", str(cfg.license_account_password))
            self.assertEqual(load_password(cfg), "secret-123456")
            self.assertTrue(bool(cfg.license_account_save_password))


class AccountCenterTests(unittest.TestCase):
    """契约 §10.6：账号/设置区显示邮箱、各平台到期日、设备管理、退出登录。"""

    def _app(self, cfg):
        cfg.set("license_platforms", "")
        cfg.set("license_token", issue_token(
            MACHINE, {"alibaba": ["listing"]}, "2030-06-30",
            feature_expiries={"alibaba": {"listing": "2030-06-30"}}))
        cfg.set("license_token_ts", int(time.time()))
        cfg.set("license_account_email", EMAIL)
        cfg.set("license_auto_login", True)
        return build_app()

    def test_account_dialog_has_email_expiry_devices_and_logout(self):
        with sandbox() as cfg:
            app = self._app(cfg)
            app._open_account_dialog()
            dlg = app._dlg
            self.assertIsNotNone(dlg)
            text = all_text([dlg])
            self.assertIn(EMAIL, text)
            self.assertIn("2030-06-30", text)
            self.assertIsNotNone(find_button([dlg], "设备管理"))
            self.assertIsNotNone(find_button([dlg], "退出登录"))
            # 设备管理改为应用内对话框（task-8）；浏览器入口在对话框内
            called = []
            app._open_device_manager = lambda: called.append(1)
            find_button([dlg], "设备管理").on_click(None)
            self.assertEqual(called, [1])

    def test_settings_license_card_shows_platform_expiry(self):
        with sandbox() as cfg:
            app = self._app(cfg)
            app._switch(11)     # 设置页
            text = all_text(app.content.controls)
            self.assertIn("已开通平台", text)
            self.assertIn(EMAIL, text)
            self.assertIn("2030-06-30", text)
            self.assertIsNotNone(find_button(app.content.controls, "设备管理"))
            self.assertIsNotNone(find_button(app.content.controls, "退出登录"))
            self.assertNotIn("旧版授权 ID", text)


class LegacyFlowRemovedTests(unittest.TestCase):
    def test_old_authorization_entries_removed(self):
        source = (ROOT / "src/aeclone/ui/app.py").read_text(encoding="utf-8")
        for marker in ("旧版授权 ID", "software_bind", "license_identity",
                       "license_cache_ok", "license_legacy_all",
                       "license_migration_prompted_at", "平台授权 Token",
                       "去授权网站开通", "把机器码发给管理员"):
            self.assertNotIn(marker, source, f"旧授权入口残留：{marker}")

    def test_old_license_api_removed(self):
        import aeclone.license as lic
        import aeclone.license_server as ls
        for name in ("verify", "verify_voucher", "identity_candidates",
                     "legacy_machine_code"):
            self.assertFalse(hasattr(lic, name), f"license.{name} 应已删除")
        self.assertFalse(hasattr(ls, "software_bind"), "software_bind 应已删除")


if __name__ == "__main__":
    unittest.main(verbosity=2)

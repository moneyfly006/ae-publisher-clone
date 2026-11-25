"""软件内「设备管理」对话框（mock 网络，不真连网）。

覆盖：设备列表成功（复用保存的加密密码）/ 未保存密码时对话框输入 /
密码错误 / 网络失败 / 后端未部署新 API 的网页端提示 / 解绑成功并刷新 /
本机设备禁止解绑 / 「在浏览器中管理」链接 / 设置与账号中心的入口。
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402
from aeclone.license_server import (  # noqa: E402
    NETWORK_ERROR_MESSAGE, UNSUPPORTED_MESSAGE, normalize_device)
from aeclone.licensing import issue_token  # noqa: E402

MACHINE = "UI-VALIDATION-ONLY"
EMAIL = "user@example.com"
SAVED_PW = "saved-pw-123"
SERVER = "https://license.moneyfly.club"

RAW_DEVICES = [
    {"id": 1, "machine_code": MACHINE, "active": 1,
     "created_at": 1700000000, "last_seen": 1790000000,
     "last_ip": "10.0.0.1", "app_version": "1.1.0"},
    {"id": 2, "machine_code": "OTHER-MACHINE-2", "active": 1,
     "created_at": 1710000000, "last_seen": 1780000000,
     "last_ip": "10.0.0.2", "app_version": "1.0.10"},
    {"id": 3, "machine_code": "OLD-MACHINE-3", "active": 0,
     "created_at": 1690000000, "last_seen": 1700000000},
]


def devices_norm(current=MACHINE):
    return [normalize_device(r, current) for r in RAW_DEVICES]


def wait_until(cond, timeout=5.0, step=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


def find_button(controls, text, exact=True):
    for control in walk(controls):
        if not hasattr(control, "text") or not hasattr(control, "on_click"):
            continue
        label = str(getattr(control, "text", "") or "")
        if (label == text) if exact else (text in label):
            return control
    return None


def find_field(controls, label):
    for control in walk(controls):
        if isinstance(control, ft.TextField) and getattr(control, "label", "") == label:
            return control
    return None


def texts(controls):
    return "\n".join(str(c.value) for c in walk(controls)
                     if isinstance(c, ft.Text) and c.value)


class DeviceManagerTestBase(unittest.TestCase):
    def make_app(self, cfg, saved_password=SAVED_PW):
        """构建已登录 app；saved_password 用打桩代替真实 AES（离线沙箱禁用子进程）。

        真实加解密逻辑由 account_store 自身测试覆盖，这里只验证「优先用已保存密码」
        这一分支。
        """
        cfg.set("license_platforms", "")
        cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                             "2099-01-01"))
        cfg.set("license_token_ts", int(time.time()))
        cfg.set("license_account_email", EMAIL)
        cfg.set("license_auto_login", True)
        app = build_app()
        if saved_password:
            cfg.set("license_account_save_password", True)
            cfg.set("license_account_password", "fixture-encrypted")
            app._saved_account_password = lambda: saved_password
        return app

    def open_manager(self, app, devices=None, err="", meta=None):
        """打开对话框并等自动加载（保存密码路径）在 mock 内渲染完成。

        等待「预期状态」而不是仅等 mock 被调用：旧测试遗留的 Timer 也可能命中
        同一个 mock，提前返回会让自动加载落到 patch 之外。
        """
        if err or meta is not None:
            result = ([], err, meta or {})
            expected = str(err)[:20]
        else:
            result = (devices if devices is not None else devices_norm(), "", {})
            count = len(result[0])
            expected = f"共 {count} 台设备"
        with patch("aeclone.license_server.software_devices",
                   return_value=result) as mocked:
            dlg = app._open_device_manager()
            wait_until(lambda: expected in texts([dlg]), timeout=5)
        return dlg, mocked


class DeviceListTests(DeviceManagerTestBase):
    def test_list_success_uses_saved_password(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            dlg, mocked = self.open_manager(app)
            self.assertIsNotNone(dlg)
            self.assertTrue(wait_until(lambda: "共 3 台设备" in texts([dlg])),
                            texts([dlg]))
            mocked.assert_called_once()
            args = mocked.call_args[0]
            self.assertEqual(args[0], SERVER)
            self.assertEqual(args[1], EMAIL)
            self.assertEqual(args[2], SAVED_PW)
            self.assertEqual(args[3], MACHINE)
            # 列表含机器码/状态/绑定时间/最近活跃
            body = texts([dlg])
            self.assertIn(MACHINE, body)
            self.assertIn("OTHER-MACHINE-2", body)
            self.assertIn("本机", body)
            self.assertIn("已绑定", body)
            self.assertIn("已解绑", body)
            self.assertIn("绑定：", body)
            self.assertIn("最近活跃：", body)
            self.assertIn("10.0.0.2", body)
            # 本机禁止解绑；仅非本机的已绑定设备有「解绑」按钮
            local_forbidden = find_button([dlg], "本机禁止解绑")
            self.assertIsNotNone(local_forbidden)
            self.assertTrue(bool(local_forbidden.disabled))
            unbind = [c for c in walk([dlg])
                      if getattr(c, "text", "") == "解绑" and hasattr(c, "on_click")]
            self.assertEqual(len(unbind), 1)
            self.assertIsNotNone(find_button([dlg], "在浏览器中管理"))

    def test_list_requires_password_when_not_saved(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg, saved_password="")
            dlg = app._open_device_manager()
            self.assertIsNotNone(dlg)
            pw_field = find_field([dlg], "账号密码")
            self.assertIsNotNone(pw_field)
            self.assertTrue(bool(pw_field.visible))
            with patch("aeclone.license_server.software_devices") as mocked:
                find_button([dlg], "刷新").on_click(None)
                self.assertTrue(wait_until(lambda: "请先输入账号密码" in texts([dlg])))
                mocked.assert_not_called()
            pw_field.value = "typed-pw"
            with patch("aeclone.license_server.software_devices",
                       return_value=(devices_norm(), "", {})) as mocked:
                find_button([dlg], "刷新").on_click(None)
                self.assertTrue(wait_until(lambda: "共 3 台设备" in texts([dlg])))
                self.assertEqual(mocked.call_args[0][2], "typed-pw")

    def test_password_error_shown(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg, saved_password="")
            dlg = app._open_device_manager()
            find_field([dlg], "账号密码").value = "bad-pw"
            with patch("aeclone.license_server.software_devices",
                       return_value=([], "邮箱或密码错误", {"error_kind": "credentials"})):
                find_button([dlg], "刷新").on_click(None)
                self.assertTrue(wait_until(
                    lambda: "邮箱或密码错误" in texts([dlg])))
            self.assertIsNotNone(app._device_dlg)

    def test_network_failure_shown(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            dlg, _ = self.open_manager(app)
            self.assertIsNotNone(dlg)
            with patch("aeclone.license_server.software_devices",
                       return_value=([], NETWORK_ERROR_MESSAGE,
                                     {"error_kind": "network"})):
                find_button([dlg], "刷新").on_click(None)
                self.assertTrue(wait_until(lambda: "网络不可达" in texts([dlg])))

    def test_unsupported_backend_hints_web(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            dlg, _ = self.open_manager(app)
            with patch("aeclone.license_server.software_devices",
                       return_value=([], UNSUPPORTED_MESSAGE,
                                     {"error_kind": "unsupported",
                                      "unsupported": True})):
                find_button([dlg], "刷新").on_click(None)
                self.assertTrue(wait_until(lambda: "网页端" in texts([dlg])))
            self.assertIsNotNone(find_button([dlg], "在浏览器中管理"))

    def test_browser_management_link(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            dlg, _ = self.open_manager(app)
            find_button([dlg], "在浏览器中管理").on_click(None)
            urls = list(getattr(app.page, "launched_urls", []) or [])
            self.assertTrue(any(u.endswith("/account/devices") for u in urls), urls)


class DeviceUnbindTests(DeviceManagerTestBase):
    def test_unbind_success_refreshes_list(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            calls = {}

            def fake_unbind(server, email, password, machine, device_id):
                calls.update(server=server, email=email, password=password,
                             machine=machine, device_id=device_id)
                return True, "", {"msg": "设备已解绑"}

            with patch("aeclone.license_server.software_devices",
                       return_value=(devices_norm(), "", {})) as listed, \
                    patch("aeclone.license_server.software_unbind_device",
                          side_effect=fake_unbind):
                dlg = app._open_device_manager()
                self.assertTrue(wait_until(lambda: "共 3 台设备" in texts([dlg])))
                unbind = find_button([dlg], "解绑")
                self.assertIsNotNone(unbind)
                unbind.on_click(None)
                self.assertIsNotNone(app._dlg)
                self.assertIn("OTHER-MACHINE-2", texts([app._dlg]))
                find_button([app._dlg], "确认解绑").on_click(None)
                self.assertTrue(wait_until(lambda: calls.get("device_id") == 2))
                self.assertEqual(calls["email"], EMAIL)
                self.assertEqual(calls["password"], SAVED_PW)
                self.assertEqual(calls["machine"], MACHINE)
                # 解绑成功后重新拉列表
                self.assertTrue(wait_until(lambda: listed.call_count >= 2))
                self.assertIn("已解绑", texts([dlg]))

    def test_local_device_has_no_unbind_button(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            dlg, _ = self.open_manager(app)
            self.assertTrue(wait_until(lambda: "共 3 台设备" in texts([dlg])))
            unbind = [c for c in walk([dlg])
                      if getattr(c, "text", "") == "解绑" and hasattr(c, "on_click")]
            # 只有 id=2 的设备可解绑；本机与已解绑设备都没有解绑按钮
            self.assertEqual(len(unbind), 1)
            local = find_button([dlg], "本机禁止解绑")
            self.assertTrue(bool(local.disabled))

    def test_unbind_failure_shown(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            dlg, _ = self.open_manager(app)
            with patch("aeclone.license_server.software_devices",
                       return_value=(devices_norm(), "", {})), \
                    patch("aeclone.license_server.software_unbind_device",
                          return_value=(False, "未找到该设备或该设备已解绑",
                                        {"error_kind": "device"})):
                find_button([dlg], "刷新").on_click(None)
                self.assertTrue(wait_until(lambda: "共 3 台设备" in texts([dlg])))
                find_button([dlg], "解绑").on_click(None)
                find_button([app._dlg], "确认解绑").on_click(None)
                self.assertTrue(wait_until(lambda: "未找到该设备" in texts([dlg])))


class DeviceManagerEntryTests(DeviceManagerTestBase):
    def test_settings_button_opens_in_app_dialog(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            app._switch(11)   # 设置页
            called = []
            app._open_device_manager = lambda: called.append(1)
            btn = find_button(app.content.controls, "设备管理")
            self.assertIsNotNone(btn)
            btn.on_click(None)
            self.assertEqual(called, [1])

    def test_account_center_button_opens_in_app_dialog(self):
        with sandbox(threads=True) as cfg:
            app = self.make_app(cfg)
            app._open_account_dialog()
            called = []
            app._open_device_manager = lambda: called.append(1)
            btn = find_button([app._dlg], "设备管理")
            self.assertIsNotNone(btn)
            btn.on_click(None)
            self.assertEqual(called, [1])


if __name__ == "__main__":
    unittest.main(verbosity=2)

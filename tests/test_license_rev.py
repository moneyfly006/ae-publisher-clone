"""心跳 license_rev 变化 → 立即刷新授权 + 锁定页刷新按钮（task-24）。

覆盖：
- 心跳响应 dict 解析 license_rev（旧服务端无字段兼容）；
- App 记录 rev 基线（登录/刷新更新）；rev 变化触发一次 _refresh_platform_license；
  同一 rev 不重复触发；刷新失败保留旧 rev 下次重试；rev 变空=权限撤销；
- 锁定页「刷新授权（已开通/刚购买点这里）」按钮：loading→刷新→解锁/toast；
- 周期刷新默认 300s（clamp 120~1800）在 test_relogin_after_unbind 覆盖。
所有网络 mock，不真连网。
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import flet as ft  # noqa: E402
from ui_sandbox import build_app, sandbox, walk  # noqa: E402
from aeclone.license_server import (  # noqa: E402
    fetch_entitlement_token, refresh_entitlements)
from aeclone.licensing import Entitlements, issue_token  # noqa: E402

MACHINE = "UI-VALIDATION-ONLY"
EMAIL = "user@example.com"


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
        self.calls.append((method, url))
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


def find_button(controls, label):
    for control in walk(controls):
        if hasattr(control, "text") and hasattr(control, "on_click"):
            if label in str(getattr(control, "text", "") or ""):
                return control
    return None


def texts(controls) -> str:
    return "\n".join(str(c.value) for c in walk(controls)
                     if isinstance(c, ft.Text) and c.value)


# ---------------------------------------------------------------- API 层
class RefreshStatusRevTests(unittest.TestCase):
    def _cfg(self, token=""):
        return Cfg(license_server="https://lic.test",
                   license_account_email=EMAIL,
                   license_token=token,
                   license_token_ts=int(time.time()) if token else 0,
                   license_platforms="")

    def test_fetch_meta_contains_license_rev(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2099-01-01")
        body = {"code": 0, "data": {"ok": True, "token": tok,
                                    "license_rev": "abcdef0123456789"}}
        sess = _Sess([_Resp(200, body)])
        meta = {}
        got, err = fetch_entitlement_token("https://lic.test", MACHINE,
                                           session=sess, meta=meta)
        self.assertEqual(got, tok)
        self.assertEqual(err, "")
        self.assertTrue(meta["license_rev_present"])
        self.assertEqual(meta["license_rev"], "abcdef0123456789")

    def test_refresh_ok_status_has_rev(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2099-01-01")
        body = {"code": 0, "data": {"ok": True, "token": tok,
                                    "license_rev": "rev-ok"}}
        status = {}
        ent = refresh_entitlements(self._cfg(), machine=MACHINE, status=status,
                                   session=_Sess([_Resp(200, body)]))
        self.assertTrue(ent.has_platform("alibaba"))
        self.assertEqual(status["result"], "ok")
        self.assertTrue(status["license_rev_present"])
        self.assertEqual(status["license_rev"], "rev-ok")

    def test_refresh_unbound_status(self):
        body = {"code": 0, "data": {"token": "", "bound": False,
                                    "license_rev": ""}}
        status = {}
        cfg = self._cfg(token=issue_token(MACHINE, {"alibaba": ["listing"]},
                                          "2099-01-01"))
        ent = refresh_entitlements(cfg, machine=MACHINE, status=status,
                                   session=_Sess([_Resp(200, body)]))
        self.assertEqual(status["result"], "unbound")
        self.assertTrue(status["license_rev_present"])
        self.assertEqual(status["license_rev"], "")
        self.assertEqual(cfg.license_token, "")
        self.assertTrue(ent.expired)

    def test_refresh_no_entitlement_clears_token(self):
        """新服务端明确返回 token="" + license_rev="" → 无任何权限，清 token 锁定。"""
        cached = issue_token(MACHINE, {"alibaba": ["listing"]}, "2099-01-01")
        cfg = self._cfg(token=cached)
        status = {}
        body = {"code": 0, "data": {"ok": True, "token": "",
                                    "license_rev": ""}}
        ent = refresh_entitlements(cfg, machine=MACHINE, status=status,
                                   session=_Sess([_Resp(200, body)]))
        self.assertEqual(status["result"], "no-entitlement")
        self.assertEqual(cfg.license_token, "")
        self.assertEqual(ent.platforms_list(), [])

    def test_old_server_empty_token_keeps_cache(self):
        """旧服务端无 license_rev：空 token 仍按原逻辑保留本地缓存（7 天宽限）。"""
        cached = issue_token(MACHINE, {"alibaba": ["listing"]}, "2099-01-01")
        cfg = self._cfg(token=cached)
        status = {}
        body = {"code": 0, "msg": "ok", "data": {"token": ""}}
        ent = refresh_entitlements(cfg, machine=MACHINE, status=status,
                                   session=_Sess([_Resp(200, body)]))
        self.assertEqual(status["result"], "network")
        self.assertEqual(cfg.license_token, cached)
        self.assertTrue(ent.has_platform("alibaba"))

    def test_refresh_network_status_keeps_token(self):
        cached = issue_token(MACHINE, {"alibaba": ["listing"]}, "2099-01-01")
        cfg = self._cfg(token=cached)
        status = {}
        sess = _Sess(raise_exc=requests.ConnectionError("boom"))
        ent = refresh_entitlements(cfg, machine=MACHINE, status=status,
                                   session=sess)
        self.assertEqual(status["result"], "network")
        self.assertFalse(status["license_rev_present"])
        self.assertEqual(cfg.license_token, cached)
        self.assertTrue(ent.has_platform("alibaba"))


# ---------------------------------------------------------------- App 层
class HeartbeatRevTests(unittest.TestCase):
    def _app(self, cfg):
        return build_app()

    def _fake_refresh(self, app, calls, *, result="ok", rev="", present=True,
                      extra=None):
        def fake(status_out=None, **kw):
            calls.append(1)
            if status_out is not None:
                status_out.update({"result": result, "license_rev": rev,
                                   "license_rev_present": present})
            if extra:
                extra()
        return fake

    def test_rev_change_triggers_refresh_once(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._license_rev, app._license_rev_known = "revA", True
            calls = []
            app._refresh_platform_license = self._fake_refresh(
                app, calls, result="ok", rev="revB")
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": "revB"})
            self.assertTrue(wait_until(lambda: app._license_rev == "revB"))
            self.assertEqual(len(calls), 1)
            # 同一 rev 再来心跳：不重复刷新
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": "revB"})
            time.sleep(0.15)
            self.assertEqual(len(calls), 1)

    def test_same_rev_does_not_refresh(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._license_rev, app._license_rev_known = "revA", True
            calls = []
            app._refresh_platform_license = self._fake_refresh(app, calls)
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": "revA"})
            time.sleep(0.15)
            self.assertEqual(calls, [])

    def test_missing_field_is_ignored(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._license_rev, app._license_rev_known = "revA", True
            calls = []
            app._refresh_platform_license = self._fake_refresh(app, calls)
            app._on_heartbeat_result({"ok": True, "license_rev_present": False})
            app._on_heartbeat_result({"ok": True})
            time.sleep(0.15)
            self.assertEqual(calls, [])
            self.assertEqual(app._license_rev, "revA")

    def test_unknown_baseline_with_rev_triggers_and_records(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._license_rev, app._license_rev_known = "", False
            calls = []
            app._refresh_platform_license = self._fake_refresh(
                app, calls, result="ok", rev="rev-new")
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": "rev-new"})
            self.assertTrue(wait_until(lambda: app._license_rev == "rev-new"))
            self.assertTrue(app._license_rev_known)
            self.assertEqual(len(calls), 1)

    def test_unknown_baseline_empty_rev_no_refresh(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._license_rev, app._license_rev_known = "", False
            calls = []
            app._refresh_platform_license = self._fake_refresh(app, calls)
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": ""})
            time.sleep(0.15)
            self.assertEqual(calls, [])
            self.assertTrue(app._license_rev_known)
            self.assertEqual(app._license_rev, "")

    def test_failure_keeps_old_rev_and_retries(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._license_rev, app._license_rev_known = "revA", True
            calls = []
            app._refresh_platform_license = self._fake_refresh(
                app, calls, result="network", present=False)
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": "revB"})
            self.assertTrue(wait_until(lambda: len(calls) == 1))
            time.sleep(0.1)
            self.assertEqual(app._license_rev, "revA")      # 失败保留旧 rev
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": "revB"})
            self.assertTrue(wait_until(lambda: len(calls) == 2))  # 下次重试

    def test_empty_rev_with_permissions_triggers_revocation_refresh(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._license_rev, app._license_rev_known = "revA", True
            calls = []
            app._refresh_platform_license = self._fake_refresh(
                app, calls, result="no-entitlement", rev="", present=True)
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": ""})
            self.assertTrue(wait_until(lambda: app._license_rev == ""
                                       and len(calls) == 1))
            # 基线已同步为 ""：同样的心跳不再重复刷新
            app._on_heartbeat_result({"ok": True, "license_rev_present": True,
                                      "license_rev": ""})
            time.sleep(0.15)
            self.assertEqual(len(calls), 1)

    def test_login_baseline_from_software_login_data(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2099-01-01")
            app._apply_account_token(tok, EMAIL, False, True, "",
                                     login_data={"license_rev": "rev-login"})
            self.assertTrue(app._license_rev_known)
            self.assertEqual(app._license_rev, "rev-login")

    def test_note_rev_from_refresh_status(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._note_license_rev_from_status(
                {"result": "ok", "license_rev": "revX",
                 "license_rev_present": True})
            self.assertEqual(app._license_rev, "revX")
            app._note_license_rev_from_status(
                {"result": "network", "license_rev": "revY",
                 "license_rev_present": True})
            self.assertEqual(app._license_rev, "revX")      # 失败不更新

    def test_start_heartbeat_passes_on_result_callback(self):
        with sandbox(threads=True) as cfg:
            app = self._app(cfg)
            app._start_license_watch = lambda: None
            app._heartbeat = None
            cfg.set("license_account_email", EMAIL)
            with patch.dict("os.environ", {"AURORA_TEST": "",
                                           "AURORA_NO_SAVE": ""}), \
                    patch("aeclone.ui.app.HeartbeatLoop") as loop_cls:
                app._start_heartbeat()
            loop_cls.assert_called_once()
            kwargs = loop_cls.call_args.kwargs
            self.assertTrue(callable(kwargs.get("on_result")))
            loop_cls.return_value.start.assert_called_once()


class LockedPageRefreshTests(unittest.TestCase):
    def _locked_app(self, cfg):
        cfg.set("license_platforms", "")
        cfg.set("license_token", issue_token(MACHINE, {"alibaba": ["listing"]},
                                             "2099-01-01"))
        cfg.set("license_token_ts", int(time.time()))
        cfg.set("license_account_email", EMAIL)
        cfg.set("license_auto_login", True)
        app = build_app()
        app._switch_platform("amazon")      # amazon 未开通 → 锁定页
        return app

    def test_refresh_button_refreshes_and_unlocks(self):
        with sandbox(threads=True) as cfg:
            app = self._locked_app(cfg)
            btn = find_button(app.content.controls, "刷新授权（已开通/刚购买点这里）")
            self.assertIsNotNone(btn)
            calls = []

            def fake_check():
                calls.append(1)
                time.sleep(0.05)
                app.entitlements = Entitlements(
                    platforms={"amazon": {"listing"}}, source="token")

            app._check_license_inner = fake_check
            btn.on_click(None)
            self.assertTrue(bool(btn.disabled), "刷新中按钮应禁用")
            self.assertIn("刷新中", str(getattr(btn, "text", "")))
            self.assertTrue(wait_until(lambda: len(calls) == 1))
            self.assertTrue(wait_until(lambda: not bool(btn.disabled)))
            self.assertTrue(app.entitlements.has_platform("amazon"))
            self.assertEqual(str(getattr(btn, "text", "")),
                             "刷新授权（已开通/刚购买点这里）")

    def test_refresh_button_keeps_lock_when_still_unlicensed(self):
        with sandbox(threads=True) as cfg:
            app = self._locked_app(cfg)
            btn = find_button(app.content.controls, "刷新授权（已开通/刚购买点这里）")
            calls = []
            app._check_license_inner = lambda: calls.append(1)
            btn.on_click(None)
            self.assertTrue(wait_until(lambda: len(calls) == 1))
            self.assertTrue(wait_until(lambda: not bool(btn.disabled)))
            self.assertFalse(app.entitlements.has_platform("amazon"))
            # 仍显示锁定按钮（未解锁）
            self.assertIn("未开通", texts(app.content.controls))


if __name__ == "__main__":
    unittest.main(verbosity=2)

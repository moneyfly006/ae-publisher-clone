"""授权服务器接口（账号制 v3）：software_login / token 刷新 / 错误分类。

契约 §7.1：
- software_login：{email,password,machine_code,version} → data{token,user,...}
  失败 code=1 + 中文 msg（密码错 / 禁用 / 设备冲突 / 超限）。
- entitlement：{machine_code} → {token}（登录成功后刷新 token 用）。
所有 HTTP 都用假 session，不真连网。
"""
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone import license_server  # noqa: E402
from aeclone.license import machine_code as real_machine_code  # noqa: E402
from aeclone.license_server import (  # noqa: E402
    LOGIN_ERROR_KINDS, NETWORK_ERROR_MESSAGE, UNSUPPORTED_MESSAGE,
    classify_login_error, fetch_entitlement_token, normalize_device,
    refresh_entitlements, software_devices, software_login,
    software_unbind_device)
from aeclone.licensing import issue_token, load_entitlements  # noqa: E402

MACHINE = "ABCD1234-EF567890"


class Cfg(dict):
    def __getattr__(self, k):
        return self.get(k, "")

    def set(self, k, v):
        self[k] = v


class _Resp:
    def __init__(self, status=200, data=None, headers=None, text=""):
        self.status_code = status
        self._data = data if data is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._data


class _Sess:
    """假 session：request 依次返回预设响应（或抛异常）。"""

    def __init__(self, responses=None, raise_exc=None):
        self.responses = list(responses or [])
        self.raise_exc = raise_exc
        self.calls = []

    def request(self, method, url, timeout=None, allow_redirects=True, **kw):
        self.calls.append({"method": method, "url": url, "json": kw.get("json")})
        if self.raise_exc is not None:
            raise self.raise_exc
        if not self.responses:
            return _Resp(404, {})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _login_ok(token, email="user@example.com"):
    return _Resp(200, {"code": 0, "msg": "ok",
                       "data": {"token": token,
                                "user": {"id": 1, "email": email, "name": "U"},
                                "machine_code": MACHINE,
                                "platforms": ["alibaba"],
                                "expires_at": "2027-11-17",
                                "device_limit": 2}})


class CanonicalUrlTests(unittest.TestCase):
    def test_canonical_server_maps_legacy_alias(self):
        from aeclone.license import canonical_server, site_url
        self.assertEqual(canonical_server("https://ali.moneyfly.club/license"),
                         "https://license.moneyfly.club")
        self.assertEqual(canonical_server("https://ali.moneyfly.club/license/"),
                         "https://license.moneyfly.club")
        self.assertEqual(canonical_server("https://license.moneyfly.club/"),
                         "https://license.moneyfly.club")
        self.assertEqual(site_url("https://lic.test"), "https://lic.test")

    def test_site_links(self):
        from aeclone.license import (devices_url, forgot_url, pricing_url,
                                     register_url)
        self.assertEqual(register_url("https://lic.test"), "https://lic.test/register")
        self.assertEqual(forgot_url("https://lic.test"), "https://lic.test/forgot")
        self.assertEqual(devices_url("https://lic.test"),
                         "https://lic.test/account/devices")
        url = pricing_url("https://lic.test", platform="amazon",
                          machine=MACHINE, email="a@b.c")
        parts = urlsplit(url)
        self.assertEqual(parts.path, "/pricing")
        q = parse_qs(parts.query)
        self.assertEqual(q["platform"], ["amazon"])
        self.assertEqual(q["machine"], [MACHINE])
        self.assertEqual(q["email"], ["a@b.c"])


class ClassifyErrorTests(unittest.TestCase):
    def test_credentials(self):
        kind, msg = classify_login_error("邮箱或密码错误")
        self.assertEqual(kind, "credentials")
        self.assertIn("邮箱或密码", msg)

    def test_device_conflict(self):
        kind, msg = classify_login_error("该设备已绑定其他账号，请联系客服解绑后再登录")
        self.assertEqual(kind, "device_conflict")
        self.assertIn("已绑定其他账号", msg)

    def test_device_limit(self):
        kind, msg = classify_login_error("设备数量已达上限（2 台），请先在账号中心解绑其他设备")
        self.assertEqual(kind, "device_limit")
        self.assertIn("上限", msg)

    def test_disabled(self):
        kind, msg = classify_login_error("账号已被禁用，请联系客服")
        self.assertEqual(kind, "disabled")
        self.assertIn("禁用", msg)

    def test_network_and_default(self):
        self.assertEqual(classify_login_error(NETWORK_ERROR_MESSAGE)[0], "network")
        self.assertEqual(classify_login_error("")[0], "server")
        for kind in LOGIN_ERROR_KINDS.values():
            self.assertTrue(kind)


class SoftwareLoginTests(unittest.TestCase):
    def test_success_returns_token_and_user(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        sess = _Sess([_login_ok(tok)])
        got, err, data = software_login("https://lic.test", "User@Example.com ",
                                        "pw", MACHINE, version="1.1.0", session=sess)
        self.assertEqual(got, tok)
        self.assertEqual(err, "")
        self.assertEqual(data["user"]["email"], "user@example.com")
        self.assertEqual(data["device_limit"], 2)
        sent = sess.calls[0]["json"]
        self.assertEqual(sent["machine_code"], MACHINE)
        self.assertEqual(sent["email"], "user@example.com")   # 邮箱小写去空格
        self.assertEqual(sent["version"], "1.1.0")
        self.assertIn("software_login", sess.calls[0]["url"])

    def test_success_reports_device_env_for_backend_devices(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        sess = _Sess([_login_ok(tok)])
        software_login("https://lic.test", "a@b.c", "pw", MACHINE,
                       os_version="Windows 11", machine_name="DESKTOP-AL",
                       store_name="Aurora Store", session=sess)
        sent = sess.calls[0]["json"]
        self.assertEqual(sent["os"], "Windows 11")
        self.assertEqual(sent["machine_name"], "DESKTOP-AL")
        self.assertEqual(sent["store_name"], "Aurora Store")

    def test_password_error(self):
        sess = _Sess([_Resp(200, {"code": 1, "msg": "邮箱或密码错误"})])
        got, err, data = software_login("https://lic.test", "a@b.c", "bad",
                                        MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertIn("邮箱或密码错误", err)
        self.assertEqual(data.get("error_kind"), "credentials")

    def test_device_conflict(self):
        sess = _Sess([_Resp(200, {"code": 1,
                                  "msg": "该设备已绑定其他账号，请联系客服解绑后再登录"})])
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertEqual(data.get("error_kind"), "device_conflict")
        self.assertIn("已绑定其他账号", err)

    def test_device_limit(self):
        sess = _Sess([_Resp(200, {"code": 1,
                                  "msg": "设备数量已达上限（2 台），请先在账号中心解绑其他设备"})])
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(data.get("error_kind"), "device_limit")
        self.assertIn("上限", err)

    def test_disabled(self):
        sess = _Sess([_Resp(200, {"code": 1, "msg": "账号已被禁用，请联系客服"})])
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(data.get("error_kind"), "disabled")
        self.assertIn("禁用", err)

    def test_network_unreachable(self):
        sess = _Sess(raise_exc=requests.ConnectionError("boom"))
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertEqual(err, NETWORK_ERROR_MESSAGE)
        self.assertEqual(data.get("error_kind"), "network")
        self.assertIn("网络", err)

    def test_bad_response_body(self):
        class _BadJson(_Resp):
            def json(self):
                raise ValueError("bad json")
        sess = _Sess([_BadJson(200)])
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertEqual(data.get("error_kind"), "server")
        self.assertTrue(err)

    def test_token_machine_mismatch_rejected(self):
        tok = issue_token("OTHER-MACHINE", {"alibaba": ["listing"]}, "2027-11-17")
        sess = _Sess([_login_ok(tok)])
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertEqual(data.get("error_kind"), "machine")
        self.assertIn("不匹配", err)

    def test_token_signature_invalid_rejected(self):
        bad = '{"payload":{"machine_code":"%s","platforms":{}},"sig":"00"}' % MACHINE
        sess = _Sess([_login_ok(bad)])
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertEqual(data.get("error_kind"), "token")

    def test_missing_token_in_response(self):
        sess = _Sess([_Resp(200, {"code": 0, "data": {}})])
        got, err, data = software_login("https://lic.test", "a@b.c", "pw",
                                        MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertEqual(data.get("error_kind"), "server")

    def test_redirect_preserves_action_query(self):
        """旧域名 308 跳转丢 query 会让服务端报「未知操作」；客户端应补回 action。"""
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        sess = _Sess([
            _Resp(308, {}, {"location": "https://license.moneyfly.club/shop/api.php"}),
            _login_ok(tok),
        ])
        got, err, _ = software_login("https://lic.test", "a@b.c", "pw",
                                     MACHINE, session=sess)
        self.assertEqual(got, tok)
        final_url = sess.calls[-1]["url"]
        self.assertTrue(final_url.endswith("?action=software_login"),
                        f"最终 URL 丢了 action：{final_url}")

    def test_empty_email_or_password(self):
        got, err, data = software_login("https://lic.test", "", "", MACHINE)
        self.assertEqual(got, "")
        self.assertEqual(data.get("error_kind"), "credentials")


class FetchAndRefreshTests(unittest.TestCase):
    def test_fetch_empty_token_has_actionable_message(self):
        sess = _Sess([_Resp(200, {"code": 0, "msg": "ok", "data": {"token": ""}})] * 8)
        got, err = fetch_entitlement_token("https://lic.test", MACHINE, session=sess)
        self.assertEqual(got, "")
        self.assertIn("未绑定授权账号", err)
        self.assertIn("购买套餐", err)

    def test_fetch_returns_token(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        sess = _Sess([_Resp(200, {"code": 0, "data": {"token": tok}})])
        got, err = fetch_entitlement_token("https://lic.test", MACHINE, session=sess)
        self.assertEqual(got, tok)
        self.assertEqual(err, "")

    def test_fetch_failure(self):
        got, err = fetch_entitlement_token("https://lic.test", MACHINE,
                                           session=_Sess())
        self.assertEqual(got, "")
        self.assertTrue(err)

    def test_refresh_applies_matching_token(self):
        tok = issue_token(real_machine_code(), {"alibaba": ["listing", "ai_image"]},
                          "2027-11-17")
        cfg = Cfg(license_server="https://lic.test", license_token="",
                  license_token_ts=0, license_platforms="",
                  license_account_email="user@example.com")
        saved = []
        ent = refresh_entitlements(cfg, version="1.1.0", save=lambda c: saved.append(1),
                                   session=_Sess([_Resp(200, {"code": 0,
                                                              "data": {"token": tok}})]))
        self.assertTrue(ent.has_feature("alibaba", "ai_image"))
        self.assertEqual(cfg.license_token, tok)
        self.assertTrue(saved)
        self.assertTrue(load_entitlements(cfg).has_platform("alibaba"))

    def test_refresh_keeps_cache_on_network_failure(self):
        cached = issue_token(real_machine_code(), {"alibaba": ["listing"]}, "2027-11-17")
        cfg = Cfg(license_server="https://lic.test", license_token=cached,
                  license_token_ts=time.time(), license_platforms="",
                  license_account_email="user@example.com")
        ent = refresh_entitlements(cfg, session=_Sess())
        self.assertTrue(ent.has_platform("alibaba"))
        self.assertEqual(cfg.license_token, cached)

    def test_refresh_skips_network_when_not_logged_in(self):
        sess = _Sess([_Resp(200, {"code": 0, "data": {"token": "x"}})])
        cfg = Cfg(license_server="https://lic.test", license_token="",
                  license_token_ts=0, license_platforms="", license_account_email="")
        refresh_entitlements(cfg, session=sess)
        self.assertEqual(sess.calls, [])

    def test_refresh_ignores_mismatched_token(self):
        tok = issue_token("OTHER-MACHINE", {"alibaba": ["listing"]}, "2027-11-17")
        cfg = Cfg(license_server="https://lic.test", license_token="",
                  license_token_ts=0, license_platforms="",
                  license_account_email="user@example.com")
        ent = refresh_entitlements(cfg, session=_Sess([_Resp(200, {"code": 0,
                                                                  "data": {"token": tok}})]))
        self.assertEqual(cfg.license_token, "")
        self.assertEqual(ent.platforms_list(), [])


class DeviceApiTests(unittest.TestCase):
    """software_devices / software_unbind（无会话，凭邮箱+密码+机器码）。"""

    def test_normalize_device_marks_current_and_status(self):
        row = {"id": 7, "machine_code": MACHINE.lower(), "active": 1,
               "created_at": 1700000000, "last_seen": 1710000000,
               "last_ip": "1.2.3.4", "app_version": "1.1.0"}
        dev = normalize_device(row, MACHINE)
        self.assertEqual(dev["id"], 7)
        self.assertTrue(dev["is_current"])
        self.assertTrue(dev["active"])
        self.assertEqual(dev["status"], "已绑定")
        self.assertEqual(dev["created_at"], 1700000000)
        old = normalize_device({"id": 8, "machine_code": "OTHER", "active": 0}, MACHINE)
        self.assertFalse(old["is_current"])
        self.assertEqual(old["status"], "已解绑")
        self.assertEqual(old["last_ip"], "")

    def test_list_success_returns_normalized_devices(self):
        sess = _Sess([_Resp(200, {"code": 0, "msg": "ok", "data": [
            {"id": 1, "machine_code": MACHINE, "active": 1,
             "created_at": 1700000000, "last_seen": 1710000000},
            {"id": 2, "machine_code": "OTHER-MACHINE-2", "active": 1},
        ]})])
        devices, err, meta = software_devices("https://lic.test", "User@Example.com ",
                                              "pw", MACHINE, session=sess)
        self.assertEqual(err, "")
        self.assertEqual(len(devices), 2)
        self.assertTrue(devices[0]["is_current"])
        self.assertFalse(devices[1]["is_current"])
        self.assertEqual(meta["count"], 2)
        sent = sess.calls[0]["json"]
        self.assertEqual(sent["email"], "user@example.com")
        self.assertEqual(sent["password"], "pw")
        self.assertEqual(sent["machine_code"], MACHINE)
        self.assertIn("software_devices", sess.calls[0]["url"])

    def test_list_accepts_devices_dict_shape(self):
        sess = _Sess([_Resp(200, {"code": 0, "data": {"devices": [
            {"id": 3, "machine_code": "X-MACHINE", "active": 1}]}})])
        devices, err, _ = software_devices("https://lic.test", "a@b.c", "pw",
                                           MACHINE, session=sess)
        self.assertEqual(err, "")
        self.assertEqual(devices[0]["machine_code"], "X-MACHINE")

    def test_list_password_error(self):
        sess = _Sess([_Resp(200, {"code": 1, "msg": "邮箱或密码错误"})])
        devices, err, meta = software_devices("https://lic.test", "a@b.c", "bad",
                                              MACHINE, session=sess)
        self.assertEqual(devices, [])
        self.assertIn("邮箱或密码错误", err)
        self.assertEqual(meta["error_kind"], "credentials")

    def test_list_network_error(self):
        sess = _Sess(raise_exc=requests.ConnectionError("boom"))
        devices, err, meta = software_devices("https://lic.test", "a@b.c", "pw",
                                              MACHINE, session=sess)
        self.assertEqual(devices, [])
        self.assertEqual(err, NETWORK_ERROR_MESSAGE)
        self.assertEqual(meta["error_kind"], "network")

    def test_list_unknown_action_backend_unsupported(self):
        sess = _Sess([_Resp(200, {"code": 1, "msg": "未知操作"})])
        devices, err, meta = software_devices("https://lic.test", "a@b.c", "pw",
                                              MACHINE, session=sess)
        self.assertEqual(devices, [])
        self.assertEqual(err, UNSUPPORTED_MESSAGE)
        self.assertEqual(meta["error_kind"], "unsupported")
        self.assertTrue(meta["unsupported"])

    def test_list_requires_password_locally(self):
        sess = _Sess()
        devices, err, meta = software_devices("https://lic.test", "a@b.c", "",
                                              MACHINE, session=sess)
        self.assertEqual(devices, [])
        self.assertTrue(err)
        self.assertEqual(meta["error_kind"], "credentials")
        self.assertEqual(sess.calls, [])

    def test_unbind_success_body_and_result(self):
        # 后端契约：成功 data = 剩余仍绑定（active=1）的设备数组；无 data.ok
        sess = _Sess([_Resp(200, {"code": 0, "msg": "设备已解绑",
                                  "data": [{"id": 1, "machine_code": MACHINE,
                                            "active": 1},
                                           {"id": 3, "machine_code": "THIRD",
                                            "active": 1}]})])
        ok, err, meta = software_unbind_device("https://lic.test", "A@B.C", "pw",
                                               MACHINE, 12, session=sess)
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertEqual(meta["msg"], "设备已解绑")
        self.assertEqual(len(meta["devices"]), 2)
        self.assertTrue(meta["devices"][0]["is_current"])
        sent = sess.calls[0]["json"]
        self.assertEqual(sent["device_id"], 12)
        self.assertEqual(sent["email"], "a@b.c")
        self.assertEqual(sent["machine_code"], MACHINE)
        self.assertIn("software_unbind", sess.calls[0]["url"])

    def test_unbind_local_device_rejected_by_server(self):
        sess = _Sess([_Resp(200, {"code": 1,
                                  "msg": "不能解绑当前登录设备，请在网页端操作"})])
        ok, err, meta = software_unbind_device("https://lic.test", "a@b.c", "pw",
                                               MACHINE, 1, session=sess)
        self.assertFalse(ok)
        self.assertIn("当前登录设备", err)
        self.assertEqual(meta["error_kind"], "device_local")

    def test_unbind_device_not_found(self):
        sess = _Sess([_Resp(200, {"code": 1, "msg": "设备不存在或不属于当前账号"})])
        ok, err, meta = software_unbind_device("https://lic.test", "a@b.c", "pw",
                                               MACHINE, 99, session=sess)
        self.assertFalse(ok)
        self.assertIn("设备不存在", err)
        self.assertEqual(meta["error_kind"], "device")

    def test_unbind_rejects_bad_device_id_locally(self):
        sess = _Sess()
        ok, err, _ = software_unbind_device("https://lic.test", "a@b.c", "pw",
                                            MACHINE, 0, session=sess)
        self.assertFalse(ok)
        self.assertTrue(err)
        self.assertEqual(sess.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

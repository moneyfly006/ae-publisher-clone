"""网站 ↔ 客户端对接：授权状态一致性（解绑/禁用/断网）单元测试。

覆盖 v3 契约补充行为：
- 服务端明确返回「设备已解绑」或「账号被禁用」时，客户端立即清本地 token（后台操作即时生效）；
- 纯网络失败/服务器异常时保留 7 天离线宽限（不清 token）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import time
import unittest
from unittest import mock

from aeclone import license_server, licensing


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class _Cfg:
    def __init__(self, **kw):
        self.d = {
            "license_server": "https://license.example.test",
            "license_account_email": "user@example.test",
            "license_token": "",
            "license_token_ts": 0,
            "license_platforms": "",
        }
        self.d.update(kw)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def set(self, k, v):
        self.d[k] = v

    def __getattr__(self, k):
        return self.d.get(k)


MACHINE = "ABCD1234-EF567890"


def _signed_token(machine=MACHINE, platforms=None, expires="2030-01-01"):
    return licensing.issue_token(machine, platforms or {"alibaba": ["listing"]}, expires)


def _unbound_response(*a, **kw):
    return _Resp({"code": 0, "msg": "ok",
                  "data": {"token": "", "bound": False, "msg": "该设备未绑定账号"}})


def _disabled_response(*a, **kw):
    return _Resp({"code": 0, "msg": "ok",
                  "data": {"token": "", "bound": True, "disabled": True,
                           "msg": "账号已被禁用，请联系客服"}})


class ExplicitStateTests(unittest.TestCase):
    def test_unbound_marker_detected(self):
        with mock.patch.object(license_server, "_request_keep_query", _unbound_response):
            tok, err = license_server.fetch_entitlement_token(
                "https://license.example.test", MACHINE)
        self.assertEqual(tok, "")
        self.assertTrue(err.startswith("UNBOUND:"), err)

    def test_disabled_marker_detected(self):
        with mock.patch.object(license_server, "_request_keep_query", _disabled_response):
            tok, err = license_server.fetch_entitlement_token(
                "https://license.example.test", MACHINE)
        self.assertEqual(tok, "")
        self.assertTrue(err.startswith("DISABLED:"), err)

    def test_unbound_clears_cached_token_immediately(self):
        cfg = _Cfg(license_token=_signed_token(), license_token_ts=int(time.time()))
        self.assertTrue(licensing.load_entitlements(cfg, machine=MACHINE).platforms_list())
        with mock.patch.object(license_server, "_request_keep_query", _unbound_response):
            ent = license_server.refresh_entitlements(cfg, save=None, machine=MACHINE)
        self.assertEqual(cfg.get("license_token"), "")
        self.assertEqual(cfg.get("license_token_ts"), 0)
        self.assertEqual(ent.platforms_list(), [])

    def test_disabled_clears_cached_token_immediately(self):
        cfg = _Cfg(license_token=_signed_token(), license_token_ts=int(time.time()))
        with mock.patch.object(license_server, "_request_keep_query", _disabled_response):
            ent = license_server.refresh_entitlements(cfg, save=None, machine=MACHINE)
        self.assertEqual(cfg.get("license_token"), "")
        self.assertEqual(ent.platforms_list(), [])

    def test_network_failure_keeps_offline_grace(self):
        cfg = _Cfg(license_token=_signed_token(), license_token_ts=int(time.time()))

        def boom(*a, **kw):
            raise RuntimeError("network down")

        with mock.patch.object(license_server, "_request_keep_query", boom):
            ent = license_server.refresh_entitlements(cfg, save=None, machine=MACHINE)
        # 网络失败不应清 token：保留缓存供离线使用（7 天宽限）
        self.assertEqual(cfg.get("license_token"), _signed_token())
        self.assertTrue(ent.has_platform("alibaba"))


if __name__ == "__main__":
    unittest.main()

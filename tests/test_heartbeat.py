"""心跳上报：send_heartbeat 返回响应 dict + license_rev 提取 + 回调（task-24）。"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.heartbeat import (  # noqa: E402
    HeartbeatLoop, _extract_license_rev, send_heartbeat)


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return self._payload


class SendHeartbeatTests(unittest.TestCase):
    def test_disabled_without_server_or_machine(self):
        self.assertFalse(send_heartbeat("", "M", {"platforms": {}})["ok"])
        self.assertFalse(send_heartbeat("https://x", "", {"platforms": {}})["ok"])

    def test_response_with_license_rev_in_data(self):
        body = {"code": 0, "msg": "ok",
                "data": {"ok": True, "license_rev": "50f4f84a1b2c3d4e"}}
        with patch("aeclone.services.heartbeat.requests.post",
                   return_value=_Resp(200, body)):
            res = send_heartbeat("https://lic.test", "M", {"platforms": {}})
        self.assertTrue(res["ok"])
        self.assertTrue(res["license_rev_present"])
        self.assertEqual(res["license_rev"], "50f4f84a1b2c3d4e")
        self.assertEqual(res["code"], 0)

    def test_old_server_without_license_rev_is_compatible(self):
        body = {"code": 0, "msg": "ok"}
        with patch("aeclone.services.heartbeat.requests.post",
                   return_value=_Resp(200, body)):
            res = send_heartbeat("https://lic.test", "M", {"platforms": {}})
        self.assertTrue(res["ok"])
        self.assertFalse(res["license_rev_present"])
        self.assertEqual(res["license_rev"], "")

    def test_empty_rev_present_means_no_permissions(self):
        body = {"code": 0, "data": {"ok": True, "license_rev": ""}}
        with patch("aeclone.services.heartbeat.requests.post",
                   return_value=_Resp(200, body)):
            res = send_heartbeat("https://lic.test", "M", {})
        self.assertTrue(res["license_rev_present"])
        self.assertEqual(res["license_rev"], "")

    def test_http_or_code_failure(self):
        with patch("aeclone.services.heartbeat.requests.post",
                   return_value=_Resp(500, {"code": 1, "msg": "boom"})):
            res = send_heartbeat("https://lic.test", "M", {})
        self.assertFalse(res["ok"])
        self.assertTrue(res["error"])
        with patch("aeclone.services.heartbeat.requests.post",
                   side_effect=RuntimeError("network down")):
            res2 = send_heartbeat("https://lic.test", "M", {})
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["license_rev"], "")

    def test_extract_license_rev_variants(self):
        self.assertEqual(_extract_license_rev({"data": {"license_rev": "a1"}}),
                         ("a1", True))
        self.assertEqual(_extract_license_rev({"license_rev": "b2"}), ("b2", True))
        self.assertEqual(_extract_license_rev({"code": 0}), ("", False))
        self.assertEqual(_extract_license_rev({"data": {"license_rev": ""}}),
                         ("", True))


class HeartbeatLoopCallbackTests(unittest.TestCase):
    def test_loop_calls_on_result(self):
        seen = []
        body = {"code": 0, "data": {"ok": True, "license_rev": "rev-1"}}

        def build():
            return "https://lic.test", "M", {"platforms": {}}

        loop = HeartbeatLoop(build, interval=30, on_result=seen.append)
        with patch("aeclone.services.heartbeat.send_heartbeat",
                   return_value={"ok": True, "license_rev_present": True,
                                 "license_rev": "rev-1"}):
            loop.start()
            deadline = time.time() + 3
            while time.time() < deadline and not seen:
                time.sleep(0.02)
            loop.stop()
        self.assertTrue(seen)
        self.assertEqual(seen[0]["license_rev"], "rev-1")

    def test_loop_without_callback_still_works(self):
        calls = []

        def build():
            calls.append(1)
            return "https://127.0.0.1:9", "M", {"platforms": {"alibaba": {}}}

        loop = HeartbeatLoop(build, interval=30)
        loop.start()
        time.sleep(0.3)
        loop.stop()
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)

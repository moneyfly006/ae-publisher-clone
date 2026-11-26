"""生产日志（机器码 5B11795C-CD238492 / v1.0.16）四类错误修复回归。

覆盖：
1) SKU 保存前「规格发生变化」误报（采集箱 raw vs 详情 target 来源不同）——见 test_sku_recovery；
2) AI 标题/详情 timeout/429/5xx 重试 + 真实异常原因记录；
3) 图床上传失败：自定义地址/密钥失败后回退默认图床（含 key 空/错）、重试一次、错误细节；
4) Cookie 非 latin-1（阿拉伯字符）净化后不再抛 UnicodeEncodeError；
5) 「还没有选择店铺」确认属客户设置，错误文案含可操作引导。
所有网络均 mock，不真连网。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aeclone import api as api_mod  # noqa: E402
from aeclone import services  # noqa: E402
from aeclone.api import ApiClient, ApiError  # noqa: E402
from aeclone.integrations.dianxiaomi import (  # noqa: E402
    DianxiaomiClient, sanitize_cookies)
from aeclone.services import hosting  # noqa: E402
from aeclone.services.hosting import DEFAULT_HOST, DEFAULT_KEY, HostError  # noqa: E402
from aeclone.services.text_pipeline import TextPipeline  # noqa: E402


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


class _ChatSession:
    """按顺序返回响应/抛异常的假 session。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        item = self.outcomes.pop(0) if self.outcomes else _Resp(500, {"error": "no outcome"})
        if isinstance(item, Exception):
            raise item
        return item


class ApiRetryTests(unittest.TestCase):
    def _client(self, outcomes):
        sess = _ChatSession(outcomes)
        cfg = NS(api_base="https://api.test", api_key="k", timeout=30)
        client = ApiClient(cfg.api_base, cfg.api_key, timeout=cfg.timeout, session=sess)
        client.RETRIES = 2
        return client, sess

    def test_title_call_retries_timeout_then_succeeds(self):
        client, sess = self._client([
            requests.exceptions.ReadTimeout("Read timeout"),
            _Resp(200, {"choices": [{"message": {"content": "Retry title works"}}]}),
        ])
        cfg = NS(title_prompt="prompt-unique-1", title_prefix="", title_suffix="",
                 detail_prompt="d", api_base="https://api.test", api_key="k", timeout=30)
        with patch.object(api_mod.time, "sleep", lambda *_: None):
            tp = TextPipeline(cfg, client=client)
            res = tp.make_title("原始标题-重试测试-1")
        self.assertTrue(res.ok, res.error)
        self.assertIn("Retry title works", res.value)
        self.assertEqual(sess.calls, 2, "timeout 后应自动重试一次")

    def test_chat_retries_429_and_5xx(self):
        for status in (429, 503):
            client, sess = self._client([
                _Resp(status, {"error": "busy"}, "busy"),
                _Resp(200, {"choices": [{"message": {"content": "ok"}}]}),
            ])
            with patch.object(api_mod.time, "sleep", lambda *_: None):
                self.assertEqual(client.chat("m", [{"role": "user", "content": "x"}]),
                                 "ok")
            self.assertEqual(sess.calls, 2, f"HTTP {status} 应重试")

    def test_chat_retry_exhausted_has_clear_error(self):
        client, sess = self._client([
            requests.exceptions.ReadTimeout("Read timeout"),
            requests.exceptions.ReadTimeout("Read timeout"),
            requests.exceptions.ReadTimeout("Read timeout"),
        ])
        with patch.object(api_mod.time, "sleep", lambda *_: None):
            with self.assertRaises(ApiError) as ctx:
                client.chat("m", [{"role": "user", "content": "x"}])
        self.assertIn("已重试 2 次", str(ctx.exception))
        self.assertIn("网络超时", str(ctx.exception))
        self.assertEqual(sess.calls, 3)

    def test_non_retryable_status_raises_immediately(self):
        client, sess = self._client([_Resp(401, {"error": "Invalid token"}, "Invalid token")])
        with self.assertRaises(ApiError) as ctx:
            client.chat("m", [{"role": "user", "content": "x"}])
        self.assertEqual(sess.calls, 1)
        self.assertIn("Invalid token", str(ctx.exception))

    def test_detail_generation_uses_retry(self):
        client, sess = self._client([
            requests.exceptions.ReadTimeout("Read timeout"),
            _Resp(200, {"choices": [{"message": {"content": "<p>detail ok</p>"}}]}),
        ])
        cfg = NS(title_prompt="t", detail_prompt="detail-prompt-unique-2",
                 api_base="https://api.test", api_key="k", timeout=30)
        with patch.object(api_mod.time, "sleep", lambda *_: None):
            tp = TextPipeline(cfg, client=client)
            res = tp.make_detail("产品标题-重试详情-2")
        self.assertTrue(res.ok, res.error)
        self.assertIn("detail ok", res.value)
        self.assertEqual(sess.calls, 2)

    def test_publisher_records_real_title_exception(self):
        """publisher.py 不再静默吞异常：步骤里带真实原因（静态约束）。"""
        src = (ROOT / "src/aeclone/services/publisher.py").read_text(encoding="utf-8")
        self.assertIn("_title_err", src)
        self.assertIn("调用异常，已跳过：", src)
        self.assertIn("_sku_err", src)


class HostingFallbackTests(unittest.TestCase):
    def setUp(self):
        hosting._CACHE.clear()

    def _file(self, content=b"fake-image-bytes"):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "x.png"
        p.write_bytes(content)
        return p

    def test_custom_host_failure_falls_back_to_default_host_and_key(self):
        calls = []

        def fake_post(url, headers=None, files=None, data=None, timeout=None, **kw):
            calls.append((url, (headers or {}).get("Authorization")))
            if "custom.test" in url:
                raise requests.ConnectionError("custom host down")
            if url.rstrip("/").endswith("/rest/upload"):
                if (headers or {}).get("Authorization") == DEFAULT_KEY:
                    return _Resp(200, {"data": [{"url": "https://img.test/ok.jpg",
                                                 "delToken": "t1"}]})
                return _Resp(401, {"error": "bad key"}, "bad key")
            return _Resp(404, {}, "not found")

        with patch.object(hosting.requests, "post", fake_post), \
                patch("time.sleep", lambda *_: None):
            url = hosting.upload(self._file(b"unique-custom-fail-1"),
                                 base="https://custom.test", key="wrong-key",
                                 timeout=10)
        self.assertEqual(url, "https://img.test/ok.jpg")
        self.assertTrue(any(DEFAULT_HOST in u and auth == DEFAULT_KEY
                            for u, auth in calls),
                        "失败后必须回退默认图床+默认密钥")

    def test_same_base_but_empty_key_also_falls_back_to_default_key(self):
        calls = []

        def fake_post(url, headers=None, files=None, data=None, timeout=None, **kw):
            calls.append((url, (headers or {}).get("Authorization")))
            if DEFAULT_HOST in url and url.rstrip("/").endswith("/rest/upload"):
                if (headers or {}).get("Authorization") == DEFAULT_KEY:
                    return _Resp(200, {"data": [{"url": "https://img.test/key.jpg"}]})
                return _Resp(401, {"error": "unauthorized"}, "unauthorized")
            return _Resp(404, {}, "not found")

        with patch.object(hosting.requests, "post", fake_post), \
                patch("time.sleep", lambda *_: None):
            url = hosting.upload(self._file(b"unique-empty-key-2"),
                                 base=DEFAULT_HOST, key="", timeout=10)
        self.assertEqual(url, "https://img.test/key.jpg")
        self.assertTrue(any(auth == DEFAULT_KEY for _u, auth in calls))

    def test_all_attempts_fail_reports_last_error_detail(self):
        def fake_post(url, headers=None, files=None, data=None, timeout=None, **kw):
            if "/rest/upload" in url:
                return _Resp(503, {"error": "upstream busy"}, "upstream busy")
            return _Resp(404, {}, "not found")

        with patch.object(hosting.requests, "post", fake_post), \
                patch("time.sleep", lambda *_: None):
            with self.assertRaises(HostError) as ctx:
                hosting.upload(self._file(b"unique-all-fail-3"),
                               base="https://custom.test", key="k", timeout=10)
        msg = str(ctx.exception)
        self.assertIn("上传失败", msg)
        self.assertIn("重试", msg)
        self.assertIn("回退默认图床", msg)
        self.assertIn("HTTP 503", msg)
        self.assertIn("upstream busy", msg)


class CookieSanitizeTests(unittest.TestCase):
    def test_arabic_cookie_is_percent_encoded_not_crashing(self):
        cookies = {"dxm_i": "normal", "dxm_t": "abc\u063eXYZ"}
        clean = sanitize_cookies(cookies)
        self.assertEqual(clean["dxm_i"], "normal")
        self.assertIn("%", clean["dxm_t"])
        for value in clean.values():
            value.encode("latin-1")   # 必须可写 Cookie 头
        client = DianxiaomiClient(cookies)
        req = requests.Request("GET", "https://www.dianxiaomi.com/api/x.json")
        prepared = client.session.prepare_request(req)
        header = prepared.headers.get("Cookie", "")
        header.encode("latin-1")      # 修复前这里会 UnicodeEncodeError
        self.assertIn("dxm_i=normal", header)

    def test_non_latin1_cookie_name_is_skipped(self):
        clean = sanitize_cookies({"bad\u063e": "v", "ok": "1"})
        self.assertNotIn("bad\u063e", clean)
        self.assertEqual(clean.get("ok"), "1")
        DianxiaomiClient({"ok": "1", "bad\u063e": "v"})   # 构造不抛异常


class ShopSettingMessageTests(unittest.TestCase):
    def test_missing_shop_message_is_actionable_customer_setting(self):
        src = (ROOT / "src/aeclone/ui/app.py").read_text(encoding="utf-8")
        self.assertIn("还没有选择店铺", src)
        self.assertIn("读取并选择", src, "缺少可操作引导（去店铺页选择店铺）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

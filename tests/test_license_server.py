import sys, time, unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.license_server import fetch_entitlement_token, refresh_entitlements
from aeclone.licensing import issue_token, load_entitlements
from aeclone.license import machine_code as real_machine_code


class Cfg(dict):
    def __getattr__(self, k):
        return self.get(k, "")
    def set(self, k, v):
        self[k] = v


class _Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {}
    def json(self):
        return self._data


class _Sess:
    """假 session：只实现 request（客户端统一走 request + allow_redirects=False）。"""

    def __init__(self, token="", code=0, empty_ok=False):
        self.token, self.code, self.empty_ok = token, code, empty_ok
        self.calls = []

    def request(self, method, url, timeout=None, allow_redirects=True, **kw):
        self.calls.append((method, url))
        if self.token:
            return _Resp(200, {"code": 0, "msg": "ok", "data": {"token": self.token}})
        if self.empty_ok:
            return _Resp(200, {"code": 0, "msg": "ok", "data": {"token": ""}})
        return _Resp(404, {})

    # 兼容可能出现的直接 post/get 调用
    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


class _RedirectSess(_Sess):
    """第一次返回 308（Location 不带 query），第二次记录最终 URL 并成功。"""

    def __init__(self, token=""):
        super().__init__(token=token)
        self.final_url = ""

    def request(self, method, url, timeout=None, allow_redirects=True, **kw):
        self.calls.append((method, url))
        if len(self.calls) == 1:
            return _Resp(308, {}, {"location": "https://license.moneyfly.club/shop/api.php"})
        self.final_url = url
        return _Resp(200, {"code": 0, "msg": "邮箱或密码错误", "data": None})


class LicenseServerTests(unittest.TestCase):
    def test_canonical_server_maps_legacy_alias(self):
        from aeclone.license import canonical_server, shop_url
        self.assertEqual(canonical_server("https://ali.moneyfly.club/license"),
                         "https://license.moneyfly.club")
        self.assertEqual(canonical_server("https://ali.moneyfly.club/license/"),
                         "https://license.moneyfly.club")
        # 正式地址/其它地址保持原样（只去掉末尾斜杠）
        self.assertEqual(canonical_server("https://license.moneyfly.club/"),
                         "https://license.moneyfly.club")
        self.assertEqual(canonical_server("https://lic.test"), "https://lic.test")
        self.assertEqual(shop_url("https://lic.test"), "https://lic.test/shop/")

    def test_fetch_empty_token_has_actionable_message(self):
        """服务端 200 但无 token → 明确提示未绑定，而不是最后的 404。"""
        got, err = fetch_entitlement_token("https://lic.test", "PC-1",
                                           session=_Sess(empty_ok=True))
        self.assertEqual(got, "")
        self.assertIn("未绑定授权账号", err)
        self.assertNotIn("HTTP 404", err)

    def test_software_login_preserves_action_query_across_redirect(self):
        """旧域名 308 跳转丢 query 会让服务端报「未知操作」；客户端应补回 action。"""
        from aeclone.license_server import software_login
        sess = _RedirectSess()
        software_login("https://lic.test", "a@b.c", "pw", "PC-1", timeout=3, session=sess)
        self.assertTrue(sess.final_url.endswith("?action=software_login"),
                        f"最终 URL 丢了 action：{sess.final_url}")

    def test_fetch_returns_token(self):
        tok = issue_token("PC-1", {"alibaba": ["listing"]}, "2027-11-17")
        s = _Sess(token=tok)
        got, err = fetch_entitlement_token("https://lic.test", "PC-1", session=s)
        self.assertEqual(got, tok)
        self.assertEqual(err, "")
        self.assertTrue(s.calls)

    def test_fetch_failure(self):
        got, err = fetch_entitlement_token("https://lic.test", "PC-1", session=_Sess())
        self.assertEqual(got, "")
        self.assertTrue(err)

    def test_refresh_applies_token(self):
        tok = issue_token(real_machine_code(), {"alibaba": ["listing", "ai_image"]}, "2027-11-17")
        cfg = Cfg(license_server="https://lic.test", license_token="", license_token_ts=0,
                  license_platforms="", license_cache_ok=False, license_identity="PC-1")
        saved = []
        ent = refresh_entitlements(cfg, version="1.0.5", save=lambda c: saved.append(1),
                                   session=_Sess(token=tok))
        self.assertTrue(ent.has_feature("alibaba", "ai_image"))
        self.assertEqual(cfg.license_token, tok)
        self.assertTrue(saved)
        # 本地再解析应一致
        e2 = load_entitlements(cfg)
        self.assertTrue(e2.has_platform("alibaba"))

    def test_refresh_keeps_cache_on_failure(self):
        cfg = Cfg(license_server="https://lic.test", license_token="", license_token_ts=0,
                  license_platforms="", license_cache_ok=True, license_identity="PC-1")
        ent = refresh_entitlements(cfg, session=_Sess())
        self.assertTrue(ent.has_platform("alibaba"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

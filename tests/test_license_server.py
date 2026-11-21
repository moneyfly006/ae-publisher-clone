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
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data or {}
    def json(self):
        return self._data


class _Sess:
    def __init__(self, token="", code=0):
        self.token, self.code = token, code
        self.calls = []
    def post(self, url, json=None, timeout=None):
        self.calls.append(("post", url))
        if self.token:
            return _Resp(200, {"code": 0, "data": {"token": self.token}})
        return _Resp(200, {"code": self.code, "msg": "no token"})
    def get(self, url, params=None, timeout=None):
        self.calls.append(("get", url))
        return _Resp(404, {})


class LicenseServerTests(unittest.TestCase):
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

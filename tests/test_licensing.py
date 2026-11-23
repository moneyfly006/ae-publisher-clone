import sys, time, unittest
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.licensing import (issue_token, verify_token, load_entitlements,
                               sign_payload, PLATFORM_FEATURES)


def cfg(**kw):
    base = dict(license_platforms="", license_token="", license_token_ts=0,
                license_cache_ok=False)
    base.update(kw)
    return NS(**base)


class EntitlementTests(unittest.TestCase):
    def test_issue_and_verify(self):
        tok = issue_token("PC-1", {"alibaba": ["listing", "ai_image"]}, "2027-11-17")
        p = verify_token(tok)
        self.assertEqual(p["machine_code"], "PC-1")
        self.assertIn("listing", p["platforms"]["alibaba"])

    def test_tampered_token_rejected(self):
        tok = issue_token("PC-1", {"alibaba": ["listing"]}, "2027-11-17")
        bad = tok.replace("listing", "audit")
        self.assertIsNone(verify_token(bad))

    def test_feature_gate(self):
        tok = issue_token("PC-1", {"alibaba": ["listing"]}, "2027-11-17")
        e = load_entitlements(cfg(license_token=tok, license_token_ts=time.time()))
        self.assertTrue(e.has_platform("alibaba"))
        self.assertTrue(e.has_feature("alibaba", "listing"))
        self.assertFalse(e.has_feature("alibaba", "ai_image"))
        self.assertFalse(e.has_platform("aliexpress"))

    def test_expired_token(self):
        tok = issue_token("PC-1", {"alibaba": ["listing"]}, "2020-01-01")
        e = load_entitlements(cfg(license_token=tok, license_token_ts=time.time()))
        self.assertTrue(e.expired)
        self.assertFalse(e.has_platform("alibaba"))

    def test_offline_grace(self):
        tok = issue_token("PC-1", {"alibaba": ["listing"]}, "2027-11-17")
        old = time.time() - 8 * 86400
        e = load_entitlements(cfg(license_token=tok, license_token_ts=old))
        self.assertFalse(e.has_platform("alibaba"))   # 超过 7 天需重新校验
        e2 = load_entitlements(cfg(license_token=tok, license_token_ts=time.time() - 2 * 86400))
        self.assertTrue(e2.has_platform("alibaba"))

    def test_override_for_dev(self):
        e = load_entitlements(cfg(license_platforms="alibaba:listing+ai_text"))
        self.assertTrue(e.has_feature("alibaba", "ai_text"))
        self.assertFalse(e.has_feature("aliexpress", "listing"))

    def test_legacy_customer_gets_both(self):
        e = load_entitlements(cfg(license_cache_ok=True))
        self.assertTrue(e.has_platform("alibaba"))
        self.assertTrue(e.has_platform("aliexpress"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

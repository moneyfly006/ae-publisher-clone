"""Existing device customers keep their rights when adding an account package."""
from datetime import datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.licensing import PLATFORM_FEATURES, issue_token, load_entitlements, sign_payload
import json


class LicenseCompatibilityTests(unittest.TestCase):
    def cfg(self, **kwargs):
        return AppConfig(dict(license_platforms="", license_cache_ok=False, **kwargs))

    def test_existing_device_and_new_amazon_package_are_combined(self):
        cfg = self.cfg(license_token=issue_token("fixture", {"amazon": ["listing"]}, "2099-01-01"))
        cfg.set("license_cache_ok", True)
        cfg.set("license_cache_expiry", "2099-01-01")
        ent = load_entitlements(cfg)
        self.assertEqual(set(ent.platforms_list()), set(PLATFORM_FEATURES))

    def test_account_compatibility_voucher_does_not_unlock_legacy_platforms(self):
        cfg = self.cfg(license_token=issue_token("fixture", {"amazon": ["listing"]}, "2099-01-01"),
                       license_cache_legacy=False)
        cfg.set("license_cache_ok", True)
        self.assertEqual(load_entitlements(cfg).platforms_list(), ["amazon"])

    def test_independent_expiry_filters_each_feature(self):
        payload = dict(machine_code="fixture", platforms={"alibaba": ["listing", "ai_text"], "amazon": ["listing"]},
                       expires_at="2030-01-01", issued_at=1,
                       feature_expiries={"alibaba": {"listing": "2030-12-31", "ai_text": "2030-01-01"},
                                         "amazon": {"listing": "2030-12-31"}})
        token = json.dumps(dict(payload=payload, sig=sign_payload(payload)))
        now = datetime(2030, 2, 1, 12).timestamp()
        cfg = self.cfg(license_token=token, license_token_ts=now)
        ent = load_entitlements(cfg, now=now)
        self.assertTrue(ent.has_feature("alibaba", "listing"))
        self.assertFalse(ent.has_feature("alibaba", "ai_text"))
        self.assertTrue(ent.has_platform("amazon"))

    def test_expiry_day_is_inclusive(self):
        cfg = self.cfg(license_token=issue_token("fixture", {"amazon": ["listing"]}, "2030-01-01"))
        self.assertTrue(load_entitlements(cfg, now=datetime(2030, 1, 1, 12).timestamp()).has_platform("amazon"))
        self.assertFalse(load_entitlements(cfg, now=datetime(2030, 1, 2, 0).timestamp()).has_platform("amazon"))

    def test_old_token_offline_expiry_does_not_remove_current_device_license(self):
        cfg = self.cfg(license_token=issue_token("fixture", {"amazon": ["listing"]}, "2099-01-01"), license_token_ts=1)
        cfg.set("license_cache_ok", True)
        cfg.set("license_cache_expiry", "2099-01-01")
        self.assertEqual(set(load_entitlements(cfg).platforms_list()), set(PLATFORM_FEATURES))


if __name__ == "__main__":
    unittest.main()

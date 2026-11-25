"""Token 契约兼容性（v1.0.10 后端 ↔ v1.1.0 客户端）。

契约 §6：token 格式/签名密钥不变（老服务端签的 token 新客户端仍能验签）；
客户端只认 token，不再把旧设备授权（voucher / license_identity / 缓存）叠加进来。
"""
import datetime
import hashlib
import hmac
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig  # noqa: E402
from aeclone.licensing import (PLATFORM_FEATURES, issue_token, load_entitlements,  # noqa: E402
                               sign_payload, verify_token)

MACHINE = "ABCD1234-EF567890"
KEY = b"aurora-list-platform-license-v1"


def cfg(**kwargs):
    data = dict(license_platforms="", license_token="", license_token_ts=0,
                license_account_email="user@example.com")
    data.update(kwargs)
    return AppConfig(data)


class TokenCompatibilityTests(unittest.TestCase):
    def test_server_signed_token_verifies(self):
        """按服务端 lc_sign_token 的 canonical 规则手写 token，客户端必须验签通过。"""
        payload = {"machine_code": MACHINE, "expires_at": "2099-01-01",
                   "platforms": {"alibaba": ["listing", "ai_text"]},
                   "issued_at": 1790000000}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        sig = hmac.new(KEY, canonical, hashlib.sha256).hexdigest()
        token = json.dumps({"payload": payload, "sig": sig}, ensure_ascii=False)
        self.assertIsNotNone(verify_token(token))
        ent = load_entitlements(cfg(license_token=token), machine=MACHINE)
        self.assertTrue(ent.has_feature("alibaba", "ai_text"))

    def test_feature_expiries_filter_independently(self):
        payload = dict(machine_code=MACHINE,
                       platforms={"alibaba": ["listing", "ai_text"],
                                  "amazon": ["listing"]},
                       expires_at="2030-01-01", issued_at=1,
                       feature_expiries={"alibaba": {"listing": "2030-12-31",
                                                     "ai_text": "2030-01-01"},
                                         "amazon": {"listing": "2030-12-31"}})
        token = json.dumps(dict(payload=payload, sig=sign_payload(payload)))
        now = datetime.datetime(2030, 2, 1, 12).timestamp()
        ent = load_entitlements(cfg(license_token=token, license_token_ts=now),
                                now=now, machine=MACHINE)
        self.assertTrue(ent.has_feature("alibaba", "listing"))
        self.assertFalse(ent.has_feature("alibaba", "ai_text"))
        self.assertTrue(ent.has_platform("amazon"))

    def test_expiry_day_is_inclusive(self):
        token = issue_token(MACHINE, {"amazon": ["listing"]}, "2030-01-01")
        day = datetime.datetime(2030, 1, 1, 12).timestamp()
        ent = load_entitlements(cfg(license_token=token, license_token_ts=day),
                               now=day, machine=MACHINE)
        self.assertTrue(ent.has_platform("amazon"))
        after = datetime.datetime(2030, 1, 2, 0).timestamp()
        ent2 = load_entitlements(cfg(license_token=token, license_token_ts=day),
                                 now=after, machine=MACHINE)
        self.assertFalse(ent2.has_platform("amazon"))

    def test_old_device_license_does_not_unlock_platforms(self):
        """旧客户 localStorage 里残留的授权键不得再开全部平台（契约 §10.7）。"""
        token = issue_token(MACHINE, {"amazon": ["listing"]}, "2099-01-01")
        c = cfg(license_token=token, license_token_ts=time.time())
        # 老配置里可能存在、但新版本已删除的键
        c.set("license_cache_ok", True)
        c.set("license_cache_expiry", "2099-01-01")
        c.set("license_identity", "OLD-LEGACY-ID")
        ent = load_entitlements(c, machine=MACHINE)
        self.assertEqual(ent.platforms_list(), ["amazon"])

    def test_no_token_means_no_platforms(self):
        c = cfg()
        c.set("license_cache_ok", True)
        c.set("license_identity", "OLD-LEGACY-ID")
        ent = load_entitlements(c, machine=MACHINE)
        self.assertEqual(ent.platforms_list(), [])
        self.assertFalse(ent.has_platform("amazon"))

    def test_all_platforms_transfer_when_token_has_them(self):
        token = issue_token(MACHINE, {p: list(f) for p, f in PLATFORM_FEATURES.items()},
                            "2099-01-01")
        ent = load_entitlements(cfg(license_token=token), machine=MACHINE)
        self.assertEqual(set(ent.platforms_list()), set(PLATFORM_FEATURES))


if __name__ == "__main__":
    unittest.main()

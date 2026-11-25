"""平台授权 token 解析（账号制 v3）。

契约 §6：token = {"payload":{machine_code,platforms,expires_at,feature_expiries,issued_at},
                "sig": HMAC-SHA256(canonical_json(payload))}
契约 §10：只认 token（旧设备授权码 / voucher / license_identity 全部删除）。
"""
import json
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.licensing import (  # noqa: E402
    PLATFORM_FEATURES, apply_license_token, issue_token, load_entitlements,
    sign_payload, silent_login_allowed, verify_token)

MACHINE = "ABCD1234-EF567890"


def cfg(**kw):
    class Cfg:
        def __init__(self, data):
            self.__dict__.update(data)

        def set(self, key, value):
            setattr(self, key, value)

    base = dict(license_platforms="", license_token="", license_token_ts=0,
                license_account_email="user@example.com",
                license_auto_login=True)
    base.update(kw)
    return Cfg(base)


class TokenFormatTests(unittest.TestCase):
    def test_issue_and_verify(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing", "ai_image"]}, "2027-11-17")
        payload = verify_token(tok)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["machine_code"], MACHINE)
        self.assertIn("listing", payload["platforms"]["alibaba"])
        obj = json.loads(tok)
        self.assertEqual(set(obj), {"payload", "sig"})
        self.assertEqual(len(obj["sig"]), 64)
        self.assertEqual(obj["sig"], sign_payload(obj["payload"]))

    def test_contract_canonical_json_is_server_compatible(self):
        """服务端 lc_sign_token 的 canonical（按键排序、无空格）必须一致。"""
        payload = {"machine_code": MACHINE, "expires_at": "2027-11-17",
                   "platforms": {"alibaba": ["listing"]}, "issued_at": 1790000000}
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        import hashlib
        import hmac
        expected = hmac.new(b"aurora-list-platform-license-v1", canonical,
                            hashlib.sha256).hexdigest()
        self.assertEqual(sign_payload(payload), expected)
        tok = json.dumps({"payload": payload, "sig": expected}, ensure_ascii=False)
        self.assertIsNotNone(verify_token(tok))

    def test_tampered_token_rejected(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        self.assertIsNone(verify_token(tok.replace("listing", "audit")))
        self.assertIsNone(verify_token("not-json"))


class EntitlementTests(unittest.TestCase):
    def test_feature_gate(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        ent = load_entitlements(cfg(license_token=tok, license_token_ts=time.time()),
                                machine=MACHINE)
        self.assertEqual(ent.source, "token")
        self.assertTrue(ent.has_platform("alibaba"))
        self.assertTrue(ent.has_feature("alibaba", "listing"))
        self.assertFalse(ent.has_feature("alibaba", "ai_image"))
        self.assertFalse(ent.has_platform("aliexpress"))

    def test_expired_token_locks_all_platforms(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2020-01-01")
        ent = load_entitlements(cfg(license_token=tok, license_token_ts=time.time()),
                                machine=MACHINE)
        self.assertTrue(ent.expired)
        self.assertFalse(ent.has_platform("alibaba"))

    def test_expiry_day_is_inclusive(self):
        tok = issue_token(MACHINE, {"amazon": ["listing"]}, "2030-01-01")
        import datetime
        day = datetime.datetime(2030, 1, 1, 12).timestamp()
        ent = load_entitlements(cfg(license_token=tok, license_token_ts=day),
                                now=day, machine=MACHINE)
        self.assertTrue(ent.has_platform("amazon"))
        later = datetime.datetime(2030, 1, 2, 0).timestamp()
        ent2 = load_entitlements(cfg(license_token=tok, license_token_ts=day),
                                 now=later, machine=MACHINE)
        self.assertFalse(ent2.has_platform("amazon"))

    def test_machine_mismatch_locks(self):
        tok = issue_token("OTHER-MACHINE", {"alibaba": ["listing"]}, "2027-11-17")
        ent = load_entitlements(cfg(license_token=tok, license_token_ts=time.time()),
                                machine=MACHINE)
        self.assertEqual(ent.source, "machine-mismatch")
        self.assertFalse(ent.has_platform("alibaba"))

    def test_offline_grace(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        old = time.time() - 8 * 86400
        ent = load_entitlements(cfg(license_token=tok, license_token_ts=old),
                                machine=MACHINE)
        self.assertEqual(ent.source, "token-offline-expired")
        self.assertFalse(ent.has_platform("alibaba"))
        ent2 = load_entitlements(cfg(license_token=tok,
                                     license_token_ts=time.time() - 2 * 86400),
                                 machine=MACHINE)
        self.assertTrue(ent2.has_platform("alibaba"))

    def test_feature_expiries_filter_each_feature(self):
        payload = dict(machine_code=MACHINE,
                       platforms={"alibaba": ["listing", "ai_text"],
                                  "amazon": ["listing"]},
                       expires_at="2030-01-01", issued_at=1,
                       feature_expiries={"alibaba": {"listing": "2030-12-31",
                                                     "ai_text": "2030-01-01"},
                                         "amazon": {"listing": "2030-12-31"}})
        token = json.dumps(dict(payload=payload, sig=sign_payload(payload)))
        import datetime
        now = datetime.datetime(2030, 2, 1, 12).timestamp()
        ent = load_entitlements(cfg(license_token=token, license_token_ts=now),
                                now=now, machine=MACHINE)
        self.assertTrue(ent.has_feature("alibaba", "listing"))
        self.assertFalse(ent.has_feature("alibaba", "ai_text"))
        self.assertTrue(ent.has_platform("amazon"))
        self.assertEqual(ent.platform_expiry("amazon"), "2030-12-31")

    def test_empty_platforms_token_is_valid_but_locked(self):
        """无任何权限的用户也拿到合法 token：可登录，但平台全部锁定。"""
        tok = issue_token(MACHINE, {}, "")
        ent = load_entitlements(cfg(license_token=tok, license_token_ts=time.time()),
                                machine=MACHINE)
        self.assertEqual(ent.source, "token")
        self.assertFalse(ent.expired)
        self.assertEqual(ent.platforms_list(), [])

    def test_no_legacy_unlock(self):
        """旧配置键（license_cache_ok / license_identity 等）不得再开通平台。"""
        ent = load_entitlements(cfg(license_cache_ok=True, license_identity="OLD-ID"),
                                machine=MACHINE)
        self.assertEqual(ent.platforms_list(), [])

    def test_override_for_dev_only(self):
        import os
        from unittest.mock import patch
        from aeclone.licensing import override_enabled
        # 生产环境（没有任何显式开关）→ 覆盖被忽略
        with patch.dict(os.environ, {"AURORA_LICENSE_OVERRIDE": "",
                                     "AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.assertFalse(override_enabled())
            ent = load_entitlements(cfg(license_platforms="alibaba:listing+ai_text"),
                                    machine=MACHINE)
            self.assertEqual(ent.platforms_list(), [])
            self.assertNotEqual(ent.source, "override")
        # 显式 AURORA_LICENSE_OVERRIDE=1（或 AURORA_TEST=1）→ 生效
        with patch.dict(os.environ, {"AURORA_LICENSE_OVERRIDE": "1"}):
            self.assertTrue(override_enabled())
            ent = load_entitlements(cfg(license_platforms="alibaba:listing+ai_text"),
                                    machine=MACHINE)
            self.assertEqual(ent.source, "override")
            self.assertTrue(ent.has_feature("alibaba", "ai_text"))
            self.assertFalse(ent.has_feature("aliexpress", "listing"))
        with patch.dict(os.environ, {"AURORA_LICENSE_OVERRIDE": "",
                                     "AURORA_TEST": "1"}):
            self.assertTrue(override_enabled())
            ent2 = load_entitlements(cfg(license_platforms="amazon:listing"),
                                     machine=MACHINE)
            self.assertTrue(ent2.has_platform("amazon"))

    def test_legacy_platform_constant_unchanged(self):
        """服务端功能包定义不变（迁移与后台一致）。"""
        self.assertEqual(set(PLATFORM_FEATURES),
                         {"alibaba", "aliexpress", "amazon", "tiktok", "shopee"})


class ApplyTokenTests(unittest.TestCase):
    def test_apply_rejects_bad_token(self):
        c = cfg()
        with self.assertRaises(ValueError):
            apply_license_token(c, "bad-token", machine=MACHINE)

    def test_apply_saves_token_and_timestamp(self):
        c = cfg()
        tok = issue_token(MACHINE, {"amazon": ["listing"]}, "2027-11-17")
        saved = []
        ent = apply_license_token(c, tok, save=lambda cc: saved.append(1),
                                  machine=MACHINE)
        self.assertEqual(c.license_token, tok)
        self.assertGreater(int(c.license_token_ts), 0)
        self.assertTrue(saved)
        self.assertTrue(ent.has_platform("amazon"))


class SilentLoginTests(unittest.TestCase):
    def test_allowed_when_token_valid_and_auto_login(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        c = cfg(license_token=tok, license_token_ts=time.time())
        self.assertTrue(silent_login_allowed(c, machine=MACHINE))

    def test_requires_auto_login(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        c = cfg(license_token=tok, license_token_ts=time.time(),
                license_auto_login=False)
        self.assertFalse(silent_login_allowed(c, machine=MACHINE))

    def test_requires_saved_email(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        c = cfg(license_token=tok, license_token_ts=time.time(),
                license_account_email="")
        self.assertFalse(silent_login_allowed(c, machine=MACHINE))

    def test_rejects_expired_or_mismatched(self):
        expired = issue_token(MACHINE, {"alibaba": ["listing"]}, "2020-01-01")
        c1 = cfg(license_token=expired, license_token_ts=time.time())
        self.assertFalse(silent_login_allowed(c1, machine=MACHINE))
        other = issue_token("OTHER", {"alibaba": ["listing"]}, "2027-11-17")
        c2 = cfg(license_token=other, license_token_ts=time.time())
        self.assertFalse(silent_login_allowed(c2, machine=MACHINE))

    def test_offline_beyond_grace_requires_login(self):
        tok = issue_token(MACHINE, {"alibaba": ["listing"]}, "2027-11-17")
        c = cfg(license_token=tok, license_token_ts=time.time() - 8 * 86400)
        self.assertFalse(silent_login_allowed(c, machine=MACHINE))


if __name__ == "__main__":
    unittest.main(verbosity=2)

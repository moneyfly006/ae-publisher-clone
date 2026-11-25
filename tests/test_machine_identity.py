"""机器码：多硬件组合应唯一、稳定，并作为账号授权的绑定标识。

契约 §10.3：只认 token、token 绑定本机机器码；旧机器码兼容候选（legacy_machine_code /
identity_candidates）与 ECDSA voucher 已删除。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import aeclone.license as lic  # noqa: E402
from aeclone.licensing import issue_token, load_entitlements, verify_token  # noqa: E402


class MachineCodeTests(unittest.TestCase):
    def test_different_hardware_gives_different_code(self):
        with patch.object(lic, "_identity_components",
                          return_value=["MACHINE-A", "SERIAL-A"]):
            a = lic.machine_code()
        with patch.object(lic, "_identity_components",
                          return_value=["MACHINE-B", "SERIAL-B"]):
            b = lic.machine_code()
        self.assertNotEqual(a, b)

    def test_same_hardware_is_stable(self):
        with patch.object(lic, "_identity_components",
                          return_value=["MACHINE-A", "SERIAL-A"]):
            a = lic.machine_code()
            a2 = lic.machine_code()
        self.assertEqual(a, a2)

    def test_machine_code_format(self):
        with patch.object(lic, "_identity_components", return_value=["MACHINE-A"]):
            code = lic.machine_code()
            full = lic.machine_id()
        self.assertEqual(len(code), 17)
        self.assertEqual(code[8], "-")
        self.assertTrue(code.replace("-", "").isalnum())
        self.assertEqual(len(full), 64)          # sha256 十六进制

    def test_legacy_compat_api_removed(self):
        for name in ("legacy_machine_code", "legacy_machine_id",
                     "identity_candidates", "verify", "verify_voucher",
                     "PUBLIC_KEY_PEM"):
            self.assertFalse(hasattr(lic, name), f"旧接口应已删除：{name}")

    def test_token_bound_to_this_machine(self):
        with patch.object(lic, "_identity_components", return_value=["MACHINE-A"]):
            code = lic.machine_code()
        tok = issue_token(code, {"alibaba": ["listing"]}, "2099-01-01")
        self.assertEqual(verify_token(tok)["machine_code"], code)

        class Cfg:
            license_platforms = ""
            license_token = tok
            license_token_ts = 9_999_999_999

        cfg = Cfg()
        ok = load_entitlements(cfg, machine=code)
        self.assertTrue(ok.has_platform("alibaba"))
        bad = load_entitlements(cfg, machine="SOME-OTHER-MACHINE")
        self.assertFalse(bad.has_platform("alibaba"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

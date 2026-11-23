"""Local account credential storage for the migration flow.

The password is encrypted with a key derived from this machine's identity and
an application salt. This is not a substitute for an OS keychain, but it keeps
plaintext out of config.json and cannot be decrypted on another machine.
"""
from __future__ import annotations

import base64
import hashlib

from .license import machine_id


def _key() -> bytes:
    return hashlib.sha256(("AuroraList|account-v1|" + machine_id()).encode("utf-8")).digest()


def save_password(cfg, password: str) -> bool:
    try:
        from Crypto.Cipher import AES
        nonce = __import__("os").urandom(12)
        cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
        sealed, tag = cipher.encrypt_and_digest(str(password).encode("utf-8"))
        cfg.set("license_account_password", base64.b64encode(nonce + tag + sealed).decode("ascii"))
        cfg.set("license_account_save_password", True)
        return True
    except Exception:
        return False


def load_password(cfg) -> str:
    try:
        from Crypto.Cipher import AES
        raw = base64.b64decode(str(getattr(cfg, "license_account_password", "") or ""))
        nonce, tag, sealed = raw[:12], raw[12:28], raw[28:]
        cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(sealed, tag).decode("utf-8")
    except Exception:
        return ""


def clear_password(cfg) -> None:
    cfg.set("license_account_password", "")
    cfg.set("license_account_save_password", False)

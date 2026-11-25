"""本地账号凭据存储（账号制 v3 强制登录）。

密码用「本机机器码 + 应用盐」派生的密钥加密后写入 config.json，不落明文，
换机器也无法解密。仅当用户勾选「记住密码」时才保存；退出登录立即清除。

契约 §10.6：退出登录清除 token / 邮箱 / 密码。
"""
from __future__ import annotations

import base64
import hashlib

from .license import machine_id

# 退出登录时必须清掉的账号相关配置键（token 是授权唯一凭据）
ACCOUNT_KEYS = (
    "license_token",
    "license_token_ts",
    "license_account_email",
    "license_account_save_password",
    "license_account_password",
    "license_auto_login",
)


def _key() -> bytes:
    return hashlib.sha256(("AuroraList|account-v1|" + machine_id()).encode("utf-8")).digest()


def save_password(cfg, password: str) -> bool:
    """加密保存密码（失败返回 False，不影响登录本身）。"""
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
    """解密已保存的密码；未保存/解密失败返回空串。"""
    try:
        if not bool(getattr(cfg, "license_account_save_password", False)):
            return ""
        from Crypto.Cipher import AES
        raw = base64.b64decode(str(getattr(cfg, "license_account_password", "") or ""))
        nonce, tag, sealed = raw[:12], raw[12:28], raw[28:]
        cipher = AES.new(_key(), AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(sealed, tag).decode("utf-8")
    except Exception:
        return ""


def has_saved_password(cfg) -> bool:
    return bool(bool(getattr(cfg, "license_account_save_password", False))
                and str(getattr(cfg, "license_account_password", "") or "").strip())


def remember_password(cfg, password: str = "", save: bool = True) -> bool:
    """按用户勾选「记住密码」保存或清除密码。"""
    if save:
        return save_password(cfg, password)
    clear_password(cfg)
    return False


def clear_password(cfg) -> None:
    cfg.set("license_account_password", "")
    cfg.set("license_account_save_password", False)


def clear_credentials(cfg) -> None:
    """退出登录：清除 token / 邮箱 / 密码 / 自动登录标记。"""
    for key in ACCOUNT_KEYS:
        cfg.set(key, 0 if key.endswith("_ts") else "")
    # auto_login 是布尔偏好，退出后重置为 False；下次勾选再打开。
    cfg.set("license_auto_login", False)

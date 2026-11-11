"""直接读取 Chromium 系浏览器 Cookie（无第三方依赖，除 pycryptodome）。"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from hashlib import pbkdf2_hmac
from pathlib import Path

from Crypto.Cipher import AES

SALT = b"saltysalt"
IV = b" " * 16


def _profiles(root: Path) -> list[Path]:
    out = []
    if (root / "Default").exists():
        out.append(root / "Default")
    for p in sorted(root.glob("Profile *")):
        out.append(p)
    return out


def _cookie_dbs(root: Path) -> list[Path]:
    dbs = []
    for prof in _profiles(root):
        for name in ("Cookies", os.path.join("Network", "Cookies")):
            p = prof / name
            if p.exists():
                dbs.append(p)
    legacy = root / "Cookies"
    if legacy.exists():
        dbs.append(legacy)
    return dbs


def _mac_key() -> bytes:
    try:
        pw = subprocess.check_output(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"],
            stderr=subprocess.DEVNULL).strip()
    except Exception:
        pw = b"peanuts"
    return pbkdf2_hmac("sha1", pw, SALT, 1003, 16)


def _win_key() -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(data: bytes) -> DATA_BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Local State"
    try:
        raw = json.loads(local.read_text(encoding="utf-8"))["os_crypt"]["encrypted_key"]
        import base64
        enc = base64.b64decode(raw)[5:]
        crypt32 = ctypes.windll.crypt32
        out = DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(blob(enc)), None, None, None, None, 0, ctypes.byref(out)):
            return b""
        return ctypes.string_at(out.pbData, out.cbData)
    except Exception:
        return b""


def _decrypt(value: bytes, key: bytes) -> str:
    try:
        if value[:3] in (b"v10", b"v11"):
            data = value[3:]
            if sys.platform == "win32" and key:
                try:
                    from Crypto.Cipher import AES as _A
                    nonce, ct = data[3:15], data[15:]
                    cipher = _A.new(key, _A.MODE_GCM, nonce=nonce)
                    return cipher.decrypt(ct)[:-16].decode("utf-8", "ignore")
                except Exception:
                    pass
            cipher = AES.new(key, AES.MODE_CBC, IV)
            dec = cipher.decrypt(data)
            pad = dec[-1] if dec else 0
            if 0 < pad <= 16:
                dec = dec[:-pad]
            if len(dec) > 32:
                dec = dec[32:]
            return dec.decode("utf-8", "ignore")
        return value.decode("utf-8", "ignore")
    except Exception:
        return ""


def read_cookies(browser: str = "chrome", domain: str = "dianxiaomi.com") -> dict:
    home = Path.home()
    if sys.platform == "darwin":
        roots = {
            "chrome": home / "Library/Application Support/Google/Chrome",
            "edge": home / "Library/Application Support/Microsoft Edge",
            "brave": home / "Library/Application Support/BraveSoftware/Brave-Browser",
        }
        key = _mac_key()
    elif sys.platform == "win32":
        loc = Path(os.environ.get("LOCALAPPDATA", ""))
        roots = {
            "chrome": loc / "Google/Chrome/User Data",
            "edge": loc / "Microsoft/Edge/User Data",
        }
        key = _win_key()
    else:
        roots = {"chrome": home / ".config/google-chrome"}
        key = pbkdf2_hmac("sha1", b"peanuts", SALT, 1003, 16)

    root = roots.get(browser)
    if not root or not root.exists():
        return {}

    out: dict[str, str] = {}
    for db in _cookie_dbs(root):
        tmp = Path(tempfile.mkdtemp()) / "cookies.db"
        try:
            shutil.copy2(db, tmp)
        except Exception:
            continue
        try:
            con = sqlite3.connect(str(tmp))
            rows = con.execute(
                "select host_key, name, encrypted_value, value from cookies where host_key like ?",
                (f"%{domain}%",)).fetchall()
            con.close()
        except Exception:
            continue
        for host, name, enc, plain in rows:
            if plain:
                out[name] = plain
                continue
            val = _decrypt(bytes(enc or b""), key)
            if val:
                out[name] = val
        if out:
            break
    return out

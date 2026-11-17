"""授权校验：对接 license.moneyfly.club。

- `machine_code()`：稳定的设备码（每台机器不同，重装系统可能变化）
  * Windows：注册表 MachineGuid
  * macOS：IOPlatformUUID
  * Linux：/etc/machine-id
  * 兜底：hostname + CPU 架构 + 平台
- `verify()`：POST /api/v1/license/verify；服务端返回 `authorized` 才是真授权，
  之前只看 `ok`（请求成功）会把未登记设备误判为已授权。
"""
from __future__ import annotations

import base64
import hashlib
import platform
import subprocess

import requests

# 授权凭证验签公钥（服务端 /www/server/ae-license/storage/keys/public.pem）
# 服务端换密钥时，可用配置 license_public_key 覆盖，不必改代码。
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAECRxN/jFtIcSEazrgsUtZao9rOlrp
p52qNtmsur5XzvPts4jXa0EAuRCQ8He9ijtSFyscFmIDTtsTsLlSczc1tg==
-----END PUBLIC KEY-----"""

CLIENT_TYPE = "local"


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "").strip()
    except Exception:
        return ""


def _raw_identity() -> str:
    system = platform.system()
    raw = ""
    if system == "Windows":
        out = _run(["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"])
        for line in out.splitlines():
            if "MachineGuid" in line:
                raw = line.split()[-1].strip()
                break
    elif system == "Darwin":
        out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                raw = line.split("=")[-1].strip().strip('"')
                break
    else:
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                with open(p, encoding="utf-8") as f:
                    raw = f.read().strip()
                if raw:
                    break
            except Exception:
                continue
    if not raw:
        raw = f"{platform.node()}|{platform.machine()}|{system}"
    return raw


def machine_id() -> str:
    """64 位十六进制设备指纹（服务端 identity 用）。"""
    return hashlib.sha256(_raw_identity().encode()).hexdigest()


def machine_code(fmt: bool = True) -> str:
    """给用户看的机器码：XXXXXXXX-XXXXXXXX（与授权后台现有登记格式一致）。"""
    raw = machine_id()[:16].upper()
    if not fmt:
        return raw
    return f"{raw[:8]}-{raw[8:16]}"


def _b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(str(s) + "=" * (-len(str(s)) % 4))


def verify_voucher(voucher: str, signature: str, identity: str,
                   client_type: str = CLIENT_TYPE, public_key_pem: str = "") -> bool:
    """校验服务端签发的授权凭证（ECDSA P-256 / SHA-256，raw r||s）。"""
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import ECC
    from Crypto.Signature import DSS
    payload = _b64url(voucher)
    parts = payload.decode("utf-8", "ignore").split("|")
    if len(parts) != 4:
        return False
    if parts[0] != identity:
        return False
    if parts[1] and parts[1] < __import__("datetime").date.today().isoformat():
        return False
    if parts[3] != client_type:
        return False
    key = ECC.import_key(public_key_pem or PUBLIC_KEY_PEM)
    DSS.new(key, "fips-186-3", encoding="binary").verify(SHA256.new(payload), _b64url(signature))
    return True


def _identity_candidates() -> list[str]:
    """设备标识候选（兼容不同版本/不同登记格式）：

    1) 旧版 16 位大写（现有后台里可能已登记的就是这个）
    2) 界面显示的 16 位带横线（管理员照客户复制的码登记时用这个）
    3) 64 位完整哈希（后台直接登记完整 identity 时用这个）
    """
    full = machine_id()
    short = full[:16].upper()
    # 顺序：后台现有格式（8-8）→ 无横线 → 完整哈希
    out = [machine_code(), short, full]
    seen = set()
    return [x for x in out if x and not (x in seen or seen.add(x))]


def verify(server: str, client_type: str = CLIENT_TYPE, timeout: int = 10,
           version: str = "", identity_override: str = "",
           public_key_pem: str = "") -> dict:
    """按多种设备标识格式依次校验，任一通过即算授权（向后兼容）。

    - identity_override：客户从老软件复制过来的旧授权 ID，优先尝试；
    - public_key_pem：服务端凭证公钥（可覆盖内置公钥）。
    """
    import platform
    last = {}
    tried = []
    candidates = [identity_override.strip()] if identity_override else []
    candidates += [x for x in _identity_candidates() if x not in candidates]
    for identity in candidates:
        tried.append(identity)
        payload = {
            "identity": identity,
            "machine_code": identity,
            "client_type": client_type,
            "platform": platform.system(),
            "machine_name": platform.node(),
            "version": version,
        }
        try:
            r = requests.post(f"{server.rstrip('/')}/api/v1/license/verify", json=payload, timeout=timeout)
            try:
                data = r.json()
            except Exception:
                data = {}
            authorized = bool(data.get("authorized"))
            if "authorized" not in data and r.status_code == 200:
                authorized = bool(data.get("ok")) and "未授权" not in str(data.get("message", ""))
            msg = str(data.get("message") or "")
            if authorized:
                voucher = str(data.get("voucher") or "")
                signature = str(data.get("signature") or "")
                ctype = str(data.get("client_type") or client_type)
                if voucher and signature:
                    try:
                        if not verify_voucher(voucher, signature, identity, ctype, public_key_pem):
                            raise ValueError("凭证内容不匹配")
                    except Exception as _e:  # noqa: BLE001
                        last = {"ok": False, "authorized": False,
                                "message": f"授权凭证验签失败：{str(_e)[:60]}",
                                "expiry": "", "identity": identity, "machine_code": identity,
                                "server_time": "", "tried": tried}
                        continue
                return {
                    "ok": True, "authorized": True, "message": "授权有效",
                    "expiry": str(data.get("expiry") or data.get("expire_at") or ""),
                    "identity": identity, "machine_code": identity,
                    "client_type": ctype, "plan": str(data.get("plan") or ""),
                    "issued_at": str(data.get("issued_at") or ""),
                    "server_time": str(data.get("server_time") or ""),
                    "voucher": voucher, "signature": signature,
                    "tried": tried,
                }
            last = {
                "ok": False, "authorized": False,
                "message": msg or (f"授权服务器返回 HTTP {r.status_code}" if r.status_code >= 400 else "未授权"),
                "expiry": str(data.get("expiry") or ""), "identity": identity,
                "machine_code": identity, "server_time": str(data.get("server_time") or ""),
                "tried": tried,
            }
        except Exception as e:  # noqa: BLE001
            last = {"ok": False, "authorized": False,
                    "message": f"无法连接授权服务器：{str(e)[:80]}",
                    "expiry": "", "identity": identity, "machine_code": identity,
                    "server_time": "", "tried": tried}
            break
    if not last:
        last = {"ok": False, "authorized": False, "message": "未授权", "expiry": "",
                "identity": machine_code(), "machine_code": machine_code(), "tried": tried}
    return last

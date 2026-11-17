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

import hashlib
import platform
import subprocess

import requests

CLIENT_TYPE = "ae-publisher"


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
    """给用户看的机器码：XXXX-XXXX-XXXX-XXXX（每台机器唯一）。"""
    raw = machine_id()[:16].upper()
    if not fmt:
        return raw
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def verify(server: str, client_type: str = CLIENT_TYPE, timeout: int = 10,
           version: str = "") -> dict:
    """返回 {ok(=authorized), authorized, message, expiry, identity, machine_code, server_time}。"""
    identity = machine_id()
    payload = {
        "identity": identity,
        "machine_code": machine_code(),
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
        # 兼容后端未返回 authorized 的情况：按 ok + message 判断
        if "authorized" not in data and r.status_code == 200:
            authorized = bool(data.get("ok")) and "未授权" not in str(data.get("message", ""))
        msg = str(data.get("message") or "")
        if authorized:
            msg = "授权有效"
        elif r.status_code >= 400 and not msg:
            msg = f"授权服务器返回 HTTP {r.status_code}"
        elif not msg:
            msg = "未授权"
        return {
            "ok": authorized,
            "authorized": authorized,
            "message": msg,
            "expiry": str(data.get("expiry") or data.get("expire_at") or ""),
            "identity": identity,
            "machine_code": machine_code(),
            "server_time": str(data.get("server_time") or ""),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "authorized": False,
                "message": f"无法连接授权服务器：{str(e)[:80]}",
                "expiry": "", "identity": identity, "machine_code": machine_code(),
                "server_time": ""}

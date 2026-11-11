"""授权校验：对接 license.moneyfly.club。"""
from __future__ import annotations

import hashlib
import platform
import subprocess

import requests


def machine_id() -> str:
    """生成稳定的设备标识。"""
    raw = f"{platform.node()}-{platform.machine()}-{platform.system()}"
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                if "MachineGuid" in line:
                    raw = line.split()[-1]
                    break
    except Exception:
        pass
    return hashlib.sha256(raw.encode()).hexdigest()[:16].upper()


def verify(server: str, client_type: str = "ae-publisher", timeout: int = 10) -> dict:
    """返回 {ok, message, expiry}。"""
    identity = machine_id()
    try:
        r = requests.post(
            f"{server.rstrip('/')}/api/v1/license/verify",
            json={"identity": identity, "client_type": client_type},
            timeout=timeout,
        )
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            return {"ok": True, "message": "授权有效", "expiry": data.get("expiry", "")}
        return {"ok": False, "message": data.get("message", "授权无效"), "expiry": ""}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"无法连接授权服务器: {e}", "expiry": ""}

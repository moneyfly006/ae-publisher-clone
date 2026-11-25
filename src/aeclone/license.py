"""机器码 + 授权网站（账号制 v3）。

- `machine_code()`：稳定的设备码（多硬件组合），登录时随 `software_login` 上报，
  服务端据此自动绑机并判断设备上限。
  * Windows：MachineGuid + 主板/整机 UUID
  * macOS：IOPlatformUUID / 序列号
  * Linux：/etc/machine-id 等
  * 兜底：hostname + CPU 架构 + 平台
- 网站地址：授权中心（注册 / 忘记密码 / 购买套餐 / 设备管理）。

契约 §10：旧版设备授权码 / ECDSA voucher / `/api/v1/license/verify` / 旧机器码兼容
已全部移除，授权只认账号登录后下发的签名 token。
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
from urllib.parse import urlencode

DEFAULT_SITE = "https://license.moneyfly.club"


def _run(cmd: list[str], timeout: int = 5) -> str:
    """执行系统命令拿机器码。

    Windows 上必须走 CREATE_NO_WINDOW：否则每次进「设置」页读 MachineGuid
    都会闪一个黑框（用户看到的就是“弹窗一闪而逝”）。
    """
    try:
        from .core.proc import run as _hidden_run
        p = _hidden_run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "").strip()
    except Exception:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return (p.stdout or "").strip()
        except Exception:
            return ""


def _clean_component(value: str) -> str:
    value = str(value or "").strip().strip('"').strip()
    low = value.lower()
    if not value or low in {
        "none", "null", "unknown", "default string", "system serial number",
        "to be filled by o.e.m.", "not applicable", "not specified",
        "00000000-0000-0000-0000-000000000000",
        "03000200-0400-0500-0006-000700080009",
    }:
        return ""
    return value


def _identity_components() -> list[str]:
    """Collect multiple stable hardware identifiers.

    A cloned VM/image often has the same MachineGuid/machine-id but different
    BIOS/board serial, so combine several identifiers instead of trusting one.
    """
    system = platform.system()
    values: list[str] = []
    if system == "Windows":
        out = _run(["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"])
        for line in out.splitlines():
            if "MachineGuid" in line:
                values.append(_clean_component(line.split()[-1]))
                break
        for cmd in (
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_ComputerSystemProduct).UUID"],
            ["wmic", "csproduct", "get", "UUID", "/value"],
        ):
            out = _run(cmd, timeout=8)
            if not out:
                continue
            if "=" in out:
                out = out.split("=", 1)[-1].strip()
            if _clean_component(out):
                values.append(_clean_component(out))
                break
    elif system == "Darwin":
        out = _run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        if not out:
            out = _run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                values.append(_clean_component(line.split("=")[-1]))
            elif "IOPlatformSerialNumber" in line:
                values.append(_clean_component(line.split("=")[-1]))
            elif '"model"' in line or '"board-id"' in line:
                values.append(_clean_component(line.split("=")[-1]))
    else:
        for path in ("/etc/machine-id", "/var/lib/dbus/machine-id",
                     "/sys/class/dmi/id/product_uuid", "/sys/class/dmi/id/board_serial",
                     "/sys/class/dmi/id/product_serial"):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    values.append(_clean_component(fh.read().strip()))
            except OSError:
                continue
    # Last resort: hostname/arch are weak and can duplicate in cloned environments.
    if not any(values):
        values = [str(platform.node()), str(platform.machine()), system]
    # Deduplicate, keep order; no transient MAC here (adapter changes would reissue licenses).
    out = []
    seen = set()
    for v in values:
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _raw_identity() -> str:
    """New composite identity; unique across common cloned VM templates."""
    return "|".join(_identity_components())


def machine_id() -> str:
    """Composite device hash (multi-hardware identifiers)."""
    return hashlib.sha256(_raw_identity().encode()).hexdigest()


def machine_code(fmt: bool = True) -> str:
    """给用户看的机器码：XXXXXXXX-XXXXXXXX（多硬件组合）。"""
    raw = machine_id()[:16].upper()
    if not fmt:
        return raw
    return f"{raw[:8]}-{raw[8:16]}"


def canonical_server(server: str) -> str:
    """归一化授权服务器地址（兼容旧版 ali.moneyfly.club/license 别名）。

    旧地址在服务端是 308 跳转，而且 Location 不带原 query（api.php?action=... 会丢），
    是「授权 404 / 未知操作」的根因。正式授权中心是 license.moneyfly.club。
    """
    raw = str(server or "").strip()
    if not raw:
        return raw
    try:
        from urllib.parse import urlparse
        p = urlparse(raw if "://" in raw else "https://" + raw)
        host = (p.hostname or "").lower()
        path = (p.path or "/").rstrip("/")
        if host == "ali.moneyfly.club" and path in ("/license", ""):
            return DEFAULT_SITE
    except Exception:
        pass
    return raw.rstrip("/")


def site_url(server: str = "") -> str:
    """授权中心网站地址（注册/登录/购买/账号中心）。"""
    return canonical_server(server or DEFAULT_SITE).rstrip("/")


def register_url(server: str = "") -> str:
    return site_url(server) + "/register"


def forgot_url(server: str = "") -> str:
    return site_url(server) + "/forgot"


def devices_url(server: str = "") -> str:
    return site_url(server) + "/account/devices"


def pricing_url(server: str = "", platform: str = "", machine: str = "",
                email: str = "") -> str:
    """购买套餐链接；带上平台/机器码/邮箱便于网站预填并自动绑机。"""
    params = {}
    if str(platform or "").strip():
        params["platform"] = str(platform).strip()
    if str(machine or "").strip():
        params["machine"] = str(machine).strip()
    if str(email or "").strip():
        params["email"] = str(email).strip()
    url = site_url(server) + "/pricing"
    return url + ("?" + urlencode(params) if params else "")

"""授权网站（license server）接口：按机器码拉取平台+功能包 token。

服务端约定（任一实现即可）：

    POST {server}/api/license/entitlements
      body: {"machine_code": "...", "version": "1.0.5", "product": "AuroraList"}
      resp: {"code":0, "data":{"token": "<signed entitlement token>"}}

也兼容 GET ?machine_code=...&version=... 以及直接返回 {"token": ...}。
客户端拿到 token 后仍会本地验签（licensing.verify_token），服务端被劫持也无法伪造平台。
"""
from __future__ import annotations

import json
import logging
import time

import requests

from .licensing import (Entitlements, apply_license_token, load_entitlements,
                        verify_token)

log = logging.getLogger(__name__)

PATHS = ("/api/license/entitlements", "/api/entitlement",
         "/api/license/platforms", "/api/version/entitlements")


def _extract_token(data) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("token", "entitlement_token", "entitlements_token"):
        if data.get(key):
            return str(data[key])
    inner = data.get("data")
    if isinstance(inner, dict):
        return _extract_token(inner)
    return ""


def fetch_entitlement_token(server: str, machine_code: str, version: str = "",
                            product: str = "AuroraList", timeout: int = 8,
                            session=None) -> tuple[str, str]:
    """返回 (token, error)。token 为空表示失败。"""
    base = str(server or "").rstrip("/")
    if not base:
        return "", "未配置授权服务器"
    s = session or requests.Session()
    payload = {"machine_code": str(machine_code or ""), "version": version,
               "product": product}
    last_err = ""
    for path in PATHS:
        url = base + path
        try:
            r = s.post(url, json=payload, timeout=timeout)
            if r.status_code < 400:
                tok = _extract_token(r.json())
                if tok:
                    return tok, ""
                last_err = f"{url}: 响应里没有 token"
            else:
                last_err = f"{url}: HTTP {r.status_code}"
        except Exception as ex:
            last_err = f"{url}: {str(ex)[:80]}"
        try:
            r = s.get(url, params={"machine_code": payload["machine_code"],
                                   "version": version, "product": product},
                      timeout=timeout)
            if r.status_code < 400:
                tok = _extract_token(r.json())
                if tok:
                    return tok, ""
        except Exception:
            pass
    return "", last_err or "授权服务器不可达"


def refresh_entitlements(cfg, version: str = "", timeout: int = 8,
                         save=None, session=None) -> Entitlements:
    """向授权服务器刷新 token；验签成功才写入配置。

    失败时保留本地缓存（7 天宽限由 licensing.load_entitlements 控制）。
    """
    try:
        from .license import machine_code
        mcode = machine_code()
    except Exception:
        mcode = str(getattr(cfg, "license_identity", "") or "")
    server = str(getattr(cfg, "license_server", "") or "")
    tok, err = fetch_entitlement_token(server, mcode, version=version,
                                       session=session, timeout=timeout)
    if not tok:
        log.info("授权服务器未返回 token：%s", err)
        return load_entitlements(cfg)
    payload = verify_token(tok)
    if not payload:
        log.warning("授权服务器返回的 token 验签失败，忽略")
        return load_entitlements(cfg)
    machine_ok = str(payload.get("machine_code") or "") in ("", mcode)
    if not machine_ok:
        log.warning("token 机器码不匹配：%s != %s", payload.get("machine_code"), mcode)
        return load_entitlements(cfg)
    ent = apply_license_token(cfg, tok, save=save)
    cfg.set("license_token_ts", int(time.time()))
    if save:
        save(cfg)
    log.info("平台授权已刷新：%s", ", ".join(ent.platforms_list()) or "无")
    return ent

def software_login(server: str, email: str, password: str, machine_code: str,
                   version: str = "", timeout: int = 10) -> tuple[str, str, dict]:
    """软件登录：账号密码 + 机器码 → (token, error, data)。

    服务器校验账号与授权，未绑定时自动绑定该机器，返回绑定机器码的 token。
    """
    base = str(server or "").rstrip("/")
    if not base:
        return "", "未配置授权服务器", {}
    try:
        r = requests.post(base + "/api.php?action=software_login",
                          json={"email": email, "password": password,
                                "machine_code": machine_code, "version": version},
                          timeout=timeout)
        d = r.json()
    except Exception as ex:
        return "", f"连接授权服务器失败：{str(ex)[:80]}", {}
    if d.get("code") != 0:
        return "", str(d.get("msg") or "登录失败"), {}
    data = d.get("data") or {}
    token = str(data.get("token") or "")
    if not token:
        return "", "服务器未返回授权", {}
    if not verify_token(token):
        return "", "授权 token 验签失败", {}
    return token, "", data

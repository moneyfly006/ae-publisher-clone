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
from .license import canonical_server, shop_url

log = logging.getLogger(__name__)

PATHS = ("/api/license/entitlements", "/api/entitlement",
         "/api/license/platforms", "/api/version/entitlements")

# 旧授权服务器（ali.moneyfly.club/license）在服务端 308 到 /shop，且会丢 query；
# 这里统一走 canonical_server()，并对 308 手动跟随、保留原查询串。
_MAX_REDIRECTS = 4


def _request_keep_query(session, method: str, url: str, *, timeout: int = 10, **kw):
    """发送请求并手动跟随 3xx；Location 缺 query 时补回原 query。

    requests 跟随 308 时若 Location 没有 query，会把 api.php?action=... 丢掉，
    服务端于是返回「未知操作」。这里显式保留查询串。
    """
    from urllib.parse import urljoin, urlsplit

    current = url
    resp = session.request(method, current, timeout=timeout, allow_redirects=False, **kw)
    for _ in range(_MAX_REDIRECTS):
        if resp.status_code not in (301, 302, 303, 307, 308):
            break
        loc = resp.headers.get("location") or resp.headers.get("Location") or ""
        if not loc:
            break
        nxt = urljoin(current, loc)
        old_q = urlsplit(current).query
        if old_q and "?" not in nxt:
            nxt = nxt + "?" + old_q
        current = nxt
        resp = session.request(method, current, timeout=timeout, allow_redirects=False, **kw)
    return resp


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
    base = canonical_server(str(server or "")).rstrip("/")
    if not base:
        return "", "未配置授权服务器"
    s = session or requests.Session()
    payload = {"machine_code": str(machine_code or ""), "version": version,
               "product": product}
    last_err = ""
    empty_ok = ""          # 服务端 200 但没给 token（机器未绑定授权账号/套餐）
    tried = []
    for path in PATHS:
        url = base + path
        try:
            r = _request_keep_query(s, "POST", url, json=payload, timeout=timeout)
            tried.append(f"POST {url} -> {r.status_code}")
            if r.status_code < 400:
                tok = _extract_token(r.json())
                if tok:
                    return tok, ""
                try:
                    msg = str((r.json() or {}).get("msg") or "")
                except Exception:
                    msg = ""
                empty_ok = msg or "ok"
            else:
                last_err = f"{url}: HTTP {r.status_code}"
        except Exception as ex:
            last_err = f"{url}: {str(ex)[:80]}"
        try:
            r = _request_keep_query(s, "GET", url, timeout=timeout,
                                    params={"machine_code": payload["machine_code"],
                                            "version": version, "product": product})
            tried.append(f"GET {url} -> {r.status_code}")
            if r.status_code < 400:
                tok = _extract_token(r.json())
                if tok:
                    return tok, ""
        except Exception:
            pass
    if empty_ok:
        return "", (f"授权服务器未返回 token（服务端：{empty_ok}）：机器码 "
                    f"{machine_code} 未绑定授权账号/套餐。请在 {shop_url(base)} "
                    "登录授权中心开通，或到「设置 → 平台授权 Token」粘贴 token")
    return "", (last_err or "授权服务器不可达")


def refresh_entitlements(cfg, version: str = "", timeout: int = 8,
                         save=None, session=None) -> Entitlements:
    """向授权服务器刷新 token；验签成功才写入配置。

    失败时保留本地缓存（7 天宽限由 licensing.load_entitlements 控制）。
    """
    try:
        from .license import machine_code, identity_candidates
        mcode = machine_code()
        allowed = set(identity_candidates())
    except Exception:
        mcode = str(getattr(cfg, "license_identity", "") or "")
        allowed = {mcode}
    server = str(getattr(cfg, "license_server", "") or "")
    tok, err = fetch_entitlement_token(server, mcode, version=version,
                                       session=session, timeout=timeout)
    if not tok:
        # 旧账号可能仍绑定旧机器码：升级后自动尝试旧格式，避免授权丢失。
        try:
            from .license import legacy_machine_code
            _legacy = legacy_machine_code()
        except Exception:
            _legacy = ""
        if _legacy and _legacy != mcode:
            tok2, err2 = fetch_entitlement_token(server, _legacy, version=version,
                                                 session=session, timeout=timeout)
            if tok2:
                tok, err = tok2, ""
        if not tok:
            log.info("授权服务器未返回 token：%s", err)
            return load_entitlements(cfg)
    payload = verify_token(tok)
    if not payload:
        log.warning("授权服务器返回的 token 验签失败，忽略")
        return load_entitlements(cfg)
    machine_ok = str(payload.get("machine_code") or "") in ("", *allowed)
    if not machine_ok:
        log.warning("token 机器码不匹配：%s != %s", payload.get("machine_code"), mcode)
        return load_entitlements(cfg)
    ent = apply_license_token(cfg, tok, save=save)
    cfg.set("license_token_ts", int(time.time()))
    if save:
        save(cfg)
    log.info("平台授权已刷新：%s", ", ".join(ent.platforms_list()) or "无")
    return ent

def software_bind(server: str, email: str, password: str, machine_code: str,
                  legacy_identities: list[str] | None = None, version: str = "",
                  timeout: int = 10, session=None) -> tuple[str, str, dict]:
    """旧授权迁移：邮箱/密码 + 旧授权 identity → 绑定本机并下发全平台 token。"""
    base = canonical_server(str(server or "")).rstrip("/")
    if not base:
        return "", "未配置授权服务器", {}
    try:
        sess = session or requests.Session()
        url = base + "/api.php?action=software_bind"
        payload = {"email": email, "password": password, "machine_code": machine_code,
                   "legacy_identity": (legacy_identities or [""])[0],
                   "legacy_identities": list(legacy_identities or []),
                   "version": version}
        r = _request_keep_query(sess, "POST", url, timeout=timeout, json=payload)
        d = r.json()
    except Exception as ex:
        return "", f"连接授权服务器失败：{str(ex)[:80]}", {}
    if d.get("code") != 0:
        msg = str(d.get("msg") or "绑定失败")
        # 后台还未部署 software_bind 时，已有账号可退回普通软件登录。
        if "未知操作" in msg or "不支持" in msg or "not found" in msg.lower():
            return software_login(server, email, password, machine_code,
                                  version=version, timeout=timeout, session=session)
        return "", msg, {}
    data = d.get("data") or {}
    token = str(data.get("token") or "")
    if not token:
        return "", "服务器未返回授权", {}
    if not verify_token(token):
        return "", "授权 token 验签失败", {}
    return token, "", data


def software_login(server: str, email: str, password: str, machine_code: str,
                   version: str = "", timeout: int = 10, session=None) -> tuple[str, str, dict]:
    """软件登录：账号密码 + 机器码 → (token, error, data)。

    服务器校验账号与授权，未绑定时自动绑定该机器，返回绑定机器码的 token。
    """
    base = canonical_server(str(server or "")).rstrip("/")
    if not base:
        return "", "未配置授权服务器", {}
    try:
        sess = session or requests.Session()
        url = base + "/api.php?action=software_login"
        r = _request_keep_query(sess, "POST", url, timeout=timeout,
                                json={"email": email, "password": password,
                                      "machine_code": machine_code, "version": version})
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


def software_logout(server: str, email: str = "", machine_code: str = "",
                    timeout: int = 6, session=None) -> bool:
    """软件退出账号：通知后台记录；失败时客户端仍可本地退出。"""
    base = canonical_server(str(server or "")).rstrip("/")
    if not base:
        return False
    try:
        sess = session or requests.Session()
        r = _request_keep_query(sess, "POST", base + "/api.php?action=software_logout",
                                timeout=timeout,
                                json={"email": email, "machine_code": machine_code})
        return r.status_code < 400
    except Exception:
        return False

"""授权网站（license server）接口：账号登录 + token 刷新（账号制 v3）。

契约 §7.1（软件端，无会话，凭邮箱密码 + 机器码；保留旧字段名）：

    POST {server}/api.php?action=software_login
      body: {"email","password","machine_code","version"}
      resp: {"code":0,"data":{"token","user":{"id","email","name"},
                              "machine_code","platforms":[...],"expires_at","device_limit"}}
      code=1 → msg 为中文错误（密码错误 / 禁用 / 设备冲突 / 超限）。

    POST {server}/api.php?action=software_logout  {"email","machine_code"} → ok（不解绑）

    POST {server}/api.php?action=entitlement      {"machine_code"} → {"token"}（刷新用）

客户端拿到 token 后仍会本地验签（licensing.verify_token），服务端被劫持也无法伪造权限。
旧版 `software_bind` / `/api/v1/license/verify` 已删除。
"""
from __future__ import annotations

import logging
import time

import requests

from .licensing import (Entitlements, apply_license_token, load_entitlements,
                        verify_token)
from .license import canonical_server, machine_code as local_machine_code

log = logging.getLogger(__name__)

# 兼容旧客户端的 token 拉取入口（新客户端仅在登录成功后用它刷新 token）
PATHS = ("/api/license/entitlements", "/api/entitlement", "/api/license/platforms")

# 旧授权服务器（ali.moneyfly.club/license）在服务端 308 到 /shop，且会丢 query；
# 这里统一走 canonical_server()，并对 308 手动跟随、保留原查询串。
_MAX_REDIRECTS = 4

NETWORK_ERROR_MESSAGE = "网络不可达：无法连接授权服务器，请检查网络后重试"
SERVER_ERROR_MESSAGE = "授权服务器响应异常，请稍后重试或联系客服"
# 后端尚未部署 software_devices / software_unbind 时的兼容提示
UNSUPPORTED_MESSAGE = "服务端暂未支持该功能，请到网页端管理设备"
UNAVAILABLE_MESSAGE = "无法获取设备列表，请稍后重试或到网页端管理"

# 服务端「未知操作」类提示（后端旧版本 / 路由未上线）
_UNKNOWN_ACTION_HINTS = ("未知操作", "不支持的操作", "未知的", "unknown action",
                         "invalid action", "action not found", "not supported")

# 登录失败分类（客户端按类型给出明确中文提示）
LOGIN_ERROR_KINDS = {
    "credentials": "邮箱或密码错误",
    "device_conflict": "该设备已绑定其他账号，请联系客服解绑后再登录",
    "device_limit": "设备数量已达上限，请先在账号中心解绑其他设备",
    "disabled": "账号已被禁用，请联系客服",
    "rate_limited": "尝试次数过多，请 10 分钟后再试",
    "machine": "授权与本机不匹配，请重新登录",
    "token": "授权校验失败，请稍后重试或联系客服",
    "network": NETWORK_ERROR_MESSAGE,
    "server": SERVER_ERROR_MESSAGE,
}


def classify_login_error(msg: str) -> tuple[str, str]:
    """把服务端/网络错误归到固定类型并返回给用户的中文文案。

    返回 (kind, display_message)；kind 见 LOGIN_ERROR_KINDS。
    """
    raw = str(msg or "").strip()
    text = raw or LOGIN_ERROR_KINDS["server"]
    if text == NETWORK_ERROR_MESSAGE or "无法连接授权服务器" in text:
        return "network", NETWORK_ERROR_MESSAGE
    if "已绑定其他账号" in text or "已绑定其它账号" in text or "绑定其他" in text:
        return "device_conflict", text
    if "上限" in text or "设备数量" in text:
        return "device_limit", text
    if "禁用" in text:
        return "disabled", text
    if "尝试次数" in text:
        return "rate_limited", text
    if ("密码" in text or "邮箱" in text) and ("错误" in text or "不正确" in text
                                              or "不存在" in text or "未注册" in text):
        return "credentials", text
    if "验签" in text or "token" in text.lower():
        return "token", text
    return "server", text


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


def _norm_machine(value: str) -> str:
    return str(value or "").strip().upper().replace("-", "")


def fetch_entitlement_token(server: str, machine_code: str, version: str = "",
                            product: str = "AuroraList", timeout: int = 8,
                            session=None) -> tuple[str, str]:
    """按机器码拉取 token（登录后刷新用）。返回 (token, error)。"""
    base = canonical_server(str(server or "")).rstrip("/")
    if not base:
        return "", "未配置授权服务器"
    s = session or requests.Session()
    payload = {"machine_code": str(machine_code or ""), "version": version,
               "product": product}
    last_err = ""
    empty_ok = ""          # 服务端 200 但没给 token（该机器未绑定账号/无套餐）
    for path in PATHS:
        url = base + path
        try:
            r = _request_keep_query(s, "POST", url, json=payload, timeout=timeout)
            if r.status_code < 400:
                body = r.json()
                tok = _extract_token(body)
                if tok:
                    return tok, ""
                data = (body or {}).get("data") or {}
                if isinstance(data, dict) and data.get("disabled"):
                    return "", "DISABLED:账号已被禁用，请联系客服"
                if isinstance(data, dict) and data.get("bound") is False:
                    return "", "UNBOUND:该设备未绑定账号"
                try:
                    msg = str((body or {}).get("msg") or "")
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
            if r.status_code < 400:
                tok = _extract_token(r.json())
                if tok:
                    return tok, ""
        except Exception:
            pass
    if empty_ok:
        return "", ("授权服务器未返回 token：该机器码未绑定授权账号或没有有效套餐，"
                    "请在授权中心登录并购买套餐")
    return "", (last_err or "授权服务器不可达")


def refresh_entitlements(cfg, version: str = "", timeout: int = 8,
                         save=None, session=None,
                         machine: str | None = None) -> Entitlements:
    """向授权服务器刷新 token；验签+机器码匹配成功才写入配置。

    失败时保留本地缓存（7 天宽限由 licensing.load_entitlements 控制）。
    未登录（无邮箱/无 token）时不发请求。
    """
    mcode = str(machine or local_machine_code())
    if not str(getattr(cfg, "license_account_email", "") or "").strip():
        return load_entitlements(cfg, machine=mcode)
    server = str(getattr(cfg, "license_server", "") or "")
    tok, err = fetch_entitlement_token(server, mcode, version=version,
                                       session=session, timeout=timeout)
    if not tok:
        # 服务端明确返回“设备已解绑/账号被禁用”时，清掉本地 token，立即锁定平台。
        # （其它错误如网络失败/服务器异常仍保留 7 天离线宽限，避免断网误锁。）
        if str(err).startswith("UNBOUND:") or str(err).startswith("DISABLED:"):
            reason = str(err).split(":", 1)[1]
            log.warning("授权已失效（%s），清除本地 token", reason)
            cfg.set("license_token", "")
            cfg.set("license_token_ts", 0)
            if save:
                save(cfg)
            return load_entitlements(cfg, machine=mcode)
        log.info("授权服务器未返回 token：%s", err)
        return load_entitlements(cfg, machine=mcode)
    payload = verify_token(tok)
    if not payload:
        log.warning("授权服务器返回的 token 验签失败，忽略")
        return load_entitlements(cfg, machine=mcode)
    if _norm_machine(payload.get("machine_code")) != _norm_machine(mcode):
        log.warning("token 机器码不匹配：%s != %s", payload.get("machine_code"), mcode)
        return load_entitlements(cfg, machine=mcode)
    ent = apply_license_token(cfg, tok, save=save, machine=mcode)
    cfg.set("license_token_ts", int(time.time()))
    if save:
        save(cfg)
    log.info("平台授权已刷新：%s", ", ".join(ent.platforms_list()) or "无")
    return ent


def software_login(server: str, email: str, password: str, machine_code: str,
                   version: str = "", timeout: int = 10, session=None,
                   os_version: str = "", machine_name: str = "",
                   store_name: str = "") -> tuple[str, str, dict]:
    """账号登录：邮箱/密码 + 机器码 → (token, error, data)。

    服务端校验账号与套餐，未绑定时自动绑定该机器（设备数上限/跨账号限制在服务端）。
    `os_version/machine_name/store_name` 会随登录上报，后台设备列表才有环境信息
    （不传也能登录，只是设备行这几个字段为空）。
    失败返回已分类的中文错误（data["error_kind"] 给出类型，便于 UI 区分重试/去购买）。
    """
    base = canonical_server(str(server or "")).rstrip("/")
    if not base:
        return "", "未配置授权服务器，请联系客服", {"error_kind": "server"}
    email_v = str(email or "").strip().lower()
    password_v = str(password or "")
    if not email_v or not password_v:
        return "", "请输入邮箱和密码", {"error_kind": "credentials"}
    try:
        sess = session or requests.Session()
        url = base + "/api.php?action=software_login"
        body = {"email": email_v, "password": password_v,
                "machine_code": str(machine_code or ""), "version": version}
        # 设备环境信息（可选）：后端只在传了字段时才写入 devices 行
        for key, value in (("os", os_version), ("machine_name", machine_name),
                           ("store_name", store_name)):
            if str(value or "").strip():
                body[key] = str(value).strip()[:120]
        r = _request_keep_query(sess, "POST", url, timeout=timeout, json=body)
        if r.status_code >= 500:
            return "", SERVER_ERROR_MESSAGE, {"error_kind": "server"}
        d = r.json()
    except requests.exceptions.RequestException:
        return "", NETWORK_ERROR_MESSAGE, {"error_kind": "network"}
    except Exception as ex:  # noqa: BLE001
        return "", f"{SERVER_ERROR_MESSAGE}（{str(ex)[:60]}）", {"error_kind": "server"}
    if not isinstance(d, dict):
        return "", SERVER_ERROR_MESSAGE, {"error_kind": "server"}
    if d.get("code") != 0:
        kind, msg = classify_login_error(str(d.get("msg") or ""))
        return "", msg, {"error_kind": kind}
    data = d.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    token = str(data.get("token") or "")
    if not token:
        return "", "服务器未返回授权信息，请联系客服", {"error_kind": "server"}
    payload = verify_token(token)
    if not payload:
        return "", LOGIN_ERROR_KINDS["token"], {"error_kind": "token"}
    if _norm_machine(payload.get("machine_code")) != _norm_machine(machine_code):
        return "", LOGIN_ERROR_KINDS["machine"], {"error_kind": "machine"}
    return token, "", data


def _is_unknown_action(msg: str) -> bool:
    text = str(msg or "").lower()
    return any(hint.lower() in text for hint in _UNKNOWN_ACTION_HINTS)


def _post_client_action(server: str, action: str, payload: dict, *,
                        timeout: int = 10, session=None) -> tuple[dict, str, str]:
    """POST {server}/api.php?action=<action>（软件端无会话接口）。

    返回 (resp, error_message, error_kind)：
    - 成功：resp={"data":..., "msg":...}, error=""
    - 失败：error 为可直接展示的中文，kind ∈
      network/server/credentials/unsupported/device/...（见 classify_login_error）
    """
    base = canonical_server(str(server or "")).rstrip("/")
    if not base:
        return {}, "未配置授权服务器，请联系客服", "server"
    try:
        sess = session or requests.Session()
        r = _request_keep_query(sess, "POST", base + f"/api.php?action={action}",
                                timeout=timeout, json=dict(payload or {}))
        if r.status_code >= 500:
            return {}, SERVER_ERROR_MESSAGE, "server"
        d = r.json()
    except requests.exceptions.RequestException:
        return {}, NETWORK_ERROR_MESSAGE, "network"
    except Exception as ex:  # noqa: BLE001
        return {}, f"{SERVER_ERROR_MESSAGE}（{str(ex)[:60]}）", "server"
    if not isinstance(d, dict):
        return {}, SERVER_ERROR_MESSAGE, "server"
    if d.get("code") != 0:
        msg = str(d.get("msg") or "").strip()
        if _is_unknown_action(msg):
            return {}, UNSUPPORTED_MESSAGE, "unsupported"
        kind, text = classify_login_error(msg)
        if any(k in msg for k in ("设备不存在", "未找到该设备", "不属于当前账号")):
            kind = "device"
        elif "不能解绑当前登录设备" in msg:
            kind = "device_local"
        return {}, (text or UNAVAILABLE_MESSAGE), kind
    return {"data": d.get("data"), "msg": str(d.get("msg") or "")}, "", ""


def _as_int(value) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def normalize_device(row: dict, current_machine: str = "") -> dict:
    """把后端 devices 行规范成客户端展示用的字段（缺字段不报错）。"""
    row = row if isinstance(row, dict) else {}
    code = str(row.get("machine_code") or "")
    active = bool(row.get("active", True))
    if "active" not in row and "status" in row:
        status = str(row.get("status") or "").lower()
        active = status in ("1", "active", "ok", "已绑定", "正常")
    is_current = bool(current_machine) and _norm_machine(code) == _norm_machine(current_machine)
    return {
        "id": _as_int(row.get("id") or row.get("device_id")),
        "machine_code": code,
        "active": active,
        "created_at": _as_int(row.get("created_at") or row.get("bound_at")),
        "updated_at": _as_int(row.get("updated_at")),
        "last_seen": _as_int(row.get("last_seen")),
        "last_ip": str(row.get("last_ip") or ""),
        "app_version": str(row.get("app_version") or ""),
        "os_version": str(row.get("os_version") or ""),
        "machine_name": str(row.get("machine_name") or ""),
        "store_name": str(row.get("store_name") or ""),
        "is_current": is_current,
        "status": "已绑定" if active else "已解绑",
    }


def software_devices(server: str, email: str, password: str, machine_code: str,
                     timeout: int = 10,
                     session=None) -> tuple[list[dict], str, dict]:
    """列出账号已绑定设备（软件端无会话接口）。

    POST /api.php?action=software_devices
      body {email,password,machine_code}
      resp {code:0,data:[{id,machine_code,active,created_at,last_seen,...}]}
    返回 (devices, error, meta)；meta 可含 error_kind/unsupported/msg。
    """
    email_v = str(email or "").strip().lower()
    password_v = str(password or "")
    if not email_v or not password_v:
        return [], "请输入账号密码后重试", {"error_kind": "credentials"}
    resp, err, kind = _post_client_action(
        server, "software_devices",
        {"email": email_v, "password": password_v,
         "machine_code": str(machine_code or "")},
        timeout=timeout, session=session)
    if err:
        return [], err, {"error_kind": kind, "unsupported": kind == "unsupported"}
    data = resp.get("data")
    rows = data if isinstance(data, list) else []
    if isinstance(data, dict):
        rows = data.get("devices") or data.get("rows") or []
    devices = [normalize_device(r, machine_code) for r in rows if isinstance(r, dict)]
    return devices, "", {"error_kind": "", "count": len(devices),
                         "msg": resp.get("msg") or ""}


def software_unbind_device(server: str, email: str, password: str, machine_code: str,
                           device_id: int, timeout: int = 10,
                           session=None) -> tuple[bool, str, dict]:
    """解绑指定设备（本机请勿调用；服务端也会拒绝/本机禁止由 UI 拦截）。

    POST /api.php?action=software_unbind
      body {email,password,machine_code,device_id} → {code:0,msg:"设备已解绑"}
    返回 (ok, error, meta)。
    """
    email_v = str(email or "").strip().lower()
    password_v = str(password or "")
    if not email_v or not password_v:
        return False, "请输入账号密码后重试", {"error_kind": "credentials"}
    try:
        device_id_v = int(device_id or 0)
    except Exception:
        device_id_v = 0
    if device_id_v <= 0:
        return False, "设备信息不完整，请刷新后重试", {"error_kind": "device"}
    resp, err, kind = _post_client_action(
        server, "software_unbind",
        {"email": email_v, "password": password_v,
         "machine_code": str(machine_code or ""), "device_id": device_id_v},
        timeout=timeout, session=session)
    if err:
        return False, err, {"error_kind": kind, "unsupported": kind == "unsupported"}
    data = resp.get("data")
    devices = []
    if isinstance(data, list):
        devices = [normalize_device(r, machine_code) for r in data if isinstance(r, dict)]
    meta = {"error_kind": "", "msg": resp.get("msg") or "设备已解绑",
            "devices": devices}
    return True, "", meta


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

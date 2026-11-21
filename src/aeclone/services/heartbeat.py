"""软件心跳上报：把平台状态/刊登产品送到授权网站，用户端可见。"""
from __future__ import annotations

import json
import logging
import threading
import time

import requests

log = logging.getLogger(__name__)


def send_heartbeat(server: str, machine_code: str, payload: dict,
                   timeout: int = 8) -> bool:
    base = str(server or "").rstrip("/")
    if not base or not machine_code:
        return False
    try:
        r = requests.post(base + "/api.php?action=heartbeat",
                          json={"machine_code": machine_code, "payload": payload},
                          timeout=timeout)
        return r.status_code < 400 and (r.json() or {}).get("code") == 0
    except Exception as ex:  # noqa: BLE001
        log.info("心跳上报失败：%s", str(ex)[:80])
        return False


class HeartbeatLoop:
    """后台心跳线程；interval 秒一次，stop() 退出。"""

    def __init__(self, build_payload, interval: int = 120):
        self.build_payload = build_payload
        self.interval = max(30, int(interval or 120))
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
            try:
                server, machine, payload = self.build_payload()
                if server and machine and payload:
                    if send_heartbeat(server, machine, payload):
                        self.last_ok = time.time()
            except Exception as ex:  # noqa: BLE001
                log.info("心跳构建失败：%s", str(ex)[:80])
            self._stop.wait(self.interval)

"""定时发布调度：持久化任务，后台线程到点执行。"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import config_dir


def _path() -> Path:
    p = config_dir() / "schedules.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Job:
    product_id: str
    run_at: float
    title: str = ""
    status: str = "pending"      # pending | done | failed
    message: str = ""


class Scheduler:
    def __init__(self):
        self.jobs: list[Job] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load()

    def _load(self):
        p = _path()
        if p.exists():
            try:
                self.jobs = [Job(**d) for d in json.loads(p.read_text(encoding="utf-8"))]
            except Exception:
                self.jobs = []

    def save(self):
        _path().write_text(json.dumps([asdict(j) for j in self.jobs], ensure_ascii=False, indent=1),
                           encoding="utf-8")

    def add(self, product_id: str, run_at: float, title: str = "") -> Job:
        # 同一产品只保留一条待执行任务，避免重复发布
        self.jobs = [j for j in self.jobs if not (j.product_id == str(product_id) and j.status == "pending")]
        job = Job(product_id=str(product_id), run_at=run_at, title=title)
        self.jobs.append(job); self.save()
        return job

    def cancel(self, product_id: str) -> int:
        """取消某产品的待执行定时任务，返回取消条数。"""
        n = 0
        for j in self.jobs:
            if j.status == "pending" and j.product_id == str(product_id):
                j.status = "cancelled"
                j.message = "已手动取消"
                n += 1
        if n:
            self.save()
        return n

    def pending_jobs(self) -> list[Job]:
        return sorted(self.pending(), key=lambda j: j.run_at)

    def due(self) -> list[Job]:
        now = time.time()
        return [j for j in self.jobs if j.status == "pending" and j.run_at <= now]

    def pending(self) -> list[Job]:
        return [j for j in self.jobs if j.status == "pending"]

    def start(self, runner, interval: float = 30.0):
        """runner(job) 负责实际执行。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                for job in self.due():
                    try:
                        runner(job)
                        job.status = "done"
                    except Exception as e:  # noqa: BLE001
                        job.status = "failed"; job.message = str(e)[:200]
                    self.save()
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

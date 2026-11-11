"""任务队列：持久化待处理产品，支持添加/取出/完成/失败。"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import config_dir


def _path() -> Path:
    p = config_dir() / "queue.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class QueueItem:
    product_id: str
    title: str = ""
    status: str = "pending"     # pending | running | done | failed
    message: str = ""
    added_at: float = field(default_factory=time.time)


class TaskQueue:
    def __init__(self):
        self.items: list[QueueItem] = []
        self.load()

    # ---- 持久化 ----
    def load(self) -> None:
        p = _path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self.items = [QueueItem(**d) for d in data]
            except Exception:
                self.items = []

    def save(self) -> None:
        _path().write_text(json.dumps([asdict(i) for i in self.items], ensure_ascii=False, indent=2),
                           encoding="utf-8")

    # ---- 操作 ----
    def add(self, product_id: str, title: str = "") -> bool:
        if any(i.product_id == product_id and i.status in ("pending", "running") for i in self.items):
            return False
        self.items.append(QueueItem(product_id=product_id, title=title))
        self.save()
        return True

    def add_many(self, products: list[dict]) -> int:
        n = 0
        for p in products:
            pid = str(p.get("id") or p.get("productId") or "")
            if pid and self.add(pid, p.get("title") or p.get("name") or ""):
                n += 1
        return n

    def pending(self) -> list[QueueItem]:
        return [i for i in self.items if i.status == "pending"]

    def mark(self, product_id: str, status: str, message: str = "") -> None:
        for i in self.items:
            if i.product_id == product_id:
                i.status = status
                i.message = message
                break
        self.save()

    def clear_done(self) -> int:
        before = len(self.items)
        self.items = [i for i in self.items if i.status in ("pending", "running")]
        self.save()
        return before - len(self.items)

    def stats(self) -> dict:
        out = {"pending": 0, "running": 0, "done": 0, "failed": 0}
        for i in self.items:
            out[i.status] = out.get(i.status, 0) + 1
        return out

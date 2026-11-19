"""实时进度日志：所有步骤/调用/费用统一上报，UI 侧实时展示。

设计目标（对齐老软件的日志窗口）：
- 每一步都有「阶段 + 动作 + 结果 + 耗时 + 花费」
- 线程安全，任何后台线程都能 emit
- UI 只需要订阅/轮询 snapshot()，不需要关心业务
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class Event:
    ts: float
    stage: str                 # 阶段：标准化 / 白底 / 去水印 / AI标题 / 保存 …
    action: str = ""           # 动作：调用 qwen-image-2.1-turbo / 写入模板 …
    detail: str = ""           # 结果或错误
    level: str = "INFO"        # INFO / WARN / ERROR / OK
    model: str = ""
    cost: float = 0.0
    elapsed: float = 0.0       # 秒
    product: str = ""          # 关联产品 ID（可选）

    def text(self) -> str:
        parts = []
        head = f"[{self.stage}]"
        if self.product:
            head = f"[{self.product}] " + head
        if self.action:
            head += f" {self.action}"
        parts.append(head)
        if self.model:
            parts.append(f"· {self.model}")
        if self.elapsed >= 0.05:
            parts.append(f"· {self.elapsed:.1f}s")
        if self.cost > 0:
            parts.append(f"· ¥{self.cost:.3f}")
        line = " ".join(parts)
        if self.detail:
            line += f" → {self.detail}"
        return line


class ProgressBus:
    def __init__(self, limit: int = 2000):
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._limit = limit
        self._subs: list[Callable[[Event], None]] = []
        self._prices: dict[str, float] = {}
        self.stage_done = 0
        self.stage_total = 0
        self.total_cost = 0.0
        self.started_at = 0.0

    # ---------- 价格 ----------
    def set_prices(self, prices: dict[str, float]) -> None:
        with self._lock:
            self._prices.update(prices)

    def price_of(self, model: str) -> float:
        return float(self._prices.get(model, 0.0) or 0.0)

    # ---------- 事件 ----------
    def emit(self, stage: str, action: str = "", detail: str = "", level: str = "INFO",
             model: str = "", cost: float = 0.0, elapsed: float = 0.0, product: str = "") -> None:
        ev = Event(ts=time.time(), stage=stage, action=action, detail=detail, level=level,
                   model=model, cost=cost, elapsed=elapsed, product=product)
        with self._lock:
            self._events.append(ev)
            if len(self._events) > self._limit:
                self._events = self._events[-self._limit:]
            if cost:
                self.total_cost += cost
                try:
                    from . import history as _hist
                    _hist.record_cost(stage, model, cost, detail)
                except Exception:
                    pass
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                pass

    def snapshot(self, limit: int = 500) -> list[Event]:
        with self._lock:
            return list(self._events[-limit:])

    def subscribe(self, cb: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs.append(cb)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self.total_cost = 0.0
            self.stage_done = 0
            self.stage_total = 0
            self.started_at = 0.0

    # ---------- 阶段计数（进度条） ----------
    def begin(self, total_stages: int) -> None:
        with self._lock:
            self.stage_total = max(1, int(total_stages))
            self.stage_done = 0
            self.started_at = time.time()
            self.total_cost = 0.0

    def done_stage(self) -> None:
        with self._lock:
            self.stage_done += 1

    def progress(self) -> tuple[int, int, float, float]:
        """返回 (已完成, 总数, 累计花费, 已用时秒)。"""
        with self._lock:
            used = (time.time() - self.started_at) if self.started_at else 0.0
            return self.stage_done, self.stage_total, self.total_cost, used


BUS = ProgressBus()


def emit(stage: str, action: str = "", detail: str = "", level: str = "INFO",
         model: str = "", cost: float = 0.0, elapsed: float = 0.0, product: str = "") -> None:
    BUS.emit(stage, action, detail, level, model, cost, elapsed, product)


class track:
    """上下文管理器：自动计时 + 上报（成功 OK / 失败 ERROR）。

    用法：
        with track("白底", "调用模型", model=m) as t:
            url = svc.white_background(p)
            t.detail = url[:60]
    """

    def __init__(self, stage: str, action: str = "", model: str = "",
                 cost: float | None = None, product: str = ""):
        self.stage = stage
        self.action = action
        self.model = model
        self.product = product
        self.cost = BUS.price_of(model) if cost is None else cost
        self.detail = ""
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.time()
        emit(self.stage, (self.action + " 开始").strip(), "", "INFO", self.model, 0.0, 0.0, self.product)
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.time() - self.t0
        if exc is not None:
            emit(self.stage, self.action, f"{type(exc).__name__}: {exc}"[:160], "ERROR",
                 self.model, 0.0, elapsed, self.product)
            return False
        emit(self.stage, self.action, self.detail, "OK", self.model, self.cost, elapsed, self.product)
        BUS.done_stage()
        return False

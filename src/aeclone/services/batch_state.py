"""批量任务断点：记录上一次批量跑到哪、还剩哪些商品，供「继续」使用。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import config_dir


def _path() -> Path:
    p = config_dir() / "batch_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save(total: int, done: int, remaining: list[str], failed: list[str] | None = None,
         stopped: bool = False) -> None:
    """保存本次批量的进度快照。done=已处理数，remaining=还没处理的商品 ID。"""
    data = {
        "ts": int(time.time()),
        "total": int(total),
        "done": int(done),
        "remaining": [str(x) for x in (remaining or [])],
        "failed": [str(x) for x in (failed or [])],
        "stopped": bool(stopped),
    }
    try:
        _path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def clear() -> None:
    try:
        _path().unlink(missing_ok=True)
    except Exception:
        pass


def has_resume() -> bool:
    d = load()
    return bool(d.get("remaining")) or bool(d.get("failed"))


def summary() -> str:
    d = load()
    if not d:
        return ""
    total = d.get("total", 0)
    done = d.get("done", 0)
    rem = len(d.get("remaining") or [])
    fail = len(d.get("failed") or [])
    ts = time.strftime("%m-%d %H:%M", time.localtime(d.get("ts", 0))) if d.get("ts") else "-"
    return f"上次批量（{ts}）：完成 {done}/{total}，剩余 {rem}，失败 {fail}"

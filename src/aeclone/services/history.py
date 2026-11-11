"""任务历史与统计（JSONL 持久化）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import config_dir


def _path() -> Path:
    p = config_dir() / "history.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record(kind: str, ok: int, failed: int, detail: str = "") -> None:
    entry = {"ts": int(time.time()), "kind": kind, "ok": ok, "failed": failed, "detail": detail[:300]}
    with open(_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recent(n: int = 50) -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[::-1]


def stats(days: int = 1) -> dict:
    cutoff = time.time() - days * 86400
    rows = [r for r in recent(5000) if r.get("ts", 0) >= cutoff]
    return {
        "runs": len(rows),
        "ok": sum(r.get("ok", 0) for r in rows),
        "failed": sum(r.get("failed", 0) for r in rows),
    }

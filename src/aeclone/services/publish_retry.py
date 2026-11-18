"""发布失败重试清单：只记「发布失败」的商品，重试时不再跑 AI。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import config_dir


def _path() -> Path:
    p = config_dir() / "publish_failed.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        _path().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def record(product_id: str, error: str, title: str = "") -> None:
    """记录一个发布失败的商品（同 ID 会覆盖为最新错误）。"""
    d = _load()
    d[str(product_id)] = {
        "error": str(error)[:300],
        "title": str(title)[:80],
        "ts": int(time.time()),
    }
    _save(d)


def remove(product_id: str) -> None:
    d = _load()
    if str(product_id) in d:
        d.pop(str(product_id), None)
        _save(d)


def clear() -> None:
    try:
        _path().unlink(missing_ok=True)
    except Exception:
        pass


def items() -> list[dict]:
    """返回 [{id, error, title, ts}]，最新的在前。"""
    d = _load()
    rows = [{"id": k, **v} for k, v in d.items()]
    rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return rows


def ids() -> list[str]:
    return [r["id"] for r in items()]


def count() -> int:
    return len(_load())

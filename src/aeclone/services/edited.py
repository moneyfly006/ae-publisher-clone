"""已编辑商品记录：编辑成功后落盘，后续运行不再重复编辑。"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ..config import config_dir

_LOCK = threading.Lock()


def _path() -> Path:
    p = config_dir() / "edited_products.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        p = _path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def _prune(data: dict) -> dict:
    # 只保留最近 180 天；最多 5000 条
    now = time.time()
    items = []
    for pid, rec in data.items():
        try:
            ts = float((rec or {}).get("ts") or 0)
        except Exception:
            ts = 0
        if ts and now - ts < 180 * 86400:
            items.append((ts, str(pid), rec))
    items.sort(reverse=True)
    return {pid: rec for _ts, pid, rec in items[:5000]}


def is_edited(product_id: str) -> bool:
    with _LOCK:
        data = _load()
    return str(product_id) in data


def mark(product_id: str, title: str = "", status: str = "saved") -> None:
    pid = str(product_id or "").strip()
    if not pid:
        return
    with _LOCK:
        data = _prune(_load())
        data[pid] = {"title": str(title or "")[:120], "ts": time.time(), "status": status}
        _save(data)


def clear() -> int:
    with _LOCK:
        data = _load()
        n = len(data)
        _save({})
    return n


def stats() -> dict:
    with _LOCK:
        data = _load()
    return {"count": len(data), "items": data}


def filter_new(products: list[dict]) -> tuple[list[dict], int]:
    """保持原顺序，去掉已编辑过的产品；返回 (保留, 跳过数)。"""
    with _LOCK:
        data = _load()
    keep, skipped = [], 0
    for p in products or []:
        pid = str(p.get("id") or "")
        if pid and pid in data:
            skipped += 1
            continue
        keep.append(p)
    return keep, skipped

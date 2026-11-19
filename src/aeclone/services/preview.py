"""改前后对比：保存每个商品 AI 处理前后的标题/详情/主图，供界面预览。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import config_dir


def _path() -> Path:
    p = config_dir() / "preview.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record(product_id: str, before: dict, after: dict, title: str = "") -> None:
    """记录一个商品的改前/改后快照（保留最近 200 条）。"""
    entry = {
        "ts": int(time.time()),
        "id": str(product_id),
        "title": str(title or "")[:80],
        "before": before,
        "after": after,
    }
    rows = load_all()
    rows = [r for r in rows if str(r.get("id")) != str(product_id)]
    rows.append(entry)
    rows = rows[-200:]
    try:
        _path().write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_all() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def latest(n: int = 10) -> list[dict]:
    return load_all()[-n:][::-1]


def clear() -> None:
    try:
        _path().unlink(missing_ok=True)
    except Exception:
        pass

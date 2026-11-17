"""图片处理结果缓存（跨产品复用，省时间也省 AI 费用）。

键 = 模型 + 处理动作/提示词 + 原图 URL 的哈希；值 = 处理后的图片 URL。
- 同一张图在多个产品里出现时只调用一次模型；
- 默认 7 天过期，可在设置里关掉；
- 存到配置目录 cache/image_cache.json，重启后仍有效。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from ..config import config_dir

_LOCK = threading.Lock()
_MEM: dict[str, dict] = {}
_LOADED = False


def _path() -> Path:
    p = config_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p / "image_cache.json"


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _MEM.update({k: v for k, v in data.items() if isinstance(v, dict)})
    except Exception:
        pass


def _save() -> None:
    try:
        _path().write_text(json.dumps(_MEM, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def key_for(url: str, action: str, model: str = "") -> str:
    raw = f"{model}|{action}|{url}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()[:24]


def get(url: str, action: str, model: str = "", days: int = 7) -> str | None:
    _load()
    k = key_for(url, action, model)
    with _LOCK:
        item = _MEM.get(k)
    if not item:
        return None
    if time.time() - float(item.get("ts") or 0) > max(1, days) * 86400:
        with _LOCK:
            _MEM.pop(k, None)
        return None
    return str(item.get("out") or "") or None


def put(url: str, action: str, out: str, model: str = "", extra: dict | None = None) -> None:
    _load()
    if not out:
        return
    k = key_for(url, action, model)
    with _LOCK:
        _MEM[k] = {"out": str(out), "ts": time.time(), "url": str(url)[:300],
                   "action": str(action)[:80], **(extra or {})}
        if len(_MEM) > 4000:
            # 简单淘汰：删最旧的 500 条
            for old in sorted(_MEM.items(), key=lambda kv: kv[1].get("ts") or 0)[:500]:
                _MEM.pop(old[0], None)
        _save()


def clear() -> int:
    _load()
    with _LOCK:
        n = len(_MEM)
        _MEM.clear()
        _save()
    return n


def stats() -> dict:
    _load()
    with _LOCK:
        return {"count": len(_MEM)}

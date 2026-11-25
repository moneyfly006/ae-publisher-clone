"""按平台隔离配置：平台专属键、店铺、模板、Cookie 分开存。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import config_dir, save_config

_ALIEXPRESS_EXTRAS = {
    "store_id", "store_name", "template_product_id", "template_product_title",
    "template_product_source", "template_source", "template_library",
    "template_snapshot", "template_values", "product_template_id",
    "image_ai_jobs", "detail_local_images",
}


def _dir() -> Path:
    p = config_dir() / "platforms"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(pid: str) -> Path:
    safe = "".join(c for c in str(pid or "") if c.isalnum() or c in "-_") or "unknown"
    return _dir() / f"{safe}.json"


def _keys(cfg, pid: str) -> dict:
    out = {}
    prefix = str(pid or "") + "_"
    try:
        data = cfg.as_dict()
    except Exception:
        data = {}
    for k, v in data.items():
        if str(k).startswith(prefix) or (pid == "aliexpress" and k in _ALIEXPRESS_EXTRAS):
            out[k] = v
    return out


def save(cfg, pid: str) -> None:
    try:
        p = _path(pid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_keys(cfg, pid), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


def load(cfg, pid: str) -> bool:
    try:
        p = _path(pid)
        if not p.is_file():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False
        for k, v in data.items():
            cfg.set(k, v)
        return True
    except Exception:
        return False


def migrate_legacy_cookie(cfg) -> None:
    """旧版本只有 manual_cookies：迁移到当前平台专属 Cookie，不再共享。"""
    try:
        manual = getattr(cfg, "manual_cookies", None)
        if not manual:
            return
        pid = str(getattr(cfg, "active_platform", "") or "aliexpress")
        key = pid + "_cookies"
        if not getattr(cfg, key, None):
            cfg.set(key, manual)
            save_config(cfg)
        cfg.set("manual_cookies", {})
        save_config(cfg)
    except Exception:
        pass

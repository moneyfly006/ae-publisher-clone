"""用户自定义提示词预设 + 上次选择记忆（所有平台共用）。

不覆盖内置预设；自定义预设按提示词目标 key 分开保存，互不影响。
config 里只存一个 JSON：prompt_presets_data = {"custom": {key: {name: prompt}}, "choices": {key: name}}
"""
from __future__ import annotations

import json
from typing import Any

DATA_KEY = "prompt_presets_data"


def _load(cfg: Any) -> dict:
    try:
        raw = getattr(cfg, DATA_KEY, "{}")
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    custom = data.get("custom")
    choices = data.get("choices")
    return {"custom": custom if isinstance(custom, dict) else {},
            "choices": choices if isinstance(choices, dict) else {}}


def _store(cfg: Any, data: dict, save=None) -> None:
    try:
        cfg.set(DATA_KEY, json.dumps(data, ensure_ascii=False))
        if callable(save):
            save(cfg)
    except Exception:
        pass


def custom_presets(cfg: Any, key: str) -> dict[str, str]:
    row = _load(cfg)["custom"].get(str(key)) or {}
    if not isinstance(row, dict):
        return {}
    return {str(k): str(v) for k, v in row.items() if str(k).strip() and str(v).strip()}


def save_custom(cfg: Any, key: str, name: str, prompt: str, save=None) -> bool:
    key, name, prompt = str(key), str(name or "").strip(), str(prompt or "").strip()
    if not key or not name or not prompt:
        return False
    data = _load(cfg)
    bucket = data["custom"].setdefault(key, {})
    if not isinstance(bucket, dict):
        bucket = data["custom"][key] = {}
    bucket[name] = prompt
    data["choices"][key] = name
    _store(cfg, data, save)
    return True


def delete_custom(cfg: Any, key: str, name: str, save=None) -> bool:
    data = _load(cfg)
    bucket = data["custom"].get(str(key))
    name = str(name or "").strip()
    if not isinstance(bucket, dict) or name not in bucket:
        return False
    bucket.pop(name, None)
    if not bucket:
        data["custom"].pop(str(key), None)
    if data["choices"].get(str(key)) == name:
        data["choices"].pop(str(key), None)
    _store(cfg, data, save)
    return True


def choice(cfg: Any, key: str) -> str:
    return str(_load(cfg)["choices"].get(str(key)) or "")


def set_choice(cfg: Any, key: str, name: str, save=None) -> None:
    data = _load(cfg)
    name = str(name or "").strip()
    if name:
        data["choices"][str(key)] = name
    else:
        data["choices"].pop(str(key), None)
    _store(cfg, data, save)

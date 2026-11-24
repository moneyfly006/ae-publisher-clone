"""任务配置：把当前关键词、模板、平台和非敏感配置保存为可复用任务。"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from ..config import config_dir

_FILE = "task_profiles.json"
_SENSITIVE = ("cookie", "token", "password", "secret", "api_key",
              "license", "proxy_user", "proxy_pass")


def _path() -> Path:
    p = config_dir() / _FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read() -> list[dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _write(items: list[dict]) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def list_tasks() -> list[dict]:
    return sorted(_read(), key=lambda x: float(x.get("updated_at") or 0), reverse=True)


def get(task_id: str) -> dict:
    for t in _read():
        if str(t.get("id")) == str(task_id):
            return t
    return {}


def snapshot_config(cfg) -> dict:
    """保存配置快照，但剔除 Cookie/Token/密码/API Key 等敏感项。"""
    try:
        data = cfg.as_dict()
    except Exception:
        data = dict(cfg or {})
    return {k: v for k, v in data.items()
            if not any(word in str(k).lower() for word in _SENSITIVE)}


def create(name: str, cfg, platform: str, *, keyword: str = "",
           auto_release: str = "keep", task_id: str = "", status: str = "idle",
           quantity: int = 0, selected_products: list | None = None) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("任务名称不能为空")
    now = time.time()
    item = {
        "id": task_id or uuid.uuid4().hex[:12],
        "name": name,
        "platform": str(platform or ""),
        "keyword": str(keyword or ""),
        "quantity": int(quantity or 0),
        "selected_products": [str(x) for x in (selected_products or []) if str(x).strip()],
        "config": snapshot_config(cfg),
        "auto_release": "delete" if str(auto_release) == "delete" else "keep",
        "status": status,
        "last_run": 0,
        "last_error": "",
        "logs": [],
        "created_at": now,
        "updated_at": now,
    }
    items = [x for x in _read() if str(x.get("id")) != str(item["id"])]
    items.append(item)
    _write(items)
    return item


def update(task_id: str, **changes) -> dict:
    items = _read()
    for i, t in enumerate(items):
        if str(t.get("id")) == str(task_id):
            allowed = {"name", "platform", "keyword", "config", "auto_release",
                       "status", "last_run", "last_error", "quantity", "selected_products",
                       "logs"}
            for k, v in changes.items():
                if k in allowed:
                    t[k] = v
            t["updated_at"] = time.time()
            items[i] = t
            _write(items)
            return t
    return {}


def delete(task_id: str) -> bool:
    items = _read()
    left = [x for x in items if str(x.get("id")) != str(task_id)]
    if len(left) == len(items):
        return False
    _write(left)
    return True


def apply_to_config(task: dict, cfg) -> None:
    """把任务快照应用到目标配置；保留当前敏感项（Cookie/授权）。"""
    snapshot = task.get("config") or {}
    if not isinstance(snapshot, dict):
        return
    try:
        current = cfg.as_dict()
    except Exception:
        current = {}
    for k, v in snapshot.items():
        if any(word in str(k).lower() for word in _SENSITIVE):
            continue
        cfg.set(k, v)
    # 保留当前敏感项
    for k, v in current.items():
        if any(word in str(k).lower() for word in _SENSITIVE):
            cfg.set(k, v)


def mark(task_id: str, status: str, error: str = "") -> None:
    update(task_id, status=status, last_run=time.time(), last_error=str(error or "")[:300])


def release_if_done(task: dict) -> bool:
    """完成后按任务设置释放；返回是否已删除。"""
    if str(task.get("auto_release") or "keep") != "delete":
        return False
    return delete(str(task.get("id") or ""))


def enqueue(name: str, cfg, platform: str, *, keyword: str = "",
            quantity: int = 0, selected_products: list | None = None) -> dict:
    """把当前配置快照加入待执行队列。"""
    if not str(name or "").strip():
        raise ValueError("请填写队列任务名称")
    return create(name, cfg, platform, keyword=keyword, status="pending",
                  quantity=quantity, selected_products=selected_products)


def pending_jobs() -> list[dict]:
    return [t for t in list_tasks() if str(t.get("status") or "") == "pending"]


def clear_finished() -> int:
    """清理已完成/失败任务；未执行和运行中的保留。"""
    items = _read()
    left = [t for t in items if str(t.get("status") or "") not in ("done", "failed")]
    n = len(items) - len(left)
    if n:
        _write(left)
    return n


def recover_interrupted() -> int:
    """软件重启后把上次中断的 running 任务恢复成 pending，避免永远卡住。"""
    items = _read()
    n = 0
    for t in items:
        if str(t.get("status") or "") == "running":
            t["status"] = "pending"
            t["last_error"] = "上次运行中断，已重新排队"
            t["updated_at"] = time.time()
            n += 1
    if n:
        _write(items)
    return n


def append_log(task_id: str, message: str, max_lines: int = 300) -> None:
    """把队列任务日志持久化到任务本身；实时日志关闭后仍可回看。"""
    items = _read()
    for t in items:
        if str(t.get("id")) == str(task_id):
            logs = t.get("logs") if isinstance(t.get("logs"), list) else []
            logs.append(str(message or "")[:500])
            t["logs"] = logs[-int(max_lines or 300):]
            t["updated_at"] = time.time()
            _write(items)
            return

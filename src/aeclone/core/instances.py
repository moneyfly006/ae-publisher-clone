"""多开：每个实例独立配置目录 + 独立浏览器调试端口，可并行运行。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..config import config_dir


def _root() -> Path:
    return config_dir().parent / "AuroraListInstances"


def _read_cfg(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _used_ports() -> set[int]:
    used = set()
    for item in list_instances():
        try:
            used.add(int(item.get("cdp_port") or 0))
        except Exception:
            continue
    return {p for p in used if p}


def _next_port() -> int:
    """默认实例用 9333；新实例从 9334 起找第一个空闲端口。"""
    used = _used_ports()
    port = 9334
    while port in used and port < 9500:
        port += 1
    return port


def list_instances() -> list[dict]:
    root = _root()
    out = [{"name": "默认", "path": str(config_dir()),
            "cdp_port": int(_read_cfg(config_dir() / "config.json").get("cdp_port") or 9333)}]
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        cfg = _read_cfg(d / "config" / "config.json")
        out.append({"name": d.name, "path": str(d / "config"),
                    "cdp_port": int(cfg.get("cdp_port") or 0)})
    return out


def create_instance(name: str) -> dict:
    """创建实例：复制默认配置，并分配一个独立调试端口（避免多开打架）。"""
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "instance"
    target = _root() / safe / "config"
    target.mkdir(parents=True, exist_ok=True)
    cfg_path = target / "config.json"
    if not cfg_path.exists():
        src = config_dir() / "config.json"
        data = _read_cfg(src) if src.exists() else {}
        data["cdp_port"] = _next_port()
        # 实例默认不再弹引导
        data["onboarded"] = True
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    cfg = _read_cfg(cfg_path)
    if not cfg.get("cdp_port"):
        cfg["cdp_port"] = _next_port()
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"name": safe, "path": str(target), "cdp_port": int(cfg.get("cdp_port") or 0)}


def launch_instance(name: str, env_extra: dict | None = None) -> subprocess.Popen:
    """启动一个新实例窗口；窗口位置错开，避免叠在一起。"""
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (env_extra or {}).items()})
    env["AURORA_INSTANCE"] = name
    # 按实例序号错开窗口位置
    idx = 0
    for i, item in enumerate(list_instances()):
        if item.get("name") == name:
            idx = i
            break
    env["AURORA_WINDOW_POS"] = f"{120 + 40 * idx},{90 + 36 * idx}"
    exe = sys.executable
    if getattr(sys, "frozen", False):
        cmd = [exe]
    else:
        cmd = [exe, str(Path(__file__).resolve().parents[3] / "main.py")]
    from .proc import popen as _hidden_popen
    return _hidden_popen(cmd, env=env, close_fds=True)


def launch_task_instance(task: dict, instance_name: str = "") -> dict:
    """把任务配置写入独立实例并启动；返回 {'process','name','path'}。"""
    name = instance_name or ("task-" + str(task.get("id") or "")[:8])
    info = create_instance(name)
    cfg_path = Path(info["path"]) / "config.json"
    try:
        data = _read_cfg(cfg_path)
    except Exception:
        data = {}
    snapshot = task.get("config") or {}
    if isinstance(snapshot, dict):
        for k, v in snapshot.items():
            if any(word in str(k).lower() for word in
                   ("cookie", "token", "password", "secret", "api_key", "license")):
                continue
            data[k] = v
    if task.get("keyword"):
        plat = str(task.get("platform") or data.get("active_platform") or "")
        if plat:
            data[plat + "_collect_keyword"] = str(task.get("keyword") or "")
    if task.get("platform"):
        data["active_platform"] = str(task.get("platform"))
    cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 实例配置目录写一份单任务配置，启动进程可从自己的 config_dir 读取状态。
    try:
        (Path(info["path"]) / "task_profiles.json").write_text(
            json.dumps([task], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    proc = launch_instance(name, env_extra={
        "AURORA_AUTOSTART": "1",
        "AURORA_TASK_ID": str(task.get("id") or ""),
    })
    return {"process": proc, "name": name, "path": info["path"]}

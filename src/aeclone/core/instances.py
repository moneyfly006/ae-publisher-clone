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


def launch_instance(name: str) -> subprocess.Popen:
    """启动一个新实例窗口；窗口位置错开，避免叠在一起。"""
    env = dict(os.environ)
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
    return subprocess.Popen(cmd, env=env, close_fds=True)

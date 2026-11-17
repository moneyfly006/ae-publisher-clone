"""多实例：每个实例独立配置目录，可并行运行。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from .proc import popen, run
import sys
from pathlib import Path

from ..config import config_dir


def _root() -> Path:
    return config_dir().parent / "AuroraListInstances"


def list_instances() -> list[dict]:
    root = _root()
    if not root.exists():
        return [{"name": "默认", "path": str(config_dir())}]
    out = [{"name": "默认", "path": str(config_dir())}]
    for d in sorted(root.iterdir()):
        if d.is_dir():
            out.append({"name": d.name, "path": str(d / "config")})
    return out


def create_instance(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or "instance"
    target = _root() / safe / "config"
    target.mkdir(parents=True, exist_ok=True)
    src = config_dir() / "config.json"
    if src.exists() and not (target / "config.json").exists():
        shutil.copy2(src, target / "config.json")
    return target


def launch_instance(name: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["AURORA_INSTANCE"] = name
    exe = sys.executable
    if getattr(sys, "frozen", False):
        cmd = [exe]
    else:
        cmd = [exe, str(Path(__file__).resolve().parents[3] / "main.py")]
    return popen(cmd, env=env)

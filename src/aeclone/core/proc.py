"""统一的子进程调用：Windows 下不弹控制台窗口。"""
from __future__ import annotations

import os
import subprocess


def _flags() -> int:
    if os.name == "nt":
        return 0x08000000  # CREATE_NO_WINDOW
    return 0


def run(cmd, **kw):
    kw.setdefault("creationflags", _flags())
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw.setdefault("startupinfo", si)
    return subprocess.run(cmd, **kw)


def popen(cmd, **kw):
    kw.setdefault("creationflags", _flags())
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kw.setdefault("startupinfo", si)
    return subprocess.Popen(cmd, **kw)

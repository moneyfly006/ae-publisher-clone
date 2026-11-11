"""自动更新：检查版本、下载安装包、校验。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import requests

CURRENT = "0.2.0"


def _parse(v: str) -> tuple:
    parts = []
    for p in (v or "").replace("v", "").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts[:3])


def check(server: str, app_name: str = "AuroraList", timeout: int = 10) -> dict:
    try:
        r = requests.get(f"{server.rstrip('/')}/api/version/check",
                         params={"version": CURRENT, "platform": "windows", "appName": app_name},
                         timeout=timeout)
        data = r.json()
        info = data.get("data") or {}
        latest = info.get("version") or info.get("latest_version") or ""
        return {
            "ok": True,
            "has_update": bool(latest and _parse(latest) > _parse(CURRENT)),
            "latest": latest,
            "url": info.get("url") or info.get("download_url") or "",
            "notes": info.get("notes") or info.get("changelog") or "",
            "raw": data,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def download(url: str, dest: Path | None = None, timeout: int = 120) -> Path:
    dest = dest or (Path(tempfile.gettempdir()) / Path(url).name)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
                done += len(chunk)
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def launch_installer(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["open" if os.sys.platform == "darwin" else "xdg-open", str(path)])

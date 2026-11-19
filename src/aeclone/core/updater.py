"""自动更新：检查版本（GitHub Release 优先）、下载安装包、校验。"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

import requests

from .. import __version__

CURRENT = __version__
REPO = "moneyfly006/ae-publisher-clone"
GITHUB_API = f"https://api.github.com/repos/{REPO}/releases/latest"


def _parse(v: str) -> tuple:
    parts = []
    for p in (v or "").replace("v", "").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts[:3])


def _platform_asset(name: str) -> bool:
    """release 里的 asset 是否适用于当前平台。"""
    n = (name or "").lower()
    if sys.platform == "darwin":
        return "macos" in n and n.endswith(".zip")
    if os.name == "nt":
        return ("setup" in n and n.endswith(".exe")) or (
            "windows" in n and n.endswith(".zip"))
    return False


def check_github(timeout: int = 10) -> dict:
    """查 GitHub 最新 Release，并挑当前平台的下载地址。"""
    try:
        r = requests.get(GITHUB_API, timeout=timeout,
                         headers={"Accept": "application/vnd.github+json"})
        if r.status_code >= 400:
            return {"ok": False, "error": f"GitHub HTTP {r.status_code}"}
        data = r.json() or {}
        tag = str(data.get("tag_name") or "").strip()
        assets = data.get("assets") or []
        pick = None
        for a in assets:
            if _platform_asset(str(a.get("name") or "")):
                pick = a
                # Windows 优先安装包
                if os.name == "nt" and str(a.get("name") or "").lower().startswith("auroralist-setup"):
                    break
        url = str((pick or {}).get("browser_download_url") or "")
        has = bool(tag and _parse(tag) > _parse(CURRENT))
        return {
            "ok": True,
            "source": "github",
            "has_update": has,
            "latest": tag.lstrip("v"),
            "url": url,
            "sha256": "",
            "notes": str(data.get("body") or "")[:800],
            "raw": data,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def check(server: str = "", app_name: str = "AuroraList", timeout: int = 10) -> dict:
    """优先查 GitHub Release；失败再回退到自建版本接口。"""
    gh = check_github(timeout)
    if gh.get("ok"):
        return gh
    if not server:
        return gh
    try:
        platform_tag = "macos" if sys.platform == "darwin" else "windows"
        r = requests.get(f"{server.rstrip('/')}/api/version/check",
                         params={"version": CURRENT, "platform": platform_tag,
                                 "appName": app_name},
                         timeout=timeout)
        data = r.json()
        info = data.get("data") or {}
        latest = (info.get("version") or info.get("latestVersion")
                  or info.get("latest_version") or "")
        url = info.get("downloadUrl") or info.get("url") or info.get("download_url") or ""
        server_says = bool(info.get("hasUpdate"))
        has = bool(latest and _parse(latest) > _parse(CURRENT)) or (server_says and bool(latest))
        return {
            "ok": True,
            "source": "server",
            "has_update": has,
            "latest": latest,
            "url": url,
            "sha256": info.get("sha256") or info.get("fileSha256") or info.get("fileHash") or "",
            "notes": info.get("notes") or info.get("changelog") or info.get("changeLog") or "",
            "raw": data,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def download(url: str, dest: Path | None = None, timeout: int = 120) -> Path:
    dest = dest or (Path(tempfile.gettempdir()) / Path(url).name)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                f.write(chunk)
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
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])

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


def _machine_arch() -> str:
    """arm64 / x86_64（兼容 Rosetta 下报 x86_64 的情况）。"""
    import platform as _pf
    m = (_pf.machine() or "").lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    return "x64"


def _asset_score(name: str) -> int:
    """给 release asset 打分，越大越合适；0 表示不适用于当前平台。"""
    n = (name or "").lower()
    if sys.platform == "darwin":
        if not (n.startswith("auroralist-macos") and n.endswith(".zip")):
            return 0
        want = _machine_arch()
        other = "x64" if want == "arm64" else "arm64"
        if want in n:
            return 100
        if other in n:
            return 10      # 架构不匹配，仅作兜底
        return 20
    if os.name == "nt":
        if n.startswith("auroralist-setup") and n.endswith(".exe"):
            return 100
        if "windows" in n and n.endswith(".zip"):
            return 50
    return 0


def _platform_asset(name: str) -> bool:
    """兼容旧调用：是否能用于当前平台。"""
    return _asset_score(name) > 0


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
        best = 0
        for a in assets:
            name = str(a.get("name") or "")
            sc = _asset_score(name)
            if sc > best:
                best, pick = sc, a
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


def download_with_progress(url: str, dest: Path | None = None,
                           on_progress=None, timeout: int = 30) -> Path:
    """边下边回报进度（0~1，总大小未知时回调 -1）。"""
    dest = dest or (Path(tempfile.gettempdir()) / Path(url).name)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    try:
                        on_progress(done / total if total else -1.0)
                    except Exception:
                        pass
    return dest


def looks_like_installer(path: Path) -> bool:
    """粗校验：exe 应以 MZ 开头，zip 应以 PK 开头；HTML 错误页直接判坏。"""
    try:
        head = Path(path).read_bytes()[:512]
    except Exception:
        return False
    if not head:
        return False
    if head.startswith(b"MZ") or head.startswith(b"PK"):
        return True
    low = head.lower()
    return not (b"<html" in low or low.startswith(b"<") or b"doctype" in low)


def windows_install_target(path: Path) -> Path | None:
    """Windows 上真正要运行的文件：Setup.exe 直接用；zip 解压后找 Setup/主程序。"""
    import zipfile
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".exe":
        return path
    if ext == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="aurora-upd-"))
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        setups = sorted(tmp.rglob("Setup*.exe")) or sorted(tmp.rglob("*[Ss]etup*.exe"))
        if setups:
            return setups[0]
        exes = sorted(tmp.rglob("AuroraList.exe"))
        if exes:
            return exes[0]
        return None
    return None


def install_update(path: Path, app_dir: str = "/Applications/AuroraList.app") -> bool:
    """安装更新并重启。

    macOS：解压 zip → 等本进程退出 → 替换 /Applications 下的 app → 重新打开。
    Windows：直接运行安装包（NSIS 会处理替换）。
    返回 True 表示已交给后台安装器（调用方随后应退出）。
    """
    path = Path(path)
    if os.name == "nt":
        try:
            target = windows_install_target(path)
            if target is None:
                return False
            os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        except Exception:
            return False
    if sys.platform != "darwin":
        return False
    import subprocess
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="aurora-upd-"))
    try:
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(path), str(tmp)],
                       check=True, timeout=300)
    except Exception:
        try:
            shutil.unpack_archive(str(path), str(tmp))
        except Exception:
            return False
    apps = list(tmp.glob("*.app")) or list(tmp.glob("*/*.app"))
    if not apps:
        return False
    new_app = apps[0]
    script = tmp / "install.sh"
    script.write_text(
        "#!/bin/bash\n"
        f"sleep 2\n"
        f"rm -rf '{app_dir}'\n"
        f"cp -R '{new_app}' '{app_dir}'\n"
        f"xattr -dr com.apple.quarantine '{app_dir}' 2>/dev/null || true\n"
        f"open '{app_dir}'\n", encoding="utf-8")
    script.chmod(0o755)
    try:
        subprocess.Popen(["/bin/bash", str(script)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def launch_installer(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])

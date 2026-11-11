"""CDP 浏览器接入：Edge / 比特 / 站斧 统一抽象。

思路与老软件一致：
- 启动/复用带 --remote-debugging-port 的 Chromium
- 通过 http://127.0.0.1:<port>/json/version 拿 webSocketDebuggerUrl
- Playwright connect_over_cdp 后读取 cookies
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import requests


class BrowserError(RuntimeError):
    pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_chrome() -> str | None:
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in cands:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chrome", "chromium"):
        p = shutil.which(name)
        if p:
            return p
    return None


def find_edge() -> str | None:
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in cands:
        if Path(c).exists():
            return c
    for name in ("msedge", "google-chrome", "chromium", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def default_chrome_profile() -> str | None:
    """返回用户日常 Chrome 配置目录（复用已有登录态）。"""
    import os
    cands = [
        os.path.expanduser("~/Library/Application Support/Google/Chrome"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
        os.path.expanduser("~/.config/google-chrome"),
    ]
    for c in cands:
        if Path(c).exists():
            return c
    return None


def launch_browser(kind: str = "chrome", user_data_dir: str | None = None,
                   port: int | None = None, headless: bool = False,
                   url: str | None = None, use_default_profile: bool = True) -> dict:
    """启动 Chrome/Edge 并返回 CDP 端点（默认 Chrome）。"""
    exe = find_chrome() if kind == "chrome" else find_edge()
    if not exe and kind == "chrome":
        exe = find_edge()
    if not exe:
        raise BrowserError("未找到 Chrome/Edge")
    profile = user_data_dir
    if not profile and use_default_profile:
        # 独立持久配置：登录一次后长期复用（Chrome 136+ 禁止默认配置开调试端口）
        from ..config import config_dir
        profile = str(config_dir() / "chrome-profile")
        Path(profile).mkdir(parents=True, exist_ok=True)
    # 固定端口便于复用已开的窗口
    if port is None:
        port = 9333
        try:
            import requests as _rq
            if _rq.get(f"http://127.0.0.1:{port}/json/version", timeout=2).ok:
                return {"ws": f"ws://127.0.0.1:{port}/devtools/browser", "port": port,
                        "exe": exe, "profile": profile, "reused": True}
        except Exception:
            pass
    return _launch_exe(exe, profile, port, headless, url)


def launch_edge(user_data_dir: str | None = None, port: int | None = None, headless: bool = False) -> dict:
    """启动独立 Edge 并返回 CDP 端点。"""
    exe = find_edge()
    if not exe:
        raise BrowserError("未找到 Edge/Chrome，可执行文件不存在")
    return _launch_exe(exe, user_data_dir, port, headless, None)


def _launch_exe(exe: str, user_data_dir: str | None = None, port: int | None = None,
                headless: bool = False, url: str | None = None) -> dict:
    port = port or _free_port()
    profile = user_data_dir or str(Path(tempfile.gettempdir()) / "aurora-chrome-profile")
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
    ]
    if headless:
        args.append("--headless=new")
    args.append("about:blank")
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + 20
    last = ""
    while time.time() < deadline:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
            if r.status_code == 200:
                info = r.json()
                return {
                    "success": True,
                    "port": port,
                    "pid": proc.pid,
                    "ws": info.get("webSocketDebuggerUrl", ""),
                    "browser": info.get("Browser", ""),
                }
        except Exception as e:  # noqa: BLE001
            last = str(e)
        time.sleep(0.5)
    raise BrowserError(f"Edge 启动失败（CDP 端口 {port} 未就绪）：{last}")


def cookies_via_cdp(ws_endpoint: str, domains: list[str] | None = None, timeout: int = 30) -> dict[str, str]:
    """通过 CDP 读取 cookies，返回 name->value。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise BrowserError(f"缺少 playwright：{e}") from e

    out: dict[str, str] = {}
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_endpoint, timeout=timeout * 1000)
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            for c in ctx.cookies():
                d = c.get("domain", "")
                if domains and not any(x in d for x in domains):
                    continue
                out[c["name"]] = c["value"]
        finally:
            browser.close()
    return out


def open_url_via_cdp(ws_endpoint: str, url: str, wait_ms: int = 3000) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise BrowserError(f"缺少 playwright：{e}") from e
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(ws_endpoint, timeout=30000)
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
        finally:
            browser.close()


def edge_status(port: int) -> dict[str, Any]:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
        return {"alive": r.status_code == 200, "info": r.json() if r.status_code == 200 else None}
    except Exception:
        return {"alive": False, "info": None}

"""直接读取本机浏览器 Cookie（复用已登录状态，无需 CDP）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CookieSource:
    name: str
    ok: bool
    cookies: dict
    error: str = ""


def _collect(jar) -> dict:
    return {c.name: c.value for c in jar}


def read_from(browser: str, domain: str = "dianxiaomi.com") -> CookieSource:
    try:
        import browser_cookie3 as bc
    except ImportError:
        return CookieSource(browser, False, {}, "缺少 browser_cookie3 依赖")

    fn = {
        "chrome": bc.chrome,
        "edge": bc.edge,
        "brave": bc.brave,
        "chromium": bc.chromium,
        "firefox": bc.firefox,
    }.get(browser, bc.chrome)
    try:
        jar = fn(domain_name=domain)
        ck = _collect(jar)
        return CookieSource(browser, bool(ck), ck, "" if ck else "未读取到 Cookie（可能未登录）")
    except Exception as e:  # noqa: BLE001
        return CookieSource(browser, False, {}, f"{type(e).__name__}: {e}")


def read_best(domain: str = "dianxiaomi.com") -> CookieSource:
    """按 Chrome → Edge → Brave → Chromium 顺序尝试。"""
    errors = []
    for name in ("chrome", "edge", "brave", "chromium"):
        src = read_from(name, domain)
        if src.ok:
            return src
        errors.append(f"{name}: {src.error}")
    return CookieSource("auto", False, {}, " | ".join(errors)[:300])


def is_logged_in(cookies: dict) -> bool:
    return any(k in cookies for k in ("dxm_i", "dxm_t", "JSESSIONID"))

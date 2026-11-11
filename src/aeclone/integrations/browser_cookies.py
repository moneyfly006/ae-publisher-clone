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
    # 1) 自带解密器（无第三方依赖）
    try:
        from .chrome_cookies import read_cookies
        ck = read_cookies(browser, domain)
        if ck:
            return CookieSource(browser, True, ck, "")
    except Exception as e:  # noqa: BLE001
        first_err = f"{type(e).__name__}: {e}"
    else:
        first_err = "未读到 Cookie"
    # 2) 回退到 browser_cookie3（如果打包里有）
    try:
        import browser_cookie3 as bc  # type: ignore
    except ImportError:
        return CookieSource(browser, False, {}, first_err)

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

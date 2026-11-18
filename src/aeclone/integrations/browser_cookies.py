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


# ---------------- 统一取 Cookie 入口 ----------------
# 优先级（ISSUES #1）：手动粘贴/配置 → 已开浏览器（CDP）→ 本机 Cookie 库
# 原因：本机 Cookie 库在新版 Chrome 上常因 Keychain/加密读不到（显示「0 个」），
# 而实际能用的往往是配置里注入过的 Cookie 或已启动的浏览器会话。
SOURCE_MANUAL = "手动配置"
SOURCE_CDP = "已开浏览器"
SOURCE_LOCAL = "本机 Cookie 库"


def resolve(domain: str = "dianxiaomi.com", manual: dict | None = None,
            ws_endpoint: str = "", use_local: bool = True) -> CookieSource:
    """按「手动 → CDP → 本机库」顺序解析可用 Cookie。"""
    errors: list[str] = []

    # 1) 配置里的手动 Cookie（注入过就一直可用，不依赖浏览器）
    if isinstance(manual, dict) and manual:
        return CookieSource(SOURCE_MANUAL, True, {str(k): str(v) for k, v in manual.items()})

    # 2) 本机浏览器 Cookie 库（可能因加密/权限读不到）
    if use_local:
        src = read_best(domain)
        if src.ok and src.cookies:
            return CookieSource(f"{SOURCE_LOCAL}（{src.name}）", True, src.cookies)
        errors.append(src.error or "本机 Cookie 库：未读到 Cookie")

    return CookieSource("auto", False, {}, " | ".join(errors)[:300])


def describe(src: CookieSource) -> str:
    """给用户看的 Cookie 状态文案。"""
    if src.ok:
        extra = ""
        try:
            if not is_logged_in(src.cookies):
                extra = "（缺少登录标识，可能已过期）"
        except Exception:
            pass
        return f"{src.name} · {len(src.cookies)} 个{extra}"
    return f"{src.name} · 0 个（读取失败，可粘贴）"

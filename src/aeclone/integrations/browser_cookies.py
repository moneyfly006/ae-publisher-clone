"""直接读取本机浏览器 Cookie（复用已登录状态，无需 CDP）。

同时负责「手动/配置里的 Cookie」的统一解析入口：配置里的 Cookie 可能是
``"dxm_i=...; dxm_t=..."`` 字符串（粘贴对话框写入的就是这种），也可能是
旧版本的 dict——这里统一用 :mod:`.cookie_parse` 归一化，避免出现「粘贴了但
识别不到」的假失败。
"""
from __future__ import annotations

from dataclasses import dataclass

from .cookie_parse import normalize_cookie


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


def is_logged_in(cookies) -> bool:
    """是否包含店小秘登录标识（兼容 dict / 字符串）。"""
    ck = normalize_cookie(cookies)
    return any(k in ck for k in ("dxm_i", "dxm_t", "JSESSIONID"))


# ---------------- 统一取 Cookie 入口 ----------------
# 优先级（ISSUES #1）：手动粘贴/配置 → 已开浏览器（CDP）→ 本机 Cookie 库
# 原因：本机 Cookie 库在新版 Chrome 上常因 Keychain/加密读不到（显示「0 个」），
# 而实际能用的往往是配置里注入过的 Cookie 或已启动的浏览器会话。
SOURCE_MANUAL = "手动配置"
SOURCE_CDP = "已开浏览器"
SOURCE_LOCAL = "本机 Cookie 库"


def resolve(domain: str = "dianxiaomi.com", manual=None,
            ws_endpoint: str = "", use_local: bool = True) -> CookieSource:
    """按「手动 → 本机库」顺序解析可用 Cookie。

    ``manual`` 可以是粘贴的 Cookie 字符串（``a=1; b=2``、带 ``Cookie:`` 前缀、
    换行分隔、JSON 导出等）或旧版本的 dict。以前这里只认 dict，导致粘贴对话框
    写入的字符串被直接丢弃 → 界面一直提示「未读取到 Cookie」。

    ``ws_endpoint`` 预留给后续「已开浏览器（CDP）」注入，当前实现不读它。
    """
    errors: list[str] = []

    # 1) 配置里的手动 Cookie（注入过就一直可用，不依赖浏览器）
    manual_ck = normalize_cookie(manual)
    if manual_ck:
        return CookieSource(SOURCE_MANUAL, True, manual_ck)
    if manual not in (None, "", {}, []):
        errors.append("手动 Cookie 无法解析（应形如 dxm_i=...; dxm_t=...）")

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
                extra = "（缺少 dxm_i/dxm_t 登录标识，可能已过期）"
        except Exception:
            pass
        return f"{src.name} · {len(src.cookies or {})} 个{extra}"
    if src.error:
        return f"未识别（{str(src.error)[:90]}）"
    return f"{src.name} · 0 个（读取失败，可粘贴）"

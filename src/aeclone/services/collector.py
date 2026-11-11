"""从商品链接采集标题与图片（1688 / 阿里国际 / 通用）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/157 Safari/537.36"


@dataclass
class Collected:
    url: str
    title: str = ""
    images: list[str] = field(default_factory=list)
    price: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.title or self.images)


def collect(url: str, timeout: int = 30) -> Collected:
    res = Collected(url=url)
    if not url.startswith("http"):
        res.error = "URL 无效"
        return res
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        html = r.text
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}"
        return res

    m = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html) or \
        re.search(r"<title>([^<]+)</title>", html)
    if m:
        res.title = m.group(1).strip()[:200]

    imgs = re.findall(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    imgs += re.findall(r'"(https?://[^"]+\.(?:jpg|jpeg|png|webp))"', html)
    seen, out = set(), []
    for u in imgs:
        u = u.split("?")[0]
        if u.startswith("http") and u not in seen and len(out) < 12:
            seen.add(u); out.append(u)
    res.images = out

    pm = re.search(r'"price"\s*:\s*"?([0-9.]+)', html) or re.search(r'US\s*\$([0-9.]+)', html)
    if pm:
        res.price = pm.group(1)
    return res


def collect_via_cdp(ws_endpoint: str, url: str, wait_ms: int = 9000) -> Collected:
    """用已连接的浏览器渲染页面后采集（适配需要 JS 的站点）。"""
    from playwright.sync_api import sync_playwright

    res = Collected(url=url)
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(ws_endpoint, timeout=30000)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(wait_ms)
        data = page.evaluate("""() => {
            const t = document.querySelector('meta[property="og:title"]')?.content || document.title || '';
            const imgs = [];
            document.querySelectorAll('img').forEach(i => {
                const s = i.currentSrc || i.src || '';
                if (s.startsWith('http')) imgs.push(s);
            });
            const og = document.querySelector('meta[property="og:image"]')?.content;
            if (og) imgs.unshift(og);
            return {title: t, imgs: Array.from(new Set(imgs)).slice(0, 15)};
        }""")
        res.title = (data.get("title") or "")[:200]
        res.images = [u for u in (data.get("imgs") or []) if u.startswith("http")][:12]
        page.close()
        browser.close()
    except Exception as e:  # noqa: BLE001
        res.error = f"{type(e).__name__}: {e}"
    finally:
        pw.stop()
    return res


def to_product(collected: Collected, cost: float = 0.0) -> dict:
    """把采集结果转成产品结构。"""
    return {
        "id": "",
        "title": collected.title,
        "images": collected.images[:8],
        "skus": [],
        "cost": cost,
        "source_url": collected.url,
    }

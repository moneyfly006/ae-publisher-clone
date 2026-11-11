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

"""发布前的图片地址体检：失效图片提前移除，避免平台发布后才报错。"""
from __future__ import annotations

import time
from urllib.parse import urlparse

_CACHE: dict[str, tuple[float, bool]] = {}
_TTL = 600.0


def is_http(url: str) -> bool:
    try:
        p = urlparse(str(url or "").strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def image_url_ok(url: str, timeout: float = 6.0) -> bool:
    """GET 前 32 字节判断是否真实图片；失败/HTML/空内容都算不可用。"""
    url = str(url or "").strip()
    if not is_http(url):
        return False
    now = time.time()
    hit = _CACHE.get(url)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    ok = False
    try:
        import requests
        r = requests.get(url, timeout=timeout, stream=True, headers={"Range": "bytes=0-4096"})
        try:
            if r.status_code in (200, 206):
                ct = str(r.headers.get("content-type") or "").lower()
                chunk = r.raw.read(32) or b""
                magic = (chunk.startswith(b"\x89PNG") or chunk.startswith(b"\xff\xd8")
                         or chunk.startswith(b"RIFF") or chunk.startswith(b"GIF8"))
                ok = bool(ct.startswith("image/") or magic)
        finally:
            r.close()
    except Exception:
        ok = False
    _CACHE[url] = (now, ok)
    return ok


def repair_images(value, *, min_count: int = 1, max_count: int | None = None,
                  timeout: float = 6.0) -> tuple[list[str], list[str], str]:
    """返回 (保留图片, 移除图片, 错误)。value 可为 list 或分隔字符串调用方自行拆分。"""
    urls = [str(x).strip() for x in (value or []) if str(x).strip()]
    kept = [u for u in urls if image_url_ok(u, timeout=timeout)]
    removed = [u for u in urls if u not in kept]
    if max_count:
        kept = kept[:max_count]
    err = ""
    if len(kept) < min_count:
        err = (f"可用图片不足（需要至少 {min_count} 张，当前 {len(kept)} 张）；"
               f"已移除失效图片 {len(removed)} 张，请补齐图片后再发布")
    return kept, removed, err


def is_fake_client(client) -> bool:
    """测试 Mock/Fake 客户端不做联网探活，避免离线测试访问外网。"""
    try:
        if client is None:
            return True
        cls = type(client)
        for klass in cls.__mro__:
            name = (klass.__module__ + "." + klass.__name__).lower()
            if ("mock" in name or "fake" in name or "stub" in name
                    or klass.__module__.startswith("unittest.mock")):
                return True
        return False
    except Exception:
        return True


def preflight_field(payload: dict, field: str, sep: str, *, min_count: int = 1,
                    max_count: int | None = None, client=None, timeout: float = 5.0) -> list[str]:
    """发布前检查/清理图片字段；返回需要阻断的问题（空=通过）。"""
    import os
    if (client is not None and is_fake_client(client)) or os.environ.get("AURORA_TEST") \
            or os.environ.get("AURORA_NO_SAVE"):
        return []
    raw = str((payload or {}).get(field) or "")
    urls = [u.strip() for u in raw.split(sep) if u.strip()]
    if not urls:
        return []
    kept, removed, err = repair_images(urls, min_count=min_count,
                                       max_count=max_count, timeout=timeout)
    if kept != urls:
        payload[field] = sep.join(kept)
    return [err] if err else []

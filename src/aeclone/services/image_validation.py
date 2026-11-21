"""图片尺寸校验（速卖通要求主图/SKU图/详情图 ≥800×800）。

两个层次：
1. 底层 `size_of()` / `check()`：只认本地文件或 bytes，不联网，带进程内缓存。
2. 上层 `validate_image_size()` / `validate_product_images()`：兼容 URL / Path / bytes，
   联网下载后才校验（批量体检用，主流程不用，避免重复下载）。

主流程（Publisher）走 `check()`：图已经被 `_fetch()` 下到本地，直接用本地文件读尺寸，
不额外产生网络请求。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Union

DEFAULT_MIN = 800

# 进程内缓存：key = 文件绝对路径 或 b"sha1:..." ；value = (w, h) 或 None(读不出来)
_SIZE_CACHE: dict[str, tuple[int, int] | None] = {}
_CACHE_MAX = 4000


def _cache_key(src) -> str:
    if isinstance(src, (bytes, bytearray)):
        import hashlib
        return "sha1:" + hashlib.sha1(bytes(src)).hexdigest()
    try:
        return str(Path(src).resolve())
    except Exception:
        return str(src)


def size_of(src: Union[str, Path, bytes]) -> tuple[int, int] | None:
    """读取图片宽高；失败返回 None。只读本地文件/bytes，不联网。"""
    key = _cache_key(src)
    if key in _SIZE_CACHE:
        return _SIZE_CACHE[key]
    size: tuple[int, int] | None = None
    try:
        from PIL import Image
        if isinstance(src, (bytes, bytearray)):
            with Image.open(io.BytesIO(bytes(src))) as im:
                size = (int(im.width), int(im.height))
        else:
            p = Path(src)
            if p.exists() and p.stat().st_size > 0:
                with Image.open(p) as im:
                    size = (int(im.width), int(im.height))
    except Exception:
        size = None
    if len(_SIZE_CACHE) >= _CACHE_MAX:
        _SIZE_CACHE.clear()
    _SIZE_CACHE[key] = size
    return size


def check(src: Union[str, Path, bytes], min_size: int = DEFAULT_MIN) -> tuple[bool, str]:
    """检查本地图是否 ≥ min_size × min_size。

    Returns: (是否合格, 说明)
        合格: (True, "1024x1024")
        不合格: (False, "600x450 低于 800x800")
        读不出: (False, "无法读取图片尺寸")
    """
    size = size_of(src)
    if not size:
        return False, "无法读取图片尺寸"
    w, h = size
    if w < min_size or h < min_size:
        return False, f"{w}x{h} 低于 {min_size}x{min_size}"
    return True, f"{w}x{h}"


def validate_image_size(source: Union[str, Path, bytes],
                        min_width: int = DEFAULT_MIN,
                        min_height: int = DEFAULT_MIN) -> tuple[bool, str]:
    """兼容入口：URL 会先下载再校验。主流程请改用 `check()`。"""
    try:
        if isinstance(source, str) and source.startswith(("http://", "https://")):
            import requests
            r = requests.get(source, timeout=30)
            r.raise_for_status()
            data = r.content
        elif isinstance(source, (bytes, bytearray)):
            data = bytes(source)
        else:
            data = Path(source).read_bytes()
    except Exception as ex:
        return False, f"无法获取图片: {str(ex)[:80]}"
    size = size_of(data)
    if not size:
        return False, "无法读取图片尺寸"
    w, h = size
    if w < min_width or h < min_height:
        return False, f"尺寸 {w}x{h} 不符合要求（需≥{min_width}x{min_height}）"
    return True, f"{w}x{h}"


def validate_product_images(product: dict, min_size: int = DEFAULT_MIN) -> dict[str, list]:
    """批量体检一个产品（会联网下载，适合离线检查工具，不在主流程里用）。"""
    results: dict[str, list] = {"main": [], "detail": [], "sku": []}
    for i, url in enumerate(product.get("images", []) or []):
        results["main"].append((i + 1, *validate_image_size(url, min_size, min_size)))
    for i, url in enumerate(product.get("detail_images", []) or []):
        results["detail"].append((i + 1, *validate_image_size(url, min_size, min_size)))
    for sku in product.get("skus", []) or []:
        url = sku.get("image") or sku.get("img") or sku.get("processed_image")
        if url:
            name = sku.get("sku") or sku.get("name") or "未命名"
            results["sku"].append((name, *validate_image_size(url, min_size, min_size)))
    return results


def summarize(results: dict) -> tuple[int, int, list[str]]:
    """汇总体检结果：(合格数, 不合格数, 问题清单)。"""
    passed = failed = 0
    problems: list[str] = []
    label = {"main": "主图", "detail": "详情图", "sku": "SKU图"}
    for cat, items in (results or {}).items():
        for item in items or []:
            if len(item) < 3:
                continue
            who, ok, info = item[0], item[1], item[2]
            if ok:
                passed += 1
            else:
                failed += 1
                problems.append(f"{label.get(cat, cat)}[{who}] {info}")
    return passed, failed, problems


# 兼容旧名字
get_validation_summary = summarize


if __name__ == "__main__":
    print("Pillow 尺寸校验自检")
    import tempfile
    from PIL import Image as _I

    with tempfile.TemporaryDirectory() as d:
        big = Path(d) / "big.png"
        small = Path(d) / "small.png"
        _I.new("RGB", (1024, 1024), "white").save(big)
        _I.new("RGB", (600, 450), "white").save(small)
        print("  1024x1024 ->", check(big))
        print("  600x450   ->", check(small))
        print("  不存在的文件 ->", check(Path(d) / "nope.png"))
        print("  bytes     ->", check(big.read_bytes()))


def audit_products(products: list[dict], min_size: int = DEFAULT_MIN,
                   max_images_per_product: int = 12,
                   progress=None) -> dict:
    """批量体检一批产品的主图（只查主图，最影响上架）。

    Args:
        products: 产品列表
        min_size: 最小边长
        max_images_per_product: 每个产品最多查几张（控制耗时）
        progress: 可选回调 progress(done, total, title)

    Returns:
        {
          "min_size": int,
          "total": 查询的图片总数,
          "passed": int,
          "failed": int,
          "products": [ {"id","title","bad":[(idx,url,info)], "ok":int, "checked":int} ],
        }
    """
    out = {"min_size": min_size, "total": 0, "passed": 0, "failed": 0, "products": []}
    total_p = len(products or [])
    for n, prod in enumerate(products or [], 1):
        title = str(prod.get("title") or prod.get("id") or "")[:40]
        imgs = [str(u) for u in (prod.get("images") or []) if str(u)][:max_images_per_product]
        bad: list[tuple[int, str, str]] = []
        ok = 0
        for i, url in enumerate(imgs, 1):
            good, info = validate_image_size(url, min_size, min_size)
            out["total"] += 1
            if good:
                ok += 1
                out["passed"] += 1
            else:
                out["failed"] += 1
                bad.append((i, url, info))
        out["products"].append({"id": str(prod.get("id") or ""), "title": title,
                                "ok": ok, "checked": len(imgs), "bad": bad})
        if progress:
            try:
                progress(n, total_p, title)
            except Exception:
                pass
    return out

#!/usr/bin/env python3
"""图片效果客观检查（会调用真实接口，约 ¥0.3/项）。

不靠肉眼，直接量像素：

1. **白底**：造一张「绿色背景 + 橙色方块」的图，
   跑 `white_background()`，然后检查
   - 边缘像素是否接近纯白（≥240）
   - 橙色主体是否还在（没被抹掉）
   - 尺寸是否 ≥800

   > 这条能直接抓住第 16 轮那类问题：模型无视提示词 → 背景还是绿的 → 检查失败。

2. **多图合成**：给 4 张不同颜色的图，`edit_many` 应返回一张与输入都不同的图。

用法：
    python tools/verify_image_quality.py
    python tools/verify_image_quality.py --only white
"""
from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

BG = (30, 190, 60)          # 绿幕
OBJ = (240, 140, 30)        # 橙色主体


def _make_image(path: Path, bg=BG, obj=OBJ, size=1024) -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), bg)
    dr = ImageDraw.Draw(img)
    m = size // 6
    dr.rectangle([m * 2, m * 1.5, size - m * 2, size - m * 1.5], fill=obj)
    img.save(path)
    return path


def _load(url):
    """本地路径或 http URL 都能读。"""
    from PIL import Image
    sp = str(url)
    if not sp.startswith(("http://", "https://")):
        return Image.open(sp).convert("RGB")
    import requests
    r = requests.get(sp, timeout=180)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def _near(px, rgb, tol=18) -> bool:
    return all(abs(int(a) - int(b)) <= tol for a, b in zip(px, rgb))


def _border_stats(img):
    """采样四条边，统计接近白色的比例。"""
    w, h = img.size
    pts = []
    for x in range(0, w, max(1, w // 40)):
        pts += [(x, 0), (x, h - 1)]
    for y in range(0, h, max(1, h // 40)):
        pts += [(0, y), (w - 1, y)]
    white = sum(1 for p in pts if _near(img.getpixel(p), (255, 255, 255), 12))
    return white / max(1, len(pts))


def _color_ratio(img, rgb, tol=40) -> float:
    w, h = img.size
    hit = tot = 0
    for x in range(0, w, max(1, w // 50)):
        for y in range(0, h, max(1, h // 50)):
            tot += 1
            if _near(img.getpixel((x, y)), rgb, tol):
                hit += 1
    return hit / max(1, tot)


def check_white_bg(svc) -> tuple[bool, list[str]]:
    msgs = []
    tmp = Path(tempfile.mkdtemp())
    src = _make_image(tmp / "green.png")
    src_img = _load(str(src))
    msgs.append(f"输入：{src_img.size[0]}x{src_img.size[1]} 绿幕背景 {BG} + 橙色主体")
    msgs.append(f"输入 边缘白色占比 {_border_stats(src_img):.0%}（预期 0%）")

    url = svc.white_background(src)
    img = _load(str(url))
    msgs.append(f"输出：{img.size[0]}x{img.size[1]}")
    msgs.append(f"输出地址 {str(url)[:70]}")

    bw = _border_stats(img)
    obj = _color_ratio(img, OBJ)
    green = _color_ratio(img, BG, 40)
    ok_size = min(img.size) >= 800
    ok_white = bw >= 0.8
    ok_obj = obj >= 0.03
    ok_green = green <= 0.10

    msgs.append(f"{'✓' if ok_size else '✗'} 尺寸 ≥800：{img.size}")
    msgs.append(f"{'✓' if ok_white else '✗'} 边缘接近纯白：{bw:.0%}（要求 ≥80%）")
    msgs.append(f"{'✓' if ok_obj else '✗'} 橙色主体还在：{obj:.1%}（要求 ≥3%）")
    msgs.append(f"{'✓' if ok_green else '✗'} 绿幕已去除：残留 {green:.1%}（要求 ≤10%）")
    return ok_size and ok_white and ok_obj and ok_green, msgs


def check_multi(edit_many) -> tuple[bool, list[str]]:
    msgs = []
    tmp = Path(tempfile.mkdtemp())
    colors = [(200, 50, 50), (50, 50, 200), (50, 160, 60), (200, 180, 40)]
    files = [_make_image(tmp / f"c{i}.png", bg=colors[i], obj=(230, 230, 230)) for i in range(4)]
    src_hashes = {str(_color_ratio(_load(str(f)), colors[i])) for i, f in enumerate(files)}
    url = edit_many(files, "Combine these 4 product photos into ONE clean product image "
                           "on a pure white background, 2x2 grid layout.")
    img = _load(str(url))
    msgs.append(f"4 张输入 → 1 张输出：{img.size[0]}x{img.size[1]}")
    msgs.append(f"输出地址 {str(url)[:70]}")
    ok_size = min(img.size) >= 800
    # 输出应该不是任何单一输入色铺满
    single = max(_color_ratio(img, c, 60) for c in colors)
    ok_not_single = single <= 0.6
    msgs.append(f"{'✓' if ok_size else '✗'} 尺寸 ≥800：{img.size}")
    msgs.append(f"{'✓' if ok_not_single else '✗'} 不是原样返回某一张：单色占比 {single:.0%}（要求 ≤60%）")
    return ok_size and ok_not_single, msgs


def _make_watermarked(path: Path, size=1024) -> Path:
    """白底 + 橙色主体 + 红色水印文字。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (size, size), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    m = size // 6
    dr.rectangle([m * 2, m * 1.5, size - m * 2, size - m * 1.5], fill=OBJ)
    for i in range(3):
        dr.text((size // 5, size // 8 + i * 26), "WATERMARK SAMPLE © 2026",
                fill=(220, 20, 20))
    img.save(path)
    return path


def check_watermark(svc) -> tuple[bool, list[str]]:
    msgs = []
    tmp = Path(tempfile.mkdtemp())
    src = _make_watermarked(tmp / "wm.png")
    src_img = _load(str(src))
    red_before = _color_ratio(src_img, (220, 20, 20), 60)
    obj_before = _color_ratio(src_img, OBJ, 40)
    msgs.append(f"输入：白底 + 橙色主体 + 红色水印，红像素 {red_before:.1%}，橙 {obj_before:.1%}")

    url = svc.remove_watermark(src)
    img = _load(str(url))
    red_after = _color_ratio(img, (220, 20, 20), 60)
    obj_after = _color_ratio(img, OBJ, 40)
    msgs.append(f"输出：{img.size[0]}x{img.size[1]}  {str(url)[:64]}")
    msgs.append(f"红像素：{red_before:.1%} → {red_after:.1%}")
    msgs.append(f"橙主体：{obj_before:.1%} → {obj_after:.1%}")

    ok_size = min(img.size) >= 800
    ok_red = red_after <= max(0.005, red_before * 0.3)
    ok_obj = obj_after >= obj_before * 0.5
    msgs.append(f"{'✓' if ok_size else '✗'} 尺寸 ≥800：{img.size}")
    msgs.append(f"{'✓' if ok_red else '✗'} 水印基本清除：残留 {red_after:.1%}（要求 ≤ 原来的 30%）")
    msgs.append(f"{'✓' if ok_obj else '✗'} 主体保留：{obj_after:.1%}（要求 ≥ 原来的 50%）")
    return ok_size and ok_red and ok_obj, msgs


def check_vision(cfg, key: str) -> tuple[bool, list[str]]:
    """图片审查 + 估重（视觉模型，¥0/次）。"""
    msgs = []
    ok_all = True
    from aeclone.api import ApiClient
    from aeclone.services.hosting import upload
    from aeclone.services.text_pipeline import TextPipeline

    tmp = Path(tempfile.mkdtemp())
    src = _make_image(tmp / "rig.png", bg=(235, 235, 235), obj=OBJ)
    url = upload(src)

    tp = TextPipeline(cfg, client=ApiClient(cfg.api_base, key, 120))

    try:
        r = tp.review_text("70m Hydraulic Water Well Drilling Rig, CE Certified")
        ok = r.ok and bool(r.value.strip())
        msgs.append(f"{'✓' if ok else '✗'} 文本审查：{r.value.strip()[:60] if r.ok else r.error[:60]}")
        ok_all &= ok
    except Exception as ex:  # noqa: BLE001
        msgs.append(f"✗ 文本审查异常：{str(ex)[:70]}")
        ok_all = False

    try:
        r2 = tp.estimate_weight(url, "小型钻机")
        ok2 = r2.ok and bool(r2.value.strip())
        msgs.append(f"{'✓' if ok2 else '✗'} 图片估重：{r2.value.strip()[:80] if r2.ok else r2.error[:60]}")
        ok_all &= ok2
        if r2.ok:
            d = tp.estimate_weight_json(url, "小型钻机", result=r2)
            msgs.append(f"    解析结果：{d if d else '（无法解析成数字）'}")
    except Exception as ex:  # noqa: BLE001
        msgs.append(f"✗ 估重异常：{str(ex)[:70]}")
        ok_all = False
    return ok_all, msgs


def main() -> int:
    only = ""
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    from aeclone.config import AppConfig
    from aeclone.api import ApiClient
    from aeclone.services import ImageService, model_of

    cfg = AppConfig.load()
    key = (cfg.api_key or "").strip()
    if not key:
        print("✗ 没有 API Key")
        return 1
    svc = ImageService(ApiClient(cfg.api_base, key, 240), cfg)

    print("=" * 64)
    print("图片效果客观检查（真实调用，约 ¥0.3/项）")
    print(f"模型：{model_of(cfg, 'image_edit')}")
    print("=" * 64)

    fails = 0
    if only in ("", "white"):
        print("\n[1] 白底")
        ok, msgs = check_white_bg(svc)
        for m in msgs:
            print("   ", m)
        fails += 0 if ok else 1

    if only in ("", "multi"):
        print("\n[2] 多图合成")
        try:
            ok2, msgs2 = check_multi(svc.edit_many)
            for m in msgs2:
                print("   ", m)
            fails += 0 if ok2 else 1
        except Exception as ex:  # noqa: BLE001
            print("    ✗ 失败：", type(ex).__name__, str(ex)[:120])
            fails += 1

    if only in ("", "wm"):
        print("\n[3] 去水印")
        try:
            ok3, msgs3 = check_watermark(svc)
            for m in msgs3:
                print("   ", m)
            fails += 0 if ok3 else 1
        except Exception as ex:  # noqa: BLE001
            print("    ✗ 失败：", type(ex).__name__, str(ex)[:120])
            fails += 1

    if only in ("", "vision"):
        print("\n[4] 视觉模型（审查 / 估重，¥0）")
        try:
            ok4, msgs4 = check_vision(cfg, key)
            for m in msgs4:
                print("   ", m)
            fails += 0 if ok4 else 1
        except Exception as ex:  # noqa: BLE001
            print("    ✗ 失败：", type(ex).__name__, str(ex)[:120])
            fails += 1

    print("\n" + "=" * 64)
    print("结论：全部通过" if fails == 0 else f"结论：{fails} 项未通过")
    print("=" * 64)
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

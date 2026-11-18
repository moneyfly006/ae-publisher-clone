"""本地图片操作：水印、四合一、方图补齐。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def ensure_rgb(img: Image.Image) -> Image.Image:
    return img.convert("RGB") if img.mode != "RGB" else img


def pad_square(path: Path, out: Path, size: int = 1024, bg=(255, 255, 255)) -> Path:
    img = ensure_rgb(Image.open(path))
    img.thumbnail((size, size))
    canvas = Image.new("RGB", (size, size), bg)
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    canvas.save(out, quality=92)
    return out


def add_watermark(path: Path, watermark: Path | str, out: Path,
                  position: str = "bottom-right", opacity: float = 0.7) -> Path:
    base = ensure_rgb(Image.open(path)).convert("RGBA")
    if isinstance(watermark, (str, Path)) and Path(watermark).exists():
        wm = Image.open(watermark).convert("RGBA")
    else:
        # 文本水印
        text = str(watermark)
        wm = Image.new("RGBA", (max(1, len(text) * 24), 60), (0, 0, 0, 0))
        d = ImageDraw.Draw(wm)
        try:
            font = ImageFont.truetype("arial.ttf", 36)
        except Exception:
            font = ImageFont.load_default()
        d.text((4, 8), text, font=font, fill=(255, 255, 255, 220))
    if wm.width > base.width // 2:
        ratio = (base.width // 2) / wm.width
        wm = wm.resize((int(wm.width * ratio), int(wm.height * ratio)))
    alpha = wm.split()[-1].point(lambda a: int(a * opacity))
    wm.putalpha(alpha)

    margin = 24
    pos = {
        "top-left": (margin, margin),
        "top-right": (base.width - wm.width - margin, margin),
        "bottom-left": (margin, base.height - wm.height - margin),
        "bottom-right": (base.width - wm.width - margin, base.height - wm.height - margin),
        "center": ((base.width - wm.width) // 2, (base.height - wm.height) // 2),
    }.get(position, (base.width - wm.width - margin, base.height - wm.height - margin))
    base.alpha_composite(wm, pos)
    base.convert("RGB").save(out, quality=92)
    return out


def four_in_one(paths: list[Path], out: Path, size: int = 1200, gap: int = 8,
                bg=(255, 255, 255)) -> Path:
    if not paths:
        raise ValueError("没有图片")
    cell = (size - gap) // 2
    canvas = Image.new("RGB", (size, size), bg)
    for i, p in enumerate(paths[:4]):
        img = ensure_rgb(Image.open(p))
        img.thumbnail((cell, cell))
        x = gap + (i % 2) * (cell + gap) + (cell - img.width) // 2
        y = gap + (i // 2) * (cell + gap) + (cell - img.height) // 2
        canvas.paste(img, (x, y))
    canvas.save(out, quality=92)
    return out


def make_marketing(src: Path, out: Path, size: tuple[int, int] = (800, 800),
                   bg=(255, 255, 255), mode: str = "contain") -> Path:
    """把主图做成营销图（店小秘「一键生成」同款，纯本地处理，不花钱）。

    店小秘前端就是 canvas 按比例 resize：
      1:1 → 800×800；3:4 → 750×1000
    mode="contain" 完整保留商品（白底补边），"cover" 铺满裁切。
    """
    img = ensure_rgb(Image.open(src))
    tw, th = int(size[0]), int(size[1])
    if mode == "cover":
        scale = max(tw / img.width, th / img.height)
    else:
        scale = min(tw / img.width, th / img.height)
    nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
    img = img.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th), bg)
    canvas.paste(img, ((tw - nw) // 2, (th - nh) // 2))
    canvas.save(out, quality=92)
    return out

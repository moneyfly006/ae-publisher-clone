"""本地图片操作：水印、四合一、方图补齐。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 文本水印字体候选：优先系统 CJK/西文字体，最后用 Pillow 可缩放默认字体。
# macOS 上没有 arial.ttf（旧实现回退 10px 位图字体 → 水印小到看不见）。
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/msyh.ttc",
)


def _load_font(size: int):
    """按像素大小加载字体；最后回退到 Pillow 可缩放默认字体（≥10.1）。"""
    for path in _FONT_CANDIDATES:
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)  # Pillow ≥ 10.1 支持指定字号
    except TypeError:
        return ImageFont.load_default()


def _text_watermark(text: str, base_width: int) -> Image.Image:
    """生成可见的文本水印（白字黑描边，任何底色都能看清）。"""
    size = max(18, int(base_width * 0.045))  # 相对图片宽度，避免大图水印过小
    font = _load_font(size)
    stroke = max(1, size // 16)
    try:
        bbox = font.getbbox(text, stroke_width=stroke)
    except Exception:
        bbox = (0, 0, len(text) * size, size)
    pad = max(6, size // 4)
    w = max(1, bbox[2] - bbox[0] + pad * 2)
    h = max(1, bbox[3] - bbox[1] + pad * 2)
    wm = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(wm)
    d.text((pad - bbox[0], pad - bbox[1]), text, font=font,
           fill=(255, 255, 255, 235), stroke_width=stroke,
           stroke_fill=(0, 0, 0, 210))
    return wm


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
        # 图片水印保持原尺寸/原位置（旧测试按坐标校验；不额外偏移）
        wm = Image.open(watermark).convert("RGBA")
    else:
        # 文本水印：白字 + 黑描边（旧实现固定白字，打在白底图上像素零变化）
        wm = _text_watermark(str(watermark), base.width)
    if wm.width > base.width // 2:
        ratio = (base.width // 2) / wm.width
        wm = wm.resize((max(1, int(wm.width * ratio)),
                        max(1, int(wm.height * ratio))))
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


def compress_jpeg(raw: bytes, out: Path, max_bytes: int = 5 * 1024 * 1024,
                  max_side: int = 1600) -> Path:
    """把任意图片压成 <5MB 的 JPG（详情图超过 5MB 平台会拒收）。"""
    import io as _io
    img = ensure_rgb(Image.open(_io.BytesIO(raw)))
    img.thumbnail((max_side, max_side))
    q = 88
    while True:
        img.save(out, "JPEG", quality=q, optimize=True)
        if out.stat().st_size <= max_bytes or q <= 35:
            return out
        q -= 7
        if q < 60:
            # 质量降不动了，再缩尺寸
            w, h = img.size
            img = img.resize((max(1, int(w * 0.8)), max(1, int(h * 0.8))))


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

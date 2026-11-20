"""主图视频合成：用 ffmpeg 把多张图片合成短视频。"""
from __future__ import annotations

import shutil
import subprocess
from ..core.proc import run
import tempfile
from pathlib import Path


class VideoError(RuntimeError):
    pass


def _exe_name() -> str:
    return "ffmpeg.exe" if __import__("os").name == "nt" else "ffmpeg"


def ffmpeg_bin_dir() -> Path:
    """可写的 ffmpeg 存放目录（Windows: %LOCALAPPDATA%\\AuroraList\\bin）。"""
    import os as _os
    if _os.name == "nt":
        base = Path(_os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "AuroraList" / "bin"
    return Path.home() / ".local" / "share" / "AuroraList" / "bin"


def find_ffmpeg() -> str | None:
    """查找顺序：PATH → 应用自带 assets/bin → 用户目录 → 常见安装位置。"""
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    import sys as _sys
    roots = []
    if getattr(_sys, "frozen", False):
        roots.append(Path(getattr(_sys, "_MEIPASS", Path(_sys.executable).parent)))
        roots.append(Path(_sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parents[3])
    for root in roots:
        for cand in (root / "assets" / "bin" / _exe_name(),
                     root / "assets" / "bin" / "ffmpeg.exe",
                     root / "ffmpeg.exe", root / "bin" / "ffmpeg.exe",
                     root / "bin" / "ffmpeg"):
            if cand.exists():
                return str(cand)
    try:
        local = ffmpeg_bin_dir() / _exe_name()
        if local.exists():
            return str(local)
    except Exception:
        pass
    for cand in (r"C:\ffmpeg\bin\ffmpeg.exe", "/opt/homebrew/bin/ffmpeg",
                 "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"):
        if Path(cand).exists():
            return cand
    return None


def ensure_ffmpeg(on_progress=None) -> str | None:
    """Windows: 没有 ffmpeg 时自动下载一份到用户目录（约 80MB）。

    其他平台返回 None（macOS 用 brew 安装即可）。
    """
    import os as _os
    got = find_ffmpeg()
    if got:
        return got
    if _os.name != "nt":
        return None
    try:
        import requests, zipfile, tempfile, io
        url = ("https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
               "ffmpeg-master-latest-win64-gpl.zip")
        dest = ffmpeg_bin_dir()
        dest.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or 0)
            buf = io.BytesIO()
            done = 0
            for chunk in r.iter_content(256 * 1024):
                if not chunk:
                    continue
                buf.write(chunk)
                done += len(chunk)
                if on_progress:
                    try:
                        on_progress(done / total if total else -1.0)
                    except Exception:
                        pass
        buf.seek(0)
        with zipfile.ZipFile(buf) as z:
            for name in z.namelist():
                if name.lower().endswith("bin/ffmpeg.exe"):
                    with z.open(name) as src, open(dest / "ffmpeg.exe", "wb") as out:
                        out.write(src.read())
                    break
        exe = dest / "ffmpeg.exe"
        return str(exe) if exe.exists() else None
    except Exception:
        return None


def build_slideshow(images: list[Path], out_path: Path, seconds_per_image: float = 2.0,
                    size: str = "1080x1080", fade: bool = True) -> Path:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise VideoError("未找到 ffmpeg，请先安装（或随包分发 ffmpeg）")
    if not images:
        raise VideoError("没有图片")

    size = size.replace("x", ":").replace("*", ":")   # ffmpeg 需要 W:H
    with tempfile.TemporaryDirectory() as td:
        listfile = Path(td) / "list.txt"
        lines = []
        for img in images:
            lines.append(f"file '{Path(img).as_posix()}'")
            lines.append(f"duration {seconds_per_image}")
        lines.append(f"file '{Path(images[-1]).as_posix()}'")
        listfile.write_text("\n".join(lines), encoding="utf-8")

        vf = (f"scale={size}:force_original_aspect_ratio=decrease,"
              f"pad={size}:(ow-iw)/2:(oh-ih)/2:color=white,fps=30,format=yuv420p")
        if fade:
            vf += ",fade=t=in:st=0:d=0.4"
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
               "-r", "30", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
               "-movflags", "+faststart", str(out_path)]
        proc = run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise VideoError(f"ffmpeg 失败：{proc.stderr[-400:]}")
    return out_path


def probe(ffmpeg: str | None = None) -> dict:
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return {"available": False, "path": ""}
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
    except Exception:
        out = ""
    return {"available": True, "path": exe, "version": out}


def run_ffmpeg(cmd: list[str]) -> None:
    proc = run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{proc.stderr[-400:]}")


def clip_video(src: Path, out: Path, start: float = 0.0, duration: float | None = None) -> Path:
    """剪辑视频：截取 start 起、duration 长的片段。"""
    info = probe()
    if not info["available"]:
        raise RuntimeError("未找到 ffmpeg")
    cmd = [info["path"], "-y", "-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(src), "-c", "copy", str(out)]
    run_ffmpeg(cmd)
    return out


def extract_frames(src: Path, out_dir: Path, fps: float = 1.0) -> list[Path]:
    """从视频按帧率提取帧图片，供 AI 处理。"""
    info = probe()
    if not info["available"]:
        raise RuntimeError("未找到 ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    pat = out_dir / "frame_%03d.png"
    cmd = [info["path"], "-y", "-i", str(src), "-vf", f"fps={fps}", str(pat)]
    run_ffmpeg(cmd)
    return sorted(out_dir.glob("frame_*.png"))


def _cover_filter(size: str, fps: int = 30) -> str:
    """等比放大后居中裁剪（cover），不留白边。"""
    w, h = size.split(":")
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},fps={fps},format=yuv420p,setsar=1")


def _pad_filter(size: str, fps: int = 30) -> str:
    w, h = size.split(":")
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=white,fps={fps},format=yuv420p,setsar=1")


def generate_carousel_video(images: list[Path], out_path: Path, slot_seconds: float = 2.5,
                            transition_seconds: float = 0.5, size: str = "1080x1080",
                            cover: bool = True, fade_edges: bool = True) -> Path:
    """把多张主图合成带交叉淡化转场的轮播视频（对齐老软件 generate_carousel_video）。

    Args:
        images: 图片路径列表（按播放顺序）
        out_path: 输出 mp4 路径
        slot_seconds: 每张图停留时长（含转场重叠）
        transition_seconds: 两张图之间的交叉淡化时长
        size: 输出尺寸，形如 "1080x1080"
        cover: True=铺满裁剪，False=留白居中
        fade_edges: 首尾是否淡入淡出
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise VideoError("未找到 ffmpeg，请先安装（或随包分发 ffmpeg）")
    imgs = [Path(p) for p in images if Path(p).exists()]
    if not imgs:
        raise VideoError("没有可用的图片")
    if len(imgs) == 1:
        return build_slideshow(imgs, out_path, seconds_per_image=max(slot_seconds, 1.0), size=size.replace(":", "x"))

    size = size.replace("*", ":").replace("x", ":")
    slot = max(float(slot_seconds), 0.6)
    trans = min(max(float(transition_seconds), 0.1), slot - 0.2 if slot > 0.8 else 0.1)
    vf = _cover_filter(size) if cover else _pad_filter(size)

    cmd = [ffmpeg, "-y"]
    for img in imgs:
        cmd += ["-loop", "1", "-t", f"{slot + 0.3:.2f}", "-i", str(img)]

    parts = []
    for i in range(len(imgs)):
        parts.append(f"[{i}:v]{vf}[v{i}]")

    cur = "v0"
    for i in range(1, len(imgs)):
        offset = i * (slot - trans)
        nxt = f"x{i}"
        parts.append(f"[{cur}][v{i}]xfade=transition=fade:duration={trans:.2f}:offset={offset:.2f}[{nxt}]")
        cur = nxt

    if fade_edges:
        total = len(imgs) * slot - (len(imgs) - 1) * trans
        parts.append(f"[{cur}]fade=t=in:st=0:d=0.4,fade=t=out:st={max(total - 0.5, 0):.2f}:d=0.5[vout]")
        cur = "vout"

    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{cur}]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    proc = run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise VideoError(f"ffmpeg 失败：{proc.stderr[-500:]}")
    return out_path

"""主图视频合成：用 ffmpeg 把多张图片合成短视频。"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class VideoError(RuntimeError):
    pass


def find_ffmpeg() -> str | None:
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (r"C:\ffmpeg\bin\ffmpeg.exe", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(cand).exists():
            return cand
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
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise VideoError(f"ffmpeg 失败：{proc.stderr[-400:]}")
    return out_path


def probe(ffmpeg: str | None = None) -> dict:
    exe = ffmpeg or find_ffmpeg()
    if not exe:
        return {"available": False, "path": ""}
    try:
        out = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    except Exception:
        out = ""
    return {"available": True, "path": exe, "version": out}


def run_ffmpeg(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
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

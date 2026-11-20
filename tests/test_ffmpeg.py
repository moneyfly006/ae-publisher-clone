"""ffmpeg 查找/自动下载的最小回归。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services import video  # noqa: E402

ok = fail = 0


def chk(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name} {extra}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


print("=" * 60)
print("ffmpeg 查找测试")
p = video.find_ffmpeg()
chk("find_ffmpeg 不抛异常", True, str(p or "未安装"))
if p:
    chk("返回的是可执行文件", Path(p).exists(), p)
chk("用户目录以 bin 结尾", video.ffmpeg_bin_dir().name == "bin", str(video.ffmpeg_bin_dir()))
r = video.ensure_ffmpeg()
chk("ensure_ffmpeg 不抛异常", True, str(r or "None"))
if sys.platform == "darwin":
    chk("非 Windows 不会尝试下载", r is None or Path(r).exists())
chk("probe 结构", set(video.probe().keys()) >= {"available", "path"})
print("=" * 60)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)

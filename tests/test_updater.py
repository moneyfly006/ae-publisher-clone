"""版本比较 + 更新安装器的最小回归。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.core import updater  # noqa: E402

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
print("更新器测试")
chk("1.0.1 > 1.0.0", updater._parse("1.0.1") > updater._parse("1.0.0"))
chk("v1.0.10 > v1.0.9", updater._parse("v1.0.10") > updater._parse("v1.0.9"))
chk("空版本不高于 1.0.0", not (updater._parse("") > updater._parse("1.0.0")))
chk("当前版本是 1.0.3", updater.CURRENT == "1.0.3", updater.CURRENT)
chk("坏包不会抛异常", updater.install_update(Path("/tmp/definitely-not-a-zip.zip")) is False)
chk("有下载进度函数", hasattr(updater, "download_with_progress"))
arm = updater._asset_score("AuroraList-macos-arm64.zip")
x64 = updater._asset_score("AuroraList-macos-x64.zip")
print(f"  · 当前机器架构 {updater._machine_arch()} | arm64={arm} x64={x64}")
if updater._machine_arch() == "arm64":
    chk("arm64 机器优先选 arm64 包", arm > x64)
else:
    chk("x64 机器优先选 x64 包", x64 > arm)
chk("无关文件不算匹配", updater._asset_score("readme.txt") == 0)
print("=" * 60)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)

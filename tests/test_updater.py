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
chk("当前版本是 1.0.5", updater.CURRENT == "1.0.5", updater.CURRENT)
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
import os as _os  # noqa: E402
if _os.name == "nt":
    chk("Windows 优先选 Setup.exe", updater._asset_score("AuroraList-Setup-1.0.2.exe") == 100)
    chk("Windows zip 兜底可用", updater._asset_score("AuroraList-windows.zip") == 50)
else:
    chk("非 Windows 不匹配 Setup.exe", updater._asset_score("AuroraList-Setup-1.0.2.exe") == 0)
import tempfile, zipfile  # noqa: E402
_p = Path(tempfile.mkdtemp()) / "fake.zip"
_p.write_bytes(b"PK\x03\x04 dummy")
chk("zip 头识别为安装包", updater.looks_like_installer(_p) is True)
_h = Path(tempfile.mkdtemp()) / "err.exe"
_h.write_bytes(b"<!DOCTYPE html><html>blocked</html>")
chk("HTML 错误页判为无效", updater.looks_like_installer(_h) is False)

# Windows 安装目标：.exe 直接返回；.zip 解压后找 Setup
_exe = Path(tempfile.mkdtemp()) / "AuroraList-Setup-1.0.2.exe"
_exe.write_bytes(b"MZ fake")
chk("Windows .exe 直接作为安装目标", updater.windows_install_target(_exe) == _exe)
_zip = Path(tempfile.mkdtemp()) / "pkg.zip"
with zipfile.ZipFile(_zip, "w") as _z:
    _z.writestr("pkg/AuroraList-Setup-1.0.2.exe", "MZ")
_wt = updater.windows_install_target(_zip)
chk("Windows zip 解压后找到 Setup", _wt is not None and _wt.name == "AuroraList-Setup-1.0.2.exe",
    str(_wt))
_zip2 = Path(tempfile.mkdtemp()) / "portable.zip"
with zipfile.ZipFile(_zip2, "w") as _z:
    _z.writestr("AuroraList/AuroraList.exe", "MZ")
_wt2 = updater.windows_install_target(_zip2)
chk("Windows 便携 zip 找到主程序", _wt2 is not None and _wt2.name == "AuroraList.exe", str(_wt2))
_inst = Path(tempfile.mkdtemp()) / "AuroraList"
_inst.mkdir(parents=True)
(_inst / "unins000.exe").write_bytes(b"MZ")
_args = updater.windows_setup_args(_inst)
chk("静默安装参数带原目录", any(a.startswith("/DIR=") and str(_inst) in a for a in _args), str(_args))
chk("静默安装会关闭旧进程", "/CLOSEAPPLICATIONS" in _args, str(_args))
print("=" * 60)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)

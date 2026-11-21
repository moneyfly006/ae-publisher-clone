"""Windows 自动更新逻辑验证（在 mac 上也能跑：显式传入平台/架构）。"""
import sys
import tempfile
import zipfile
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


print("=" * 64)
print("Windows 自动更新验证")

URL = ("https://github.com/moneyfly006/ae-publisher-clone/releases/download/"
       "v1.0.2/AuroraList-Setup-1.0.2.exe")

# 1) 资产选择：Windows 必须优先 Setup.exe
chk("Windows 选 Setup.exe（100 分）",
    updater._asset_score("AuroraList-Setup-1.0.2.exe", platform="win32", nt=True) == 100)
chk("Windows 的 zip 只作兜底（50 分）",
    updater._asset_score("AuroraList-windows.zip", platform="win32", nt=True) == 50)
chk("Windows 不会选 macOS 包",
    updater._asset_score("AuroraList-macos-arm64.zip", platform="win32", nt=True) == 0)
chk("排序后确实挑中 Setup",
    max(["AuroraList-macos-arm64.zip", "AuroraList-windows.zip",
         "AuroraList-Setup-1.0.2.exe"],
        key=lambda n: updater._asset_score(n, platform="win32", nt=True))
    == "AuroraList-Setup-1.0.2.exe")

# 2) 下载文件名：必须保留 .exe，不能写成 .zip
chk("下载文件名保留 .exe", updater.asset_filename(URL) == "AuroraList-Setup-1.0.2.exe",
    updater.asset_filename(URL))
chk("macOS 包名正确", updater.asset_filename(
    "https://x/y/AuroraList-macos-arm64.zip") == "AuroraList-macos-arm64.zip")

# 3) 下载内容校验
d = Path(tempfile.mkdtemp())
good = d / "AuroraList-Setup-1.0.2.exe"
good.write_bytes(b"MZ" + b"\x00" * 100)
chk("真 exe 通过校验", updater.looks_like_installer(good))
bad = d / "err.exe"
bad.write_bytes(b"<!DOCTYPE html><html>502</html>")
chk("HTML 错误页被拒", not updater.looks_like_installer(bad))

# 4) 安装目标：exe 直接跑；zip 解压找 Setup
chk("exe 直接作为安装目标", updater.windows_install_target(good) == good)
zp = d / "win.zip"
with zipfile.ZipFile(zp, "w") as z:
    z.writestr("AuroraList-Setup-1.0.2.exe", "MZ")
t = updater.windows_install_target(zp)
chk("zip 解压后找到 Setup", t is not None and t.name == "AuroraList-Setup-1.0.2.exe", str(t))

# 5) 静默参数：装回原目录（D 盘也行），并关闭旧进程
inst = Path(tempfile.mkdtemp()) / "AuroraList"
inst.mkdir(parents=True)
(inst / "unins000.exe").write_bytes(b"MZ")
args = updater.windows_setup_args(inst)
chk("带原安装目录 /DIR", any(a.startswith("/DIR=") and str(inst) in a for a in args), str(args))
chk("带 /CLOSEAPPLICATIONS", "/CLOSEAPPLICATIONS" in args)
chk("带 /SILENT（不弹向导）", "/SILENT" in args)

portable = Path(tempfile.mkdtemp())
args2 = updater.windows_setup_args(portable)
chk("便携目录不强塞 /DIR", not any(a.startswith("/DIR=") for a in args2), str(args2))

# 5.5) 文件名叫 .zip、内容其实是 exe 时，按文件头纠正
mislab = d / "AuroraList-Setup-1.0.2.zip"
mislab.write_bytes(b"MZ" + b"\x00" * 64)
chk("错标成 .zip 的 exe 仍能识别", updater.windows_install_target(mislab) == mislab,
    str(updater.windows_install_target(mislab)))

# 6) 完整决策：下载到的 Setup.exe -> 用静默参数原地升级
plan_target, plan_args = updater.windows_install_plan(good, exe_dir=inst)
chk("决策：目标是 Setup.exe", plan_target == good, str(plan_target))
chk("决策：已安装场景带原目录", any(a.startswith("/DIR=") for a in plan_args), str(plan_args))
chk("决策：参数含 /CLOSEAPPLICATIONS", "/CLOSEAPPLICATIONS" in plan_args, str(plan_args))
_t2, _a2 = updater.windows_install_plan(good, exe_dir=portable)
chk("决策：便携场景不带 /DIR", not any(a.startswith("/DIR=") for a in _a2), str(_a2))

# 7) 旧客户端（1.0.1）的问题：下载名固定 .zip，导致 Windows 打不开
# 历史 tag 只在完整仓库存在（CI 浅克隆/本地裁剪会没有），没有时跳过历史
# 核对而不是误报失败；「当前代码不再固定 .zip」始终校验。
import subprocess  # noqa: E402
old_src = subprocess.run(["git", "show", "v1.0.1:src/aeclone/ui/app.py"],
                         capture_output=True, text=True).stdout
if old_src.strip():
    chk("确认 1.0.1 曾固定存成 .zip", "aurora-update-{latest}.zip" in old_src)
else:
    print("  - 跳过：本仓库没有 v1.0.1 tag（历史核对不影响当前行为）")
chk("当前代码不再固定 .zip", "aurora-update-{latest}.zip" not in Path(
    "src/aeclone/ui/app.py").read_text(encoding="utf-8"))

print("=" * 64)
print(f"结果: {ok} 通过, {fail} 失败")
sys.exit(1 if fail else 0)

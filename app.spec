# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all
from pathlib import Path
sys.path.insert(0, str(Path('src').resolve()))

block_cipher = None

# Flet 桌面端（Windows 需要把 flet_desktop 客户端一起打包，否则不弹窗）
flet_datas, flet_bins, flet_hidden = [], [], []
for pkg in ("flet", "flet_desktop"):
    try:
        d, b, h = collect_all(pkg)
        flet_datas += d; flet_bins += b; flet_hidden += h
    except Exception:
        pass

if sys.platform == "darwin":
    # The branded, unpacked helper below replaces this stock archive.
    flet_datas = [(src, dst) for src, dst in flet_datas if not src.endswith("flet-macos.tar.gz")]

# certifi 根证书：打包后 requests 需要它做 TLS 校验；漏了会报
# "Could not find a suitable TLS CA certificate bundle"
cert_datas, cert_bins, cert_hidden = [], [], []
try:
    cert_datas, cert_bins, cert_hidden = collect_all("certifi")
except Exception:
    pass

a = Analysis(
    ["main.py"],
    pathex=[str(Path("src").resolve())],
    binaries=flet_bins + cert_bins,
    datas=[("assets", "assets")] + flet_datas + cert_datas,
    # pycryptodome 的 C 扩展是按需加载的（Crypto.PublicKey._nist_ecc 等），
    # 手写列表一定会漏 —— 打包后表现是 VOUCHER_FAIL / 授权校验直接失败。
    # 所以整包收集，别靠手写。
    hiddenimports=collect_submodules("aeclone") + flet_hidden + cert_hidden
                   + collect_submodules("Crypto")
                   + collect_submodules("browser_cookie3") + [
        "Crypto", "Crypto.Cipher", "Crypto.Cipher.AES",
        "Crypto.Protocol", "Crypto.Protocol.KDF", "Crypto.PublicKey",
        "browser_cookie3", "certifi",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AuroraList",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=("assets/icon.icns" if sys.platform == "darwin" else "assets/icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AuroraList",
)

# ---------- macOS：打成 .app，双击不弹终端、Dock/Finder 显示图标 ----------
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AuroraList.app",
        icon="assets/icon.icns",
        bundle_identifier="com.auroralist.app",
        info_plist={
            "CFBundleName": "AuroraList",
            "CFBundleDisplayName": "AuroraList",
            "CFBundleShortVersionString": "1.0.12",
            "CFBundleVersion": "1.0.12",
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": "11.0",
            # Python 进程负责服务；原生窗口组件负责唯一的 Dock 图标和菜单。
            "LSUIElement": True,
        },
    )
    # Put the signed native window in the installation itself. No first-launch
    # extraction or edits to ~/.flet (which can belong to another application).
    import flet_desktop
    import subprocess
    from aeclone.macos_app import prepare_native_view
    native_archive = Path(flet_desktop.__file__).parent / "app/flet-macos.tar.gz"
    prepare_native_view(native_archive, Path(app.name) / "Contents/Helpers", Path("assets/icon.icns"))
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", app.name], check=True)

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

a = Analysis(
    ["main.py"],
    pathex=[str(Path("src").resolve())],
    binaries=flet_bins,
    datas=[("assets", "assets")] + flet_datas,
    hiddenimports=collect_submodules("aeclone") + flet_hidden + ["browser_cookie3", "Crypto", "Crypto.Cipher", "Crypto.Cipher.AES", "Crypto.Protocol", "Crypto.Protocol.KDF"],
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
    icon=None,
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

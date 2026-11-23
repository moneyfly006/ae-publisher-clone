"""macOS native window identity, prepared before launch rather than patched in shared Flet caches."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

APP_NAME = "AuroraList"
VIEW_IDENTIFIER = "com.auroralist.app.window"


def prepare_native_view(archive: Path, destination: Path, icon: Path) -> Path:
    """Build an independently signed window bundle from the installed Flet archive."""
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as package:
        package.extractall(destination, filter="data")
    app = destination / "Flet.app"
    app = app.rename(destination / f"{APP_NAME}.app")
    plist = app / "Contents/Info.plist"
    data = plistlib.loads(plist.read_bytes())
    data.update(CFBundleName=APP_NAME, CFBundleDisplayName=APP_NAME,
                CFBundleIdentifier=VIEW_IDENTIFIER, LSUIElement=False,
                CFBundleIconFile="AppIcon.icns")
    plist.write_bytes(plistlib.dumps(data))
    shutil.copy2(icon, app / "Contents/Resources/AppIcon.icns")
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
                   check=True, capture_output=True, text=True)
    return app


def configure_native_view(assets: str) -> None:
    """Select this application's branded window, never another app's shared cache."""
    if sys.platform != "darwin":
        return
    if getattr(sys, "frozen", False):
        directory = Path(sys.executable).parent.parent / "Helpers"
        if not (directory / f"{APP_NAME}.app/Contents/Info.plist").is_file():
            raise RuntimeError("安装包缺少 AuroraList 桌面窗口组件，请重新安装完整应用。")
    else:
        import flet_desktop
        from .config import config_dir

        archive = Path(flet_desktop.__file__).parent / "app/flet-macos.tar.gz"
        icon = Path(assets) / "icon.icns"
        digest = hashlib.sha256(archive.read_bytes() + icon.read_bytes() +
                                Path(__file__).read_bytes()).hexdigest()[:20]
        cache = config_dir() / "native-view"
        cache.mkdir(parents=True, exist_ok=True)
        directory = cache / digest
        if not directory.exists():
            staging = Path(tempfile.mkdtemp(prefix="prepare-", dir=cache))
            try:
                prepare_native_view(archive, staging, icon)
                try:
                    staging.rename(directory)
                except OSError:
                    if not (directory / f"{APP_NAME}.app/Contents/Info.plist").is_file():
                        raise
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
    # An inherited development override must not launch a different application's window.
    os.environ["FLET_VIEW_PATH"] = str(directory)

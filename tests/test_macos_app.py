"""Native Dock identity and launcher selection regression checks."""
from pathlib import Path
import io
import os
import plistlib
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.macos_app import configure_native_view, prepare_native_view, VIEW_IDENTIFIER


class NativeIdentityTests(unittest.TestCase):
    def test_archive_is_branded_without_changing_executable_or_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "stock.tar.gz"
            original = plistlib.dumps(dict(CFBundleName="Flet", CFBundleExecutable="Flet"))
            with tarfile.open(archive, "w:gz") as package:
                for name, data in (("Contents/Info.plist", original),
                                   ("Contents/Resources/AppIcon.icns", b"stock-icon")):
                    member = tarfile.TarInfo("Flet.app/" + name)
                    member.size = len(data)
                    package.addfile(member, io.BytesIO(data))
            icon = root / "icon.icns"
            icon.write_bytes(b"aurora-icon")
            with patch("aeclone.macos_app.subprocess.run") as signing:
                app = prepare_native_view(archive, root / "helper", icon)
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(app.name, "AuroraList.app")
            self.assertEqual(info["CFBundleName"], "AuroraList")
            self.assertEqual(info["CFBundleDisplayName"], "AuroraList")
            self.assertEqual(info["CFBundleIdentifier"], VIEW_IDENTIFIER)
            self.assertEqual(info["CFBundleExecutable"], "Flet")
            self.assertIs(info["LSUIElement"], False)
            self.assertEqual((app / "Contents/Resources/AppIcon.icns").read_bytes(), icon.read_bytes())
            signing.assert_called_once()
            self.assertTrue(signing.call_args.kwargs["check"])
            with tarfile.open(archive) as package:
                self.assertEqual(package.extractfile("Flet.app/Contents/Info.plist").read(), original)

    def test_frozen_launch_selects_own_helper_and_overrides_inherited_path(self):
        with tempfile.TemporaryDirectory() as folder:
            contents = Path(folder) / "AuroraList.app/Contents"
            plist = contents / "Helpers/AuroraList.app/Contents/Info.plist"
            plist.parent.mkdir(parents=True)
            plist.write_bytes(plistlib.dumps({}))
            with patch("sys.platform", "darwin"), patch.object(sys, "frozen", True, create=True), \
                 patch("sys.executable", str(contents / "MacOS/AuroraList")), \
                 patch.dict(os.environ, FLET_VIEW_PATH="/wrong/shared/cache"):
                configure_native_view("unused")
                self.assertEqual(os.environ["FLET_VIEW_PATH"], str(contents / "Helpers"))

    def test_incomplete_bundle_does_not_silently_launch_stock_flet(self):
        with tempfile.TemporaryDirectory() as folder, patch("sys.platform", "darwin"), \
             patch.object(sys, "frozen", True, create=True), patch("sys.executable", folder + "/MacOS/AuroraList"):
            with self.assertRaisesRegex(RuntimeError, "桌面窗口组件"):
                configure_native_view("unused")

    def test_other_platforms_keep_their_launcher(self):
        with patch("sys.platform", "win32"), patch.dict(os.environ, FLET_VIEW_PATH="windows-view"):
            configure_native_view("unused")
            self.assertEqual(os.environ["FLET_VIEW_PATH"], "windows-view")


if __name__ == "__main__":
    unittest.main()

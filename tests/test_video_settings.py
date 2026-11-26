"""Video settings affect the publisher and actual FFmpeg output."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PIL import Image
from aeclone.config import AppConfig
from aeclone.services.publisher import Publisher, PublishResult
from aeclone.services.video import generate_carousel_video, find_ffmpeg

class VideoTests(unittest.TestCase):
    def test_generated_source_honors_options_and_replaces_existing_video(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.png"
            Image.new("RGB", (320, 180), "red").save(source)
            pub = Publisher.__new__(Publisher)
            pub.cfg = AppConfig(dict(video_source="generated", video_slot_seconds=1.8,
                video_transition_seconds=.2, video_size="640x480", video_cover=False,
                video_fade_edges=False, video_output_dir=str(root)))
            pub.dxm = object()
            pub._fetch = lambda url, workdir: Path(url)
            product = {"images": [str(source)]}
            with patch("aeclone.services.video.generate_carousel_video") as generate, \
                 patch("aeclone.integrations.cos_upload.upload_video", return_value="test-video") as upload:
                result = PublishResult("qa", True)
                # 视频来源=生成即为唯一规则：不依赖旧的“覆盖已有视频”开关。
                pub._prepare_video(product, result, {"raw": {"videoUrl": "existing"}}, False)
                generate.assert_called_once()
                self.assertEqual(generate.call_args.kwargs, dict(slot_seconds=1.8,
                    transition_seconds=.2, size="640x480", cover=False, fade_edges=False))
                self.assertEqual(generate.call_args.args[1].parent, root)
                self.assertEqual(product["csp_video_id"], "test-video")
                upload.assert_called_once()

    @unittest.skipUnless(find_ffmpeg(), "FFmpeg unavailable")
    def test_actual_single_and_multi_image_videos_have_requested_size_and_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = []
            for i, color in enumerate(("red", "blue", "green")):
                path = root / f"image-{i}.png"
                Image.new("RGB", (320, 180), color).save(path)
                paths.append(path)
            ffprobe = str(Path(find_ffmpeg()).with_name("ffprobe"))
            for count in (1, 3):
                for cover, fade in ((True, True), (False, False)):
                    with self.subTest(count=count, cover=cover, fade=fade):
                        out = generate_carousel_video(paths[:count], root / "out.mp4", .8, .1,
                                                      "320x320", cover, fade)
                        data = json.loads(subprocess.check_output([ffprobe, "-v", "quiet",
                            "-print_format", "json", "-show_streams", "-show_format", str(out)]))
                        stream = next(s for s in data["streams"] if s["codec_type"] == "video")
                        self.assertEqual((stream["width"], stream["height"]), (320, 320))
                        self.assertAlmostEqual(float(data["format"]["duration"]), count*.8-(count-1)*.1, delta=.08)
                        self.assertGreater(out.stat().st_size, 500)

if __name__ == "__main__": unittest.main(verbosity=2)

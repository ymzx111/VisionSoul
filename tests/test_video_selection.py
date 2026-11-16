"""验证选帧的取舍，以及原视频高清提取的时间、旋转、缓存与失败边界。"""
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import av
import httpx
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.core.config import Settings, get_settings
from app.main import app
from app.services.deepseek import DeepSeekClient
from app.services.video_details import detail_path, extract_details
from app.services.video_selection import measure_frame, select_detail_frames
from app.services.videos import SampleFrame, VideoService
from test_video import make_video, result_payload
from test_video_comparison import comparison


def patterned():
    image = Image.new("RGB", (320, 180), "#777777")
    draw = ImageDraw.Draw(image)
    for x in range(12, 310, 12):
        draw.line((x, 12, x, 168), fill="#eeeeee", width=3)
    return image


def select(images, limit=4):
    frames = [SampleFrame(index=i, time_seconds=i * 0.5, seen_at=[i * 0.5], url="") for i in range(len(images))]
    return select_detail_frames(frames, {i: measure_frame(image) for i, image in enumerate(images)}, limit)


class SelectionTests(unittest.TestCase):
    def test_dark_transition_avoided_but_dark_code_editor_preserved(self):
        dark=Image.new('RGB',(320,180),(8,8,8));ImageDraw.Draw(dark).ellipse((90,40,220,170),fill=(14,14,14))
        editor=Image.new('RGB',(320,180),'#151515');draw=ImageDraw.Draw(editor)
        for y in range(12,160,16):draw.text((12,y),'print(total) # NameError',fill='white')
        selected=select([dark,editor,Image.new('RGB',editor.size,'blue')])
        self.assertNotIn(0,selected);self.assertIn(1,selected)

    def test_similar_nearby_defocus_does_not_spend_detail_budget(self):
        sharp = patterned()
        blurred = sharp.filter(ImageFilter.GaussianBlur(6))
        chosen = select([blurred, sharp, Image.new('RGB', sharp.size, 'blue')])
        self.assertNotIn(0, chosen)
        self.assertIn(1, chosen)
        self.assertIn(2, chosen)

    def test_entirely_blurred_video_keeps_a_fallback(self):
        blurred = patterned().filter(ImageFilter.GaussianBlur(6))
        self.assertTrue(select([blurred, blurred.copy()]))

    def test_sharp_representative_wins_over_blurred_view(self):
        image = patterned()
        blurred = image.filter(ImageFilter.GaussianBlur(4))
        self.assertGreater(measure_frame(image).quality, measure_frame(blurred).quality)
        self.assertEqual(select([blurred, image], limit=1), [1])

    def test_extreme_exposure_does_not_displace_usable_views(self):
        images = [Image.new("RGB", (100, 100), color) for color in ["black", "red", "white", "blue"]]
        self.assertEqual(select(images), [1, 3])

    def test_white_page_with_black_text_is_not_rejected_as_overexposed(self):
        page = Image.new("RGB", (320, 180), "white")
        draw = ImageDraw.Draw(page)
        for y in range(12, 165, 12):
            draw.text((12, y), "Budget 2026 travel -20%", fill="black")
        images = [Image.new("RGB", page.size, "white"), page, Image.new("RGB", page.size, "blue")]
        selected = select(images)
        self.assertIn(1, selected)
        self.assertIn(2, selected)
        self.assertNotIn(0, selected)

    def test_near_duplicates_do_not_fill_budget_but_overview_input_is_preserved(self):
        image = patterned()
        images = [image.copy() for _ in range(20)] + [Image.new("RGB", image.size, "blue")]
        images[12] = image.filter(ImageFilter.GaussianBlur(0.3))
        selected = select(images)
        self.assertEqual(len(images), 21)
        self.assertEqual(len(selected), 2)
        self.assertIn(20, selected)

    def test_local_new_content_survives_when_most_background_is_unchanged(self):
        before = Image.new("RGB", (320, 180), "#777777")
        after = before.copy()
        ImageDraw.Draw(after).rectangle((20, 20, 100, 80), fill="red")
        self.assertEqual(select([before, after]), [0, 1])

    def test_distinct_views_keep_at_most_four_even_with_large_requested_limit(self):
        images = [Image.new("RGB", (100, 100), color) for color in ["red", "blue", "green", "yellow", "cyan", "purple"]]
        selected = select(images, limit=100)
        self.assertEqual(len(selected), 4)
        self.assertEqual(len(set(selected)), 4)
        self.assertEqual(selected, sorted(selected))

    def test_time_coverage_breaks_ties_between_equally_different_views(self):
        frames = [SampleFrame(index=i, time_seconds=t, seen_at=[t], url="") for i, t in enumerate([0, 1, 2, 29])]
        # 等质量、等距离的四个布局，第二名应倾向较晚的画面。
        from app.services.video_selection import FrameQuality
        metrics = {}
        for i in range(4):
            signature = bytearray([60] * 768 + [0] * 256)
            for y in range(4):
                for x in range(i * 4, i * 4 + 4):
                    start = (y * 16 + x) * 3
                    signature[start:start + 3] = bytes([180] * 3)
            metrics[i] = FrameQuality(0.8, 0.8, 1, 0.8, bytes(signature))
        self.assertEqual(select_detail_frames(frames, metrics, limit=2), [0, 3])

    def test_empty_missing_zero_budget_and_all_dark_are_safe(self):
        self.assertEqual(select([]), [])
        self.assertEqual(select([patterned()], limit=0), [])
        self.assertEqual(select([Image.new("RGB", (16, 16), "black")] * 4), [0])
        self.assertEqual(select_detail_frames([SampleFrame(index=0, time_seconds=0, seen_at=[0], url="")], {}), [])


class DetailExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        path = Path(cls.fixtures.name) / "large-rotated.mp4"
        with av.open(str(path), "w", format="mp4") as output:
            stream = output.add_stream("libx264", rate=2)
            stream.width, stream.height, stream.pix_fmt = 2400, 1350, "yuv420p"
            stream.options = {"preset": "ultrafast", "crf": "18"}
            stream.set_display_rotation(90)
            for color in ["red", "blue", "green"] + ["yellow"] * 23:
                image = Image.new("RGB", (2400, 1350), color)
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    output.mux(packet)
            for packet in stream.encode():
                output.mux(packet)
        cls.data = path.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, video_storage_dir=root / "videos",
            chat_database_path=root / "chat.sqlite3", deepseek_api_key="test-not-a-key")
        self.calls = []
        def handler(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(200, json={"model": "deepseek-flash", "choices": [{"message": {
                "content": json.dumps({**result_payload(), "comparison": comparison()})}, "finish_reason": "stop"}]})
        self.service = VideoService(self.settings, DeepSeekClient(self.settings, httpx.MockTransport(handler)))
        self.video = self.service.upload(BytesIO(self.data))
        self.directory = self.settings.video_storage_dir / self.video.video_id

    def test_only_inspection_extracts_source_resolution_and_reuses_cache(self):
        manifest = (self.directory / "manifest.json").read_bytes()
        self.service._observe(self.video)
        self.assertFalse(list(self.directory.glob("hq-*.jpg")))
        self.assertTrue(all(part["image_url"]["detail"] == "low" for part in self.calls[0]["messages"][-1]["content"] if part["type"] == "image_url"))
        with patch("app.services.videos.subprocess.run", wraps=subprocess.run) as decode:
            selected = self.service._detail_frames(self.video, self.directory)
            self.assertEqual(decode.call_count, 1)
            repeated = self.service._detail_frames(self.video, self.directory)
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(selected, repeated)
        self.assertLessEqual(len(selected), 4)
        for index, path in selected.items():
            with Image.open(path) as image:
                self.assertEqual(image.size, (1080, 1920))
            with Image.open(self.directory / f"detail-{index}.jpg") as image:
                self.assertEqual(image.size, (720, 1280))
        self.assertEqual((self.directory / "manifest.json").read_bytes(), manifest)
        self.assertFalse(list(self.directory.glob(".upload-*")))
        # 真正的分析仍只有一次上游请求，并在原位置替换高清图。
        self.calls.clear()
        self.service.inspect(self.video.video_id, "细看", [])
        self.assertEqual(len(self.calls), 1)
        images = [part for part in self.calls[0]["messages"][-1]["content"] if part["type"] == "image_url"]
        self.assertEqual(len(images), len(self.video.frames))
        self.assertEqual(sum(part["image_url"]["detail"] == "high" for part in images), len(selected))
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            index = next(iter(selected))
            response = client.get(self.video.frames[index].detail_url)
            self.assertEqual(response.status_code, 200)
            with Image.open(BytesIO(response.content)) as image:
                self.assertEqual(image.size, (1080, 1920))

    def test_timeout_and_decode_failure_fall_back_without_extra_model_calls(self):
        for failure in [subprocess.TimeoutExpired("details", 1), None]:
            with self.subTest(failure=failure):
                with patch("app.services.videos.subprocess.run", side_effect=failure,
                           return_value=subprocess.CompletedProcess([], 1)):
                    self.service.inspect(self.video.video_id, "细看", [])
                self.assertFalse(list(self.directory.glob("hq-*.jpg")))
                self.assertFalse(list(self.directory.glob(".upload-*")))
        self.assertEqual(len(self.calls), 2)

    def test_small_source_never_upscaled_and_time_matches_original(self):
        source = Path(self.temp.name) / "small.mp4"
        make_video(source, seconds=3, varying=True)
        video = self.service.upload(BytesIO(source.read_bytes()))
        directory = self.settings.video_storage_dir / video.video_id
        output = Path(self.temp.name) / "details"
        output.mkdir()
        target = video.frames[-1]
        extract_details(directory, output, [{"index": target.index, "time_seconds": target.time_seconds}])
        with Image.open(detail_path(output, target.index)) as image:
            self.assertEqual(image.size, (96, 64))
            self.assertAlmostEqual(image.getpixel((20, 20))[0], 29, delta=8)
        with self.assertRaisesRegex(ValueError, "不匹配"):
            extract_details(directory, output, [{"index": 0, "time_seconds": 0.123}])

    def test_invalid_targets_rejected_before_decode(self):
        for targets in [[], [{"index": 61, "time_seconds": 0}], [{"index": 0, "time_seconds": float("nan")}],
                        [{"index": i, "time_seconds": 0} for i in range(5)]]:
            with self.assertRaises(ValueError):
                extract_details(self.directory, Path(self.temp.name), targets)

    def test_source_missing_uses_existing_detail_and_old_video_without_details_stays_low(self):
        (self.directory / "source.mp4").unlink()
        selected = self.service._detail_frames(self.video, self.directory)
        self.assertTrue(selected)
        self.assertTrue(all(path.name.startswith("detail-") for path in selected.values()))
        for path in self.directory.glob("detail-*.jpg"):
            path.unlink()
        self.service.inspect(self.video.video_id, "细看", [])
        self.assertIn("本轮只有概览图", self.calls[0]["messages"][0]["content"])
        self.assertFalse(any(part["image_url"]["detail"] == "high" for part in self.calls[0]["messages"][-1]["content"] if part["type"] == "image_url"))

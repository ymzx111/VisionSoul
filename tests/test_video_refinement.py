"""验证补抽取到真实源帧、运算符变化保护与锐化兜底。"""
import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

import av
import httpx
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.core.config import Settings, get_settings
from app.main import app
from app.services.audio_evidence import AudioTranscript, SpeechSegment
from app.services.deepseek import DeepSeekClient
from app.services.video_details import adjusted_path, adjustment_path
from app.services.video_refinement import choose_detail
from app.services.videos import VideoService
from test_video_reading import page


class RefinementTests(unittest.TestCase):
    def test_nearby_sharp_source_wins_over_enhancement(self):
        sharp = page("Compute a <= b.")
        anchor = sharp.filter(ImageFilter.GaussianBlur(2))
        kind, time, image = choose_detail({1: anchor, 1.25: sharp}, 1)
        self.assertEqual((kind, time), ("nearby", 1.25))
        self.assertIs(image, sharp)

    def test_small_operator_change_is_not_a_replacement(self):
        sharp = page("Compute a <= b.")
        changed = page("Compute a >= b.")
        anchor = sharp.filter(ImageFilter.GaussianBlur(2))
        result = choose_detail({1: anchor, 1.25: changed}, 1)
        self.assertEqual(result[:2], ("enhanced", 1))
        heavy = sharp.filter(ImageFilter.GaussianBlur(4))
        self.assertIsNone(choose_detail({0.75: sharp, 1: heavy, 1.25: changed}, 1))
        self.assertEqual(choose_detail({0.75: sharp, 1: heavy, 1.25: sharp}, 1)[:2], ("nearby", 0.75))

    def test_fallback_keeps_source_time_and_never_modifies_original_pixels(self):
        sharp = page()
        anchor = sharp.filter(ImageFilter.GaussianBlur(2))
        original = anchor.tobytes()
        kind, time, image = choose_detail({1: anchor}, 1)
        self.assertEqual((kind, time), ("enhanced", 1))
        self.assertNotEqual(image.tobytes(), original)
        self.assertEqual(anchor.tobytes(), original)
        self.assertIsNone(choose_detail({1: sharp.filter(ImageFilter.GaussianBlur(8))}, 1))

    def test_h264_nearby_frame_reaches_model_audio_and_detail_endpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "fixture.mp4"
            sharp = page("Compute a <= b.")
            blur = sharp.filter(ImageFilter.GaussianBlur(2))
            red, blue = [Image.new("RGB", sharp.size, color) for color in ("red", "blue")]
            with av.open(str(source), "w", format="mp4") as container:
                stream = container.add_stream("libx264", rate=8)
                stream.width, stream.height = sharp.size
                stream.pix_fmt = "yuv420p"
                stream.options = {"preset": "ultrafast", "crf": "18"}
                for image in [red] * 7 + [sharp, blur, sharp] + [blue] * 10:
                    for packet in stream.encode(av.VideoFrame.from_image(image)):
                        container.mux(packet)
                for packet in stream.encode():
                    container.mux(packet)
            settings = Settings(_env_file=None, deepseek_api_key="test", audio_enabled=False,
                video_storage_dir=root / "videos", chat_database_path=root / "chat.sqlite3")
            requests = []

            def handler(request):
                body = json.loads(request.content)
                requests.append(body)
                actual = json.loads(adjustment_path(directory, anchor.index).read_text(encoding="utf-8"))["time_seconds"]
                value = {"reply": "测试回复", "summary": "题目画面", "observations": [
                    {"frame_index": anchor.index, "keywords": ["题目"], "description": "测试描述", "uncertain": False}],
                    "audio_links": [{"segment_id": 0, "frame_index": anchor.index, "frame_time": actual,
                        "relation": "uncertain", "detail": "测试对白关联"}]}
                return httpx.Response(200, json={"model": "test", "choices": [
                    {"message": {"content": json.dumps(value)}, "finish_reason": "stop"}]})

            service = VideoService(settings, DeepSeekClient(settings, httpx.MockTransport(handler)),
                                   system_prompt="测试", isolated=True)
            info = service.upload(BytesIO(source.read_bytes()))
            directory = settings.video_storage_dir / info.video_id
            anchor = next(frame for frame in info.frames if frame.time_seconds == 1)
            info = info.model_copy(update={"audio": AudioTranscript(status="ready", segments=[
                SpeechSegment(id=0, start=0.8, end=1.2, text="请看这道题。")])})
            result = service._observe(info, "读题")
            adjustment = result.detail_selection.adjustments[anchor.index]
            self.assertEqual(adjustment.kind, "nearby")
            self.assertIn(adjustment.time_seconds, (0.875, 1.125))
            self.assertEqual(result.audio_links[0].frame_time, adjustment.time_seconds)
            self.assertEqual(service._snapshot(info, result)["observations"][0]["time_seconds"], adjustment.time_seconds)
            self.assertEqual(len(requests), 1)
            parts = requests[0]["messages"][-1]["content"]
            images = [part["image_url"] for part in parts if part["type"] == "image_url"]
            self.assertEqual(len(images), len(info.frames))
            self.assertLessEqual(sum(part["detail"] == "high" for part in images), 4)
            self.assertIn(f"{anchor.index}@{adjustment.time_seconds:g}", " ".join(part.get("text", "") for part in parts))
            sent = base64.b64decode(images[anchor.index]["url"].split(",", 1)[1])
            self.assertEqual(sent, adjusted_path(directory, anchor.index, "nearby").read_bytes())
            app.dependency_overrides[get_settings] = lambda: settings
            self.addCleanup(app.dependency_overrides.pop, get_settings)
            with TestClient(app) as client:
                response = client.get(f"/api/videos/{info.video_id}/frames/{anchor.index}/detail?variant=nearby")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, sent)

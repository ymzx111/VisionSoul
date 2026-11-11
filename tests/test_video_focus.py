"""重点区间的真实解码、追问、图像引用和无效范围回归。"""
import base64
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

import av
import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.core.config import Settings, get_settings
from app.main import app
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient, DeepSeekError
from test_video import result_payload


class VideoFocusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "focus.mp4"
        colors = ["black"] * 6 + ["red", "white", "blue", "yellow", "green", "purple"]
        with av.open(str(source), "w", format="mp4") as container:
            stream = container.add_stream("libx264", rate=8)
            stream.width, stream.height, stream.pix_fmt = 160, 96, "yuv420p"
            for color in colors:
                for _ in range(4):
                    image = Image.new("RGB", (160, 96), color)
                    for packet in stream.encode(av.VideoFrame.from_image(image)):
                        container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        self.calls = []
        settings = Settings(_env_file=None, deepseek_api_key="mock", audio_enabled=False,
                            video_storage_dir=root / "videos", chat_database_path=root / "chat.sqlite3")

        def handler(request):
            body = json.loads(request.content)
            self.calls.append(body)
            parts = body["messages"][-1]["content"]
            payload = result_payload()
            if isinstance(parts, list):
                for part in parts:
                    match = re.fullmatch(r"(\d+)@([\d.]+) 重点区间原始高清画面", part.get("text", ""))
                    if match and not float(match[2]).is_integer():
                        payload["observations"][1]["frame_index"] = int(match[1])
                        break
            return httpx.Response(200, json={"model": "mock", "choices": [
                {"message": {"content": json.dumps(payload, ensure_ascii=False)}, "finish_reason": "stop"}]})

        self.settings = settings
        self.chat = ChatService(settings, DeepSeekClient(settings, httpx.MockTransport(handler)))
        self.video = self.chat.videos.upload(BytesIO(source.read_bytes()))

    def send(self, message, request_id, conversation_id=None):
        return self.chat.send(space="work", conversation_id=conversation_id, request_id=request_id * 32,
                              message=message, image_id=None, video_id=self.video.video_id)

    def test_followup_uses_half_second_frames_and_preserves_review_links(self):
        original = self.send("看看这个视频", "a")
        result = self.send("重点看 3–5 秒", "b", original["conversation_id"])
        selection = result["video_context"]["detail_selection"]
        self.assertEqual(selection["focus_range_ms"], [3000, 5000])
        focus = {int(index): time for index, time in selection["focus_frames"].items()}
        self.assertTrue(set(focus.values()) <= {3000, 3500, 4000, 4500, 5000})
        self.assertEqual(len(focus), 4)
        parts = self.calls[-1]["messages"][-1]["content"]
        self.assertEqual(sum(part.get("image_url", {}).get("detail") == "high" for part in parts), 4)
        self.assertEqual(len(self.calls), 2)
        observation = next(row for row in result["video_context"]["observations"] if row["frame_index"] >= 61)
        time = focus[observation["frame_index"]]
        self.assertEqual(observation["time_seconds"], time / 1000)
        label = f'{observation["frame_index"]}@{time / 1000:g} 重点区间原始高清画面'
        position = next(index for index, part in enumerate(parts) if part.get("text") == label)
        image = base64.b64decode(parts[position + 1]["image_url"]["url"].split(",", 1)[1])
        replay = self.send("重点看 3–5 秒", "b", original["conversation_id"])
        self.assertEqual(replay["video_context"]["detail_selection"], selection)
        self.assertEqual(len(self.calls), 2)

        app.dependency_overrides[get_settings] = lambda: self.settings
        self.addCleanup(app.dependency_overrides.pop, get_settings)
        with TestClient(app) as client:
            path = f"/api/videos/{self.video.video_id}/focus-frames/{time}"
            self.assertEqual(client.get(path).content, image)

    def test_invalid_interval_is_rejected_before_model_call(self):
        for message in ("重点看 3–2 秒", "重点看 0–6 秒", "重点看 5–7 秒"):
            with self.subTest(message=message), self.assertRaises(DeepSeekError) as error:
                self.send(message, "a")
            self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.calls, [])

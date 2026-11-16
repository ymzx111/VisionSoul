"""本机合成屏幕视频验证高清读题链路；模拟API，不评估真实模型准确率。"""
from io import BytesIO
import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import av
import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import Settings
from app.core.chat_space import WORK_PROMPT
from app.services.chat import ChatService
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.frame_quality import measure_frame, text_distance, visual_distance
from app.services.video_reading import wants_text_detail, DetailSelection
from app.services.video_selection import select_detail_frames
from app.services.videos import VideoService, SampleFrame
from test_video import result_payload


def page(text="Read two integers a and b; output a + b.", dark=False):
    image = Image.new("RGB", (1280, 720), "#171717" if dark else "white")
    draw = ImageDraw.Draw(image)
    ink = "white" if dark else "black"
    font = ImageFont.load_default(size=24)
    draw.text((80, 50), "Programming exercise", font=font, fill=ink)
    for y, line in enumerate([text, "Input: a b, integers in [-100, 100].", "Output: one integer.", "Example: 3 5 -> 8"]):
        draw.text((80, 180 + 65 * y), line, font=font, fill=ink)
    return image


def select(images, limit=4):
    frames = [SampleFrame(index=i, time_seconds=i, seen_at=[i], url="") for i in range(len(images))]
    metrics = {i: measure_frame(image, text=True) for i, image in enumerate(images)}
    return select_detail_frames(frames, metrics, limit=limit, profile="text")


def make_reading_video(path):
    clear = page()
    other = page("Compute the larger of two integers a and b.")
    images = [clear.filter(ImageFilter.GaussianBlur(4)), clear, clear.copy(), other, other.copy(), page(dark=True)]
    images.extend([images[-1]] * 7)
    with av.open(str(path), "w", format="mp4") as output:
        stream = output.add_stream("libx264", rate=2)
        stream.width, stream.height, stream.pix_fmt = 1280, 720, "yuv420p"
        stream.options = {"preset": "ultrafast", "crf": "18"}
        for image in images:
            for _ in range(2):
                for packet in stream.encode(av.VideoFrame.from_image(image)):
                    output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)


class TextSelectionTests(unittest.TestCase):
    def test_task_routing_uses_explicit_question_and_not_every_message(self):
        for question in ["请根据题目编写C程序", "识别屏幕小字", "代码运行报错"]:
            self.assertTrue(wants_text_detail(question), question)
        for question in ["湖水好看吗", "谢谢你", "不用看题目，概括风景", "你还记得我吗"]:
            self.assertFalse(wants_text_detail(question), question)

    def test_clear_text_beats_same_page_defocus_and_flat_transition(self):
        clear = page()
        selected = select([clear.filter(ImageFilter.GaussianBlur(4)), clear, Image.new("RGB", clear.size, "black")], limit=1)
        self.assertEqual(selected, [1])

    def test_text_pages_with_local_changes_survive_without_filling_duplicates(self):
        first, second = page(), page("Compute the larger of two integers a and b.")
        selected = select([first, first.copy(), second, second.copy()])
        self.assertEqual(selected, [0, 2])

    def test_dark_editor_and_white_page_are_both_eligible(self):
        self.assertEqual(select([page(), page(dark=True)]), [0, 1])

    def test_fine_layout_detects_small_edit_against_same_large_background(self):
        first = Image.new("RGB", (1280, 720), "white")
        second = first.copy()
        font = ImageFont.load_default(size=20)
        ImageDraw.Draw(first).text((600, 300), "n <= 100", font=font, fill="black")
        ImageDraw.Draw(second).text((600, 300), "n >= 100", font=font, fill="black")
        a, b = measure_frame(first, text=True), measure_frame(second, text=True)
        self.assertGreater(text_distance(a, b), visual_distance(a, b))
        self.assertEqual(select([first, second]), [0, 1])

    def test_bounded_selection_and_fully_unreadable_fallback(self):
        many = [page(f"Exercise {i}: output {i} times n") for i in range(10)]
        chosen = select(many, limit=100)
        self.assertLessEqual(len(chosen), 4)
        blurred = page().filter(ImageFilter.GaussianBlur(10))
        self.assertTrue(select([blurred, blurred.copy()]))

    def test_minor_compression_does_not_use_four_slots_for_same_page(self):
        original = page()
        images = [original]
        for quality in [88, 90, 92]:
            buffer = BytesIO()
            original.save(buffer, "JPEG", quality=quality)
            with Image.open(BytesIO(buffer.getvalue())) as decoded:
                images.append(decoded.convert("RGB"))
        self.assertEqual(len(select(images)), 1)

    def test_sparse_but_readable_page_not_discarded_by_denser_different_page(self):
        sparse = Image.new("RGB", (1280, 720), "white")
        ImageDraw.Draw(sparse).text((600, 300), "n <= 100", font=ImageFont.load_default(size=20), fill="black")
        self.assertEqual(select([sparse, page()]), [0, 1])


class ReadingChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="visionsoul-reading-video-")
        cls.addClassCleanup(cls.fixture.cleanup)
        path = Path(cls.fixture.name) / "reading.mp4"
        make_reading_video(path)
        cls.data = path.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-reading-chat-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, deepseek_api_key="test-only-invalid", audio_enabled=False,
            chat_database_path=root/"chat.sqlite3", role_database_path=root/"roles.sqlite3", video_storage_dir=root/"videos")
        self.calls = []
        self.long_reply = False
        self.overview_reply = None
        self.overview_observations = None
        self.forge_selection = False
        def handler(request):
            body = json.loads(request.content)
            self.calls.append(body)
            value = result_payload()
            if self.overview_reply and not any(part["type"] == "image_url" and part["image_url"]["detail"] == "high"
                    for part in body["messages"][-1]["content"] if isinstance(part, dict)):
                value["reply"] = self.overview_reply
                if self.overview_observations:
                    value["observations"] = self.overview_observations
            elif self.long_reply:
                code = "int main(void) {\n" + "    /* 保留完整题解。 */\n" * 16 + "    return 0;\n}\n"
                value["reply"] = f"[文件名: main.c]\n```c\n{code}```"
            else:
                value["reply"] = "请核对题意，关键条件不清时补清楚画面。"
            if self.forge_selection:
                value["detail_selection"] = {"profile":"text", "frame_indices":[60], "source_indices":[60]}
            answer = json.dumps(value, ensure_ascii=False) if body.get("response_format") else "沿用已读内容解释。"
            return httpx.Response(200, json={"model":"test", "choices":[{"message":{"content":answer}, "finish_reason":"stop"}]})
        self.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        self.videos = VideoService(self.settings, self.client, system_prompt=WORK_PROMPT, isolated=True)
        self.video = self.videos.upload(BytesIO(self.data))
        self.chat = ChatService(self.settings, self.client)
        self.counter = 0

    def send(self, question, conversation=None, request=None):
        self.counter += 1
        return self.chat.send(space="work", conversation_id=conversation, request_id=request or f"{self.counter:032x}",
            message=question, image_id=None, video_id=None if conversation else self.video.video_id)

    def pictures(self):
        return [part for message in self.calls[-1]["messages"] if isinstance(message["content"], list)
                for part in message["content"] if part["type"] == "image_url"]

    def test_clear_programming_question_uses_overview_without_extra_request(self):
        self.long_reply = True
        with patch("app.services.chat.RoleStore", side_effect=AssertionError("work read role")):
            result = self.send("请识别题目，编写C程序", request="a"*32)
        self.assertEqual(len(self.calls), 1)
        pictures = self.pictures()
        self.assertEqual(len(pictures), len(self.video.frames))
        self.assertTrue(all(part["image_url"]["detail"] == "low" for part in pictures))
        selected = result["video_context"]["detail_selection"]
        self.assertEqual(selected["profile"], "text")
        self.assertEqual(selected["frame_indices"], [])
        self.assertGreater(len(result["assistant_message"]["content"]), 240)
        self.assertIn("不受通用80字限制", self.calls[0]["messages"][0]["content"])
        self.assertNotIn("supporting_features", self.calls[0]["messages"][0]["content"])
        replay = self.send("请识别题目，编写C程序", request="a"*32)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.calls), 1)

    def test_old_low_report_reads_new_requirements_then_explicitly_rereads_high(self):
        first = self.send("请概括视频")
        self.assertTrue(all(part["image_url"]["detail"] == "low" for part in self.pictures()))
        self.long_reply = True
        second = self.send("请按要求6编写C程序", first["conversation_id"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(part["image_url"]["detail"] == "low" for part in self.pictures()))
        self.assertEqual(second["video_context"]["opening_message"], "请概括视频")
        self.send("解释这段代码的循环", first["conversation_id"])
        self.assertEqual(self.pictures(), [])
        self.send("重新读题，看看输入条件", first["conversation_id"])
        high = [part for part in self.pictures() if part["image_url"]["detail"] == "high"]
        self.assertTrue(1 <= len(high) <= 4)
        for part in high:
            with Image.open(BytesIO(base64.b64decode(part["image_url"]["url"].split(",")[1]))) as image:
                self.assertEqual(image.size, (1280, 720))
        self.assertEqual(len(self.calls), 4)

    def test_failed_source_extract_uses_saved_details_and_does_not_retry_model(self):
        with patch("app.services.videos.subprocess.run", side_effect=__import__('subprocess').TimeoutExpired("test", 1)):
            result = self.send("重新读取题目")
        selected = result["video_context"]["detail_selection"]
        self.assertTrue(selected["frame_indices"])
        self.assertEqual(selected["source_indices"], [])
        self.assertEqual(len(self.calls), 1)

    def test_generic_ordinary_followup_does_not_trigger_high_detail(self):
        first = self.send("看看这段风景")
        self.send("刚才出现了什么？", first["conversation_id"])
        self.assertEqual(self.pictures(), [])
        self.assertEqual(len(self.calls), 2)

    def test_video_request_to_film_more_triggers_review_before_asking(self):
        self.overview_reply = ("图中可见边表和规则，但样例下方说明尚未显示，无法确定最终输出格式。"
                               "请拍一下题面下半部分。")
        self.long_reply = True
        result = self.send("分析这段视频")
        self.assertEqual(len(self.calls), 2)
        self.assertIn("[文件名: main.c]", result["assistant_message"]["content"])
        self.assertTrue(result["video_context"]["detail_selection"]["frame_indices"])
        self.assertIn("同一行的标识、数值、单位和标志逐列配对", self.calls[1]["messages"][0]["content"])

    def test_user_pointing_to_visible_weights_resends_images_in_existing_topic(self):
        first = self.send("分析这段视频")
        self.assertFalse(first["video_context"]["detail_selection"]["frame_indices"])
        self.long_reply = True
        for message in ("下面不属于这道题目，已经是完整题目了", "权重值在视频中表格已经显示了",
                        "表格里显示比如A-B 4这类的，你是看不清楚内容吗"):
            with self.subTest(message=message):
                before = len(self.calls)
                result = self.send(message, first["conversation_id"])
                self.assertEqual(len(self.calls), before + 1)
                self.assertTrue(any(part["image_url"]["detail"] == "high" for part in self.pictures()))
                self.assertIn(message, self.calls[-1]["messages"][-1]["content"][-1]["text"])
                self.assertEqual(result["video_context"]["opening_message"], "分析这段视频")

    def test_scrolling_task_asks_for_screenshot_only_after_reviewing_both_sections(self):
        rule, output = self.video.frames[1:3]
        self.overview_reply = ("已经定位题目要求与输出格式，但关键的排序规则与禁止函数看不全。"
                               "请补充能完整显示「题目要求—输出格式」区的高清截图。")
        self.overview_observations = [
            {"frame_index": rule.index, "keywords": ["题目要求"], "description": "滚动到具体要求。", "uncertain": False},
            {"frame_index": output.index, "keywords": ["输出格式"], "description": "滚动到输出示例和限制条件。", "uncertain": False},
        ]
        self.long_reply = True
        result = self.send("分析这段视频")
        self.assertEqual(len(self.calls), 2)
        selection = result["video_context"]["detail_selection"]
        start, end = selection["focus_range_ms"]
        self.assertLessEqual(start, round(rule.time_seconds * 1000))
        self.assertGreaterEqual(end, round(output.time_seconds * 1000))
        self.assertLessEqual(end - start, 5000)
        self.assertTrue(1 <= len(selection["focus_frames"]) <= 4)
        self.assertIn("跨帧拼读", self.calls[-1]["messages"][0]["content"])
        self.assertIn("排除该词", self.calls[-1]["messages"][0]["content"])
        self.assertIn("[文件名: main.c]", result["assistant_message"]["content"])

    def test_forged_model_selection_is_overwritten_by_actual_sent_frames(self):
        self.forge_selection = True
        actual = self.videos._observe(self.video, "重新读题")
        indices = actual.detail_selection.frame_indices
        self.assertTrue(indices)
        self.assertNotIn(60, indices)
        self.assertTrue(set(indices) <= {frame.index for frame in self.video.frames})
        with self.assertRaises(ValueError):
            DetailSelection(profile="text", frame_indices=[0, 0])

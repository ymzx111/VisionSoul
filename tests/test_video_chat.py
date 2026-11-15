"""视频配文、持久话题记忆、一次调用与旧库迁移验证；模型使用模拟传输。"""
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.api.routes.chat import get_chat_service
from app.core.config import Settings, get_settings
from app.main import app
from app.services.audio_evidence import AudioTranscript, SpeechSegment
from app.services.chat import ChatService
from app.services.chat_store import ChatStore
from app.services.deepseek import DeepSeekClient, DeepSeekError, ModelReply
from app.services.images import save_image
from app.services.videos import VideoService
from test_video import make_video, result_payload


def pictures(payload):
    return [part for message in payload["messages"] if isinstance(message["content"], list)
            for part in message["content"] if part["type"] == "image_url"]


class VideoChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory(prefix="visionsoul-video-chat-fixture-")
        cls.addClassCleanup(cls.fixture.cleanup)
        path = Path(cls.fixture.name) / "clip.mp4"
        make_video(path)
        cls.data = path.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-video-chat-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, deepseek_api_key="test-key-not-valid",
            chat_database_path=root / "chat.sqlite3", video_storage_dir=root / "videos", role_database_path=root / "roles.sqlite3")
        self.calls = []
        self.status = 200
        self.payload = result_payload()
        self.text_answer = "记得，你说去了西湖。"

        def handler(request):
            payload = json.loads(request.content)
            self.calls.append(payload)
            answer = json.dumps(self.payload, ensure_ascii=False) if payload.get("response_format") else self.text_answer
            return httpx.Response(self.status, json={"model": "deepseek-flash", "choices": [
                {"message": {"content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

        self.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        self.videos = VideoService(self.settings, self.client)
        self.video = self.videos.upload(BytesIO(self.data))
        self.chat = ChatService(self.settings, self.client, self.videos)

    def send(self, message="嘿，我今天去了西湖，你看看这段视频。", request="1", conversation=None, video=True):
        return self.chat.send(conversation_id=conversation, request_id=request * 32, message=message,
                              image_id=None, video_id=self.video.video_id if video else None)

    def test_new_video_caption_one_call_then_text_followup_and_restore(self):
        first = self.send()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(pictures(self.calls[0])), 2)
        self.assertIn("西湖", self.calls[0]["messages"][-1]["content"][-1]["text"])
        self.assertEqual(first["video_id"], self.video.video_id)
        self.assertEqual(first["user_message"]["video_id"], self.video.video_id)
        second = self.send("我刚才说去哪儿了？", "2", first["conversation_id"], video=False)
        self.assertEqual(second["conversation_id"], first["conversation_id"])
        self.assertFalse(pictures(self.calls[1]))
        self.assertIn("西湖", json.dumps(self.calls[1]["messages"], ensure_ascii=False))
        self.assertNotIn("response_format", self.calls[1])
        fresh = ChatStore(self.settings.chat_database_path)
        fresh.initialize()
        self.assertEqual(len(fresh.messages(first["conversation_id"])), 4)
        self.assertIn("西湖", json.loads(fresh.get(first["conversation_id"])["video_context"])["opening_message"])
        replay = self.send(request="1")
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.calls), 2)

    def test_blank_video_request_asks_for_goal_and_saves_turn(self):
        self.payload["reply"] = "画面从红色切换到蓝色。你想让我分析这段视频的哪部分？"
        self.videos.analyze(self.video.video_id)
        self.calls.clear()
        first = self.send("  ")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(pictures(self.calls[0]))
        prompt = self.calls[0]["messages"][0]["content"]
        self.assertIn("只问一个", prompt)
        self.assertEqual(first["user_message"]["content"], "分析这段视频")
        self.assertEqual(first["assistant_message"]["content"], self.payload["reply"])
        self.assertTrue(self.send("")["replayed"])
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(DeepSeekError) as error:
            self.send("", "2", first["conversation_id"])
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(len(self.calls), 1)
        followup = self.send("请说颜色变化。", "2", first["conversation_id"])
        self.assertEqual(followup["conversation_id"], first["conversation_id"])
        self.assertFalse(pictures(self.calls[1]))

    def test_blank_video_request_uses_transcript_for_task_and_reading_in_one_call(self):
        audio = AudioTranscript(status="ready", segments=[
            SpeechSegment(id=0, start=0.3, end=2, text="把黄色的这一道题用 C 远离实现。"),
        ])
        (self.settings.video_storage_dir / self.video.video_id / "audio.json").write_text(
            audio.model_dump_json(), encoding="utf-8")
        self.payload.update(reply="你想完成黄色题目。这里是代码。", audio_links=[
            {"segment_id": 0, "relation": "audio_only", "detail": "对白提到黄色题目。"},
        ])
        first = self.send("")
        self.assertEqual(len(self.calls), 1)
        prompt = self.calls[0]["messages"][0]["content"]
        self.assertIn("直接作答", prompt)
        self.assertIn("本轮是题目/屏幕文字阅读", prompt)
        parts = self.calls[0]["messages"][-1]["content"]
        self.assertTrue(any("C 远离" in part.get("text", "") for part in parts))
        self.assertGreater(sum(part["image_url"]["detail"] == "high" for part in pictures(self.calls[0])), 0)
        self.assertEqual(first["assistant_message"]["content"], self.payload["reply"])

        self.videos.isolated = True
        self.send("请只概括画面。", "2")
        prompt = self.calls[1]["messages"][0]["content"]
        self.assertNotIn("本轮用户上传视频并点击开始分析", prompt)
        self.assertNotIn("本轮是题目/屏幕文字阅读", prompt)
        self.assertEqual(self.calls[1]["messages"][-1]["content"][-1]["text"], "请只概括画面。")

    def test_new_topic_reuses_visual_facts_but_not_previous_caption_or_reply(self):
        first = self.send("这是只属于第一个话题的地名秘密。")
        second = self.send("这个视频什么颜色？", "2")
        self.assertNotEqual(first["conversation_id"], second["conversation_id"])
        self.assertFalse(pictures(self.calls[-1]))
        sent = json.dumps(self.calls[-1], ensure_ascii=False)
        self.assertNotIn("地名秘密", sent)
        self.assertNotIn(first["assistant_message"]["content"], sent)

    def test_existing_standalone_analysis_is_reused_without_resending_images(self):
        self.videos.analyze(self.video.video_id)
        self.calls.clear()
        self.send()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(pictures(self.calls[0]))

    def test_same_work_video_keeps_format_question_instead_of_using_old_source(self):
        fence = chr(96) * 3
        code = (
            "import os\n"
            "with open(os.getenv('VISIONSOUL_DATA_FILE', 'inventory.txt')) as data:\n"
            "    records = [line.split() for line in data]\n"
            "with open('restock_order.txt', 'w') as output:\n"
            "    output.write(str(records))\n"
        )
        self.payload["reply"] = f"[文件名: main.py]\n{fence}python\n{code}{fence}"
        def send(request, video_id=None, space="work"):
            return self.chat.send(conversation_id=None, request_id=request * 32, message="",
                                  image_id=None, video_id=video_id or self.video.video_id, space=space)
        send("1")
        question = "读取inventory.txt并写出restock_order.txt及屏幕统计。请先确认库存记录是否空格分隔。"
        self.payload["reply"] = question
        self.assertEqual(send("2")["assistant_message"]["content"], question)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn("output.write", json.dumps(self.calls[-1]["messages"], ensure_ascii=False))

        other_path = Path(self.temp.name) / "other.mp4"
        other_path.write_bytes(self.data + b"\x00")
        with other_path.open("rb") as file:
            other = self.videos.upload(file)
        self.assertNotEqual(other.video_id, self.video.video_id)
        self.assertEqual(send("3", other.video_id)["assistant_message"]["content"], question)
        self.assertEqual(send("4", space="companion")["assistant_message"]["content"], question)

    def test_ambiguous_video_pauses_delivery_until_user_confirms_counting_rule(self):
        self.settings.local_source_workspace = Path(self.temp.name) / "workspace"
        question = "[需确认题意]\n“豆浆好喝，豆浆很香”应输出没有重复片段，还是豆浆出现两次？"
        fence = chr(96) * 3
        source = "print(input().count('豆浆'))\n"
        # A stray code block while asking for clarification must not launch or start a retry.
        self.payload["reply"] = f"{question}\n[文件名: main.py]\n{fence}python\n{source}{fence}"
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        self.addCleanup(app.dependency_overrides.clear)

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        body = {"space": "work", "video_id": self.video.video_id, "open_in_ide": True}
        with (TestClient(local_app, base_url=base, headers={"Origin": base}) as client,
              patch("app.services.local_ide.launch_ide") as launch,
              patch("app.services.local_ide.show_sandbox_result", return_value=True)):
            initial = client.post("/api/chat/messages", json={**body, "request_id": "a" * 32, "message": ""})
            self.assertEqual(initial.status_code, 200, initial.text)
            waiting = initial.json()
            self.assertIsNone(waiting["local_source"])
            self.assertEqual(len(self.calls), 1)
            self.assertFalse(self.settings.local_source_workspace.exists())
            launch.assert_not_called()
            self.assertIn("正确理解题目优先", self.calls[0]["messages"][0]["content"])
            samples = [{"name": "区分片段和关键词", "stdin": "豆浆好喝，豆浆很香", "expected": "2"}]
            answer = (f"[文件名: main.py]\n{fence}python\n{source}{fence}\n"
                      f"{fence}visionsoul-cases\n{json.dumps(samples)}\n{fence}")
            with patch.object(self.client, "complete", return_value=ModelReply(answer=answer, model="test")) as complete:
                response = client.post("/api/chat/messages", json={
                    **body, "request_id": "b" * 32, "conversation_id": waiting["conversation_id"],
                    "message": "统计豆浆这个词出现了几次。",
                })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("统计豆浆", json.dumps(complete.call_args.args[0], ensure_ascii=False))
            delivered = response.json()["local_source"]
            self.assertEqual(Path(delivered["path"]).read_text(encoding="utf8"), source)
            self.assertEqual(delivered["python_check"]["case_results"][0]["status"], "passed")
            self.assertEqual(delivered["python_check"]["status"], "awaiting_input")

    def test_opening_share_survives_recent_window_and_latest_correction_is_included(self):
        first = self.send()
        for index in range(2, 9):
            self.send(f"第 {index} 轮问题", str(index), first["conversation_id"], video=False)
        self.send("更正一下，其实是东湖。", "9", first["conversation_id"], video=False)
        sent = json.dumps(self.calls[-1]["messages"], ensure_ascii=False)
        self.assertIn("西湖", sent)
        self.assertIn("东湖", sent)
        self.assertIn("最新用户更正优先", sent)
        self.assertFalse(pictures(self.calls[-1]))
        self.assertLessEqual(len(self.calls[-1]["messages"]), 15)

    def test_cannot_change_video_or_mix_image_in_same_topic(self):
        first = self.send()
        count = len(self.calls)
        for kwargs in [{"video_id": "f" * 64, "image_id": None}, {"video_id": None, "image_id": "a" * 32}]:
            with self.assertRaises(DeepSeekError) as error:
                self.chat.send(conversation_id=first["conversation_id"], request_id="2" * 32, message="换素材", **kwargs)
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.calls), count)

    def test_failure_does_not_save_half_turn_and_never_auto_retries(self):
        self.status = 503
        with self.assertRaises(DeepSeekError):
            self.send()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.chat.store.recent(), [])

    def test_api_restore_kind_filter_and_role_injection_rejection(self):
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            body = {"request_id": "1" * 32, "message": "去了西湖", "video_id": self.video.video_id}
            response = client.post("/api/chat/messages", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            conversation = response.json()["conversation_id"]
            restored = client.get(f"/api/chat/conversations/{conversation}").json()
            self.assertEqual(restored["video_id"], self.video.video_id)
            self.assertIsInstance(restored["video_context"], dict)
            self.assertEqual(len(client.get("/api/chat/conversations?kind=video").json()), 1)
            self.assertEqual(client.get("/api/chat/conversations").json(), [])
            self.assertEqual(client.post("/api/chat/messages", json={**body, "history": []}).status_code, 422)
            self.assertEqual(client.post("/api/chat/messages", json={**body, "image_id": "a" * 32}).status_code, 422)
            blank = {"request_id": "2" * 32, "message": " "}
            self.assertEqual(client.post("/api/chat/messages", json=blank).status_code, 422)
            self.assertEqual(client.post("/api/chat/messages", json={**blank, "video_id": self.video.video_id,
                "debug_code": True}).status_code, 422)
            self.assertEqual(client.post("/api/chat/messages", json={**blank, "video_id": self.video.video_id}).status_code, 200)

    def test_local_video_code_is_saved_and_opened_once_after_success(self):
        root = Path(self.temp.name)
        launcher = root / "pycharm64.exe"
        launcher.touch()
        self.settings.local_pycharm = launcher
        self.settings.python_sandbox_enabled = False
        self.settings.local_source_workspace = root / "workspace"
        fence = chr(96) * 3
        code = 'print("VisionSoul")\n'
        complete_reply = f"[语言: Python]\n[文件名: main.py]\n[需要数据文件]\n\n{fence}python\n{code}{fence}"
        self.payload["reply"] = "题目要求编写一个 Python 程序。以下是完整源码。"
        complete_request = self.client.complete

        def omit_first_source(*args, **kwargs):
            if self.calls:
                self.payload["reply"] = complete_reply
            return complete_request(*args, **kwargs)
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        self.addCleanup(app.dependency_overrides.clear)

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        body = {"request_id": "1" * 32, "message": "", "space": "work",
                "video_id": self.video.video_id, "open_in_ide": True}
        with TestClient(local_app, base_url="http://127.0.0.1:8765") as client, \
                patch("app.services.local_ide.launch_ide") as launch, \
                patch.object(self.client, "complete", side_effect=omit_first_source):
            origin = {"Origin": "http://127.0.0.1:8765"}
            result = client.post("/api/chat/messages", json=body, headers=origin)
            self.assertEqual(result.status_code, 200, result.text)
            response = result.json()
            source = response["local_source"]
            self.assertEqual(source["status"], "opened")
            self.assertEqual(response["usage"]["total_tokens"], 240)
            self.assertIn("编程任务需要实际交付", self.calls[1]["messages"][0]["content"])
            target = Path(source["path"])
            self.assertEqual(target.parent, root / "workspace" / "Python")
            self.assertEqual(target.name, f"{date.today():%Y.%m.%d}.main.py")
            self.assertEqual(target.read_text(encoding="utf8"), code)
            self.assertEqual(launch.call_args.args[0], [str(launcher), str(target)])
            prompt = self.calls[0]["messages"][0]["content"]
            self.assertIn("[文件名: main.c]", prompt)
            self.assertIn("按时间顺序检查本轮全部提供的画面", prompt)
            self.assertIn("能确定读入什么、怎样处理和应交付什么，就直接生成代码", prompt)
            self.assertIn("用户说稍后提供数据时，继续按可读的题面生成完整源码", prompt)
            self.assertIn("只有对照预期输出通过后，才把运行结果称为已验收", prompt)
            replay = client.post("/api/chat/messages", json=body, headers=origin).json()
            self.assertTrue(replay["replayed"])
            self.assertIsNone(replay["local_source"])
            self.assertEqual(len(self.calls), 2)
            foreign = client.post("/api/chat/messages", json={**body, "request_id": "2" * 32},
                                  headers={"Origin": "https://example.invalid"}).json()
            self.assertIsNone(foreign["local_source"])
            launch.assert_called_once()

    def test_video_visual_review_precedes_local_delivery_and_is_bounded(self):
        self.settings.local_source_workspace = Path(self.temp.name) / "workspace"
        fence = chr(96) * 3
        code = 'print(int(input()) * 7)\n'
        source_reply = (f"[文件名: main.py]\n{fence}python\n{code}{fence}\n"
                        f'{fence}visionsoul-cases\n[{{"name":"结果","stdin":"6","expected":"42"}}]\n{fence}')
        blurred_reply = "[需看清画面: 0.5–2.5 秒]\n第6项里会影响结果的统计范围看不清。"
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        self.addCleanup(app.dependency_overrides.clear)

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        body = {"space": "work", "video_id": self.video.video_id, "open_in_ide": True,
                "message": "按要求6编写程序"}
        complete = self.client.complete

        for request, answers, expected in (
            ("a", [source_reply], 1),
            ("b", [blurred_reply, source_reply], 2),
            ("c", [blurred_reply, blurred_reply + "\n" + source_reply], 2),
            ("d", [blurred_reply, {"reply": "已截断"}], 2),
        ):
            with self.subTest(request=request):
                self.calls.clear()
                model_answers = iter(answers)

                def model_reply(*args, **kwargs):
                    answer = next(model_answers)
                    self.payload = result_payload() if isinstance(answer, str) else answer
                    if isinstance(answer, str):
                        self.payload["reply"] = answer
                    return complete(*args, **kwargs)

                with (TestClient(local_app, base_url=base, headers={"Origin": base}) as client,
                      patch.object(self.client, "complete", side_effect=model_reply),
                      patch("app.services.local_ide.launch_ide") as launch,
                      patch("app.services.local_ide.show_sandbox_result", return_value=True)):
                    response = client.post("/api/chat/messages", json={**body, "request_id": request * 32})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(len(self.calls), expected)
                self.assertTrue(all(part["image_url"]["detail"] == "low" for part in pictures(self.calls[0])))
                if expected == 2:
                    high = [part for part in pictures(self.calls[1]) if part["image_url"]["detail"] == "high"]
                    self.assertTrue(1 <= len(high) <= 4)
                    self.assertIn("自动高清复核", self.calls[1]["messages"][0]["content"])
                    if request != "d":
                        selection = result["video_context"]["detail_selection"]
                        self.assertEqual(selection["focus_range_ms"], [500, 2500])
                        self.assertEqual(len(selection["focus_frames"]), len(high))
                        self.assertEqual(result["usage"]["total_tokens"], 240)
                if request in {"a", "b"}:
                    source = result["local_source"]
                    self.assertEqual(Path(source["path"]).read_text(encoding="utf-8"), code)
                    self.assertEqual(source["python_check"]["status"], "awaiting_input")
                    self.assertEqual(source["python_check"]["case_results"][0]["status"], "passed")
                    launch.assert_called_once()
                else:
                    self.assertIsNone(result["local_source"])
                    self.assertIn("统计范围看不清", result["assistant_message"]["content"])
                    self.assertNotIn("[需看清画面", result["assistant_message"]["content"])
                    self.assertIn("0.5–2.5 秒", result["assistant_message"]["content"])
                    self.assertIn("聊天框补充", result["assistant_message"]["content"])
                    launch.assert_not_called()

    def test_video_followup_uses_clear_photos_with_saved_context_and_no_frame_resend(self):
        self.settings.image_storage_dir = Path(self.temp.name) / "images"
        photos = []
        for color in ("red", "white"):
            buffer = BytesIO()
            Image.new("RGB", (24, 16), color).save(buffer, "PNG")
            photos.append(save_image(buffer.getvalue(), self.settings).image_id)
        first = self.chat.send(conversation_id=None, request_id="a" * 32, message="分析视频",
                               image_id=None, video_id=self.video.video_id, space="work")
        options = dict(conversation_id=first["conversation_id"], request_id="b" * 32,
                       message="截图是视频第 1 秒的表格，请重新读题", image_id=None, image_ids=photos, space="work")
        result = self.chat.send(**options)
        self.assertEqual(result["conversation_id"], first["conversation_id"])
        self.assertEqual(result["video_id"], self.video.video_id)
        self.assertEqual(result["user_message"]["image_ids"], photos)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([part["image_url"]["detail"] for part in pictures(self.calls[1])], ["high", "high"])
        self.assertIn("本话题保存的视频观察", json.dumps(self.calls[1], ensure_ascii=False))
        self.assertNotIn("response_format", self.calls[1])
        self.assertTrue(self.chat.send(**options)["replayed"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(ChatStore(self.settings.chat_database_path).messages(first["conversation_id"])[-2]["image_ids"], photos)
        initial = self.chat.send(**{**options, "conversation_id": None, "request_id": "c" * 32,
                                    "video_id": self.video.video_id, "message": "用截图核对视频中的表格"})
        self.assertEqual(initial["user_message"]["image_ids"], photos)
        self.assertEqual([part["image_url"]["detail"] for part in pictures(self.calls[-1])][-2:], ["high", "high"])
        self.assertGreater(len(pictures(self.calls[-1])), len(photos))
        self.assertIn("response_format", self.calls[-1])

    def test_work_video_rewrite_keeps_older_photo_task_and_code_after_restore(self):
        self.settings.image_storage_dir = Path(self.temp.name) / "images"
        self.settings.chat_context_turns = 1
        buffer = BytesIO()
        Image.new("RGB", (24, 16), "green").save(buffer, "PNG")
        photo = save_image(buffer.getvalue(), self.settings).image_id
        opening = "请分析视频，长句按原文超过二十个字符判断。"
        first = self.chat.send(conversation_id=None, request_id="a" * 32, message=opening,
                               image_id=None, video_id=self.video.video_id, space="work")
        topic = first["conversation_id"]
        caption = "补图是第八秒的统计规则，保留并列结果。"
        self.text_answer = '[文件名: main.c]\n```c\nint main(void) { return 17; }\n```'
        self.chat.send(conversation_id=topic, request_id="b" * 32, message=caption,
                       image_id=None, image_ids=[photo], space="work")
        self.text_answer = "这次仍按真实文件计算。"
        self.chat.send(conversation_id=topic, request_id="c" * 32, message="按授权文件输出。",
                       image_id=None, space="work")
        restored = ChatService(self.settings, self.client)
        options = dict(conversation_id=topic, request_id="d" * 32, message="代码有误，请重新编写 C 程序。",
                       image_id=None, space="work")
        result = restored.send(**options)
        payload = self.calls[-1]
        recovered = json.dumps(payload["messages"], ensure_ascii=False)
        self.assertNotIn("response_format", payload)
        self.assertEqual([part["image_url"]["detail"] for part in pictures(payload)], ["high"])
        for expected in (opening, caption, "按授权文件输出。", "return 17;", options["message"]):
            self.assertIn(expected, recovered)
        self.assertEqual(result["user_message"]["image_ids"], [])
        self.assertTrue(restored.send(**options)["replayed"])
        self.assertEqual(len(self.calls), 4)
        restored.send(conversation_id=None, request_id="e" * 32, message="新问题", image_id=None, space="work")
        self.assertFalse(pictures(self.calls[-1]))
        self.assertNotIn(caption, json.dumps(self.calls[-1], ensure_ascii=False))

    def test_video_missing_stdin_sample_retries_before_running_real_input(self):
        self.settings.local_source_workspace = Path(self.temp.name) / "workspace"
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        app.dependency_overrides[get_settings] = lambda: self.settings
        self.addCleanup(app.dependency_overrides.clear)
        fence = chr(96) * 3
        program = "print(input().count('明天'))\n"
        reply = f"[文件名: main.py]\n{fence}python\n{program}{fence}\n"
        self.payload["reply"] = reply
        original_complete = self.client.complete

        def provide_sample_on_retry(*args, **kwargs):
            if self.calls:
                sample = [{"name": "关键词", "stdin": "明天，明天。", "expected": "2"}]
                self.payload["reply"] = f"{reply}{fence}visionsoul-cases\n{json.dumps(sample)}\n{fence}"
            return original_complete(*args, **kwargs)

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        base = "http://127.0.0.1:8765"
        with (TestClient(local_app, base_url=base, headers={"Origin": base}) as client,
              patch.object(self.client, "complete", side_effect=provide_sample_on_retry),
              patch("app.services.local_ide.launch_ide"),
              patch("app.services.local_ide.show_sandbox_result")):
            response = client.post("/api/chat/messages", json={
                "request_id": "e" * 32, "message": "", "space": "work",
                "video_id": self.video.video_id, "open_in_ide": True,
            })
            self.assertEqual(response.status_code, 200, response.text)
            delivery = response.json()["local_source"]
            check = delivery["python_check"]
            self.assertEqual((check["status"], check["stdout"]), ("awaiting_input", ""))
            self.assertEqual(check["case_results"][0]["stdout"], "2\n")
            result = client.post("/api/chat/sandbox-runs", json={
                "run_id": delivery["sandbox_run_id"], "stdin": "明天、明天、明天。", "expected": "3",
                "open_result_window": False,
            }).json()
            self.assertEqual((result["status"], result["stdout"]), ("passed", "3\n"))
            self.assertEqual(len(self.calls), 2)

    def test_python_test_is_local_and_repaired_source_uses_new_file(self):
        from app.services.python_sandbox import PythonCheck

        root = Path(self.temp.name)
        launcher = root / "pycharm64.exe"
        launcher.touch()
        self.settings.local_pycharm = launcher
        self.settings.local_source_workspace = root / "workspace"
        fence = chr(96) * 3
        original, revised = "print(unknown)\n", "print(42)\n"
        self.payload["reply"] = f"[文件名: main.py]\n{fence}python\n{original}{fence}"
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        self.addCleanup(app.dependency_overrides.clear)

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        body = {"request_id": "1" * 32, "message": "", "space": "work",
                "video_id": self.video.video_id, "open_in_ide": True,
                "python_test": {"approved_file": {"filename": "sample.txt", "content": "LOCAL-CANARY"}}}
        with TestClient(local_app, base_url="http://127.0.0.1:8765") as client, \
                patch("app.services.local_ide.launch_ide") as launch, \
                patch("app.services.local_ide.show_sandbox_result", return_value=True), \
                patch("app.api.routes.chat.run_python_sandbox",
                      return_value=PythonCheck(status="passed", detail="通过验收")) as run, \
                patch("app.api.routes.chat.debug_python",
                      return_value=PythonCheck(status="ran", detail="运行成功", attempts=2, repaired_code=revised)) as verify:
            origin = {"Origin": "http://127.0.0.1:8765"}
            first = client.post("/api/chat/messages", json=body, headers=origin)
            response = client.post("/api/chat/messages", json={**body, "request_id": "2" * 32, "python_test": None},
                                   headers=origin)
            self.payload["reply"] = "[需要数据文件]\n" + self.payload["reply"]
            pending = client.post("/api/chat/messages", json={**body, "request_id": "3" * 32, "python_test": None},
                                  headers=origin)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["local_source"]["python_check"]["status"], "passed")
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()["local_source"]
        target = Path(source["path"])
        self.assertEqual(target.read_text(encoding="utf8"), revised)
        originals = [file for file in target.parent.glob("*.py") if file != target]
        self.assertEqual([file.read_text(encoding="utf8") for file in originals], [original] * 3)
        self.assertEqual(pending.status_code, 200, pending.text)
        check = pending.json()["local_source"]["python_check"]
        self.assertEqual((check["status"], check["attempts"]), ("awaiting_file", 0))
        self.assertEqual(check["phase"], "compile")
        self.assertIn("通过语法检查", check["detail"])
        self.assertIn("授权并试跑", check["detail"])
        run.assert_called_once()
        verify.assert_called_once()
        self.assertEqual(source["python_check"]["attempts"], 2)
        self.assertEqual(run.call_args.args[-1].approved_file.content, "LOCAL-CANARY")
        self.assertIsNone(verify.call_args.args[-1].approved_file)
        self.assertIn("sample.txt", self.calls[0]["messages"][0]["content"])
        self.assertNotIn("sample.txt", self.calls[1]["messages"][0]["content"])
        self.assertNotIn("LOCAL-CANARY", repr(self.calls))
        self.assertIn(([str(launcher), str(target)], target.parent),
                      [call.args for call in launch.call_args_list])

    def test_native_source_has_buildable_project_and_falls_back_on_launch_error(self):
        from app.services.local_ide import open_generated_source
        root = Path(self.temp.name)
        launcher = root / "devenv.exe"
        launcher.touch()
        self.settings.local_visual_studio = launcher
        self.settings.local_source_workspace = root / "workspace"
        fence = chr(96) * 3
        code = '#include <stdio.h>\nint main(void) { return 0; }\n'
        turn = {"conversation_id": "a" * 32, "assistant_message": {
            "id": 2, "content": f"[文件名: main.c]\n{fence}c\n{code}{fence}"}}
        with patch("app.services.local_ide.launch_ide", side_effect=OSError):
            with self.assertLogs("app.services.local_ide", level="WARNING"):
                source = open_generated_source(self.settings, turn)
        self.assertEqual(source["status"], "saved")
        target = Path(source["path"])
        self.assertEqual(target.read_text(encoding="utf-8-sig"), code)
        self.assertEqual(target.parent, root / "workspace" / "C")
        self.assertEqual(target.name, f"{date.today():%Y.%m.%d}.main.c")
        project_dir = root / "ide_projects" / ("a" * 32 + "-2")
        self.assertIn(f'ClCompile Include="{target}"', (project_dir / "VisionSoulCode.vcxproj").read_text(encoding="utf8"))
        self.assertTrue((project_dir / f"{Path(source['path']).name}.sln").is_file())

    def test_generated_sources_keep_dated_language_paths_and_duplicate_names(self):
        from app.services.source_files import save_dated_source
        workspace = Path(self.temp.name) / "workspace"
        workspace.mkdir()
        legacy = workspace / "old.c"
        legacy.write_text("old code", encoding="utf8")
        with patch("app.services.source_files.date") as calendar:
            calendar.today.return_value = date(2026, 11, 12)
            first = save_dated_source(workspace, "main.c", "first")
            second = save_dated_source(workspace, "main.c", "second")
            cpp = save_dated_source(workspace, "main.cpp", "cpp")
            python = save_dated_source(workspace, "main.py", "python")
        self.assertEqual([path.relative_to(workspace).as_posix() for path in (first, second, cpp, python)],
                         ["C/2026.11.12.main.c", "C/2026.11.12.main-2.c",
                          "C/2026.11.12.main.cpp", "Python/2026.11.12.main.py"])
        self.assertEqual([path.read_text(encoding="utf8") for path in (first, second, legacy)],
                         ["first", "second", "old code"])

    def test_migration_preserves_legacy_rows_and_is_idempotent(self):
        path = Path(self.temp.name) / "legacy.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,image_id TEXT,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,content TEXT NOT NULL,image_id TEXT,created_at TEXT NOT NULL);
                INSERT INTO conversations VALUES('old','原话题',NULL,'now','now',1);
                INSERT INTO messages(conversation_id,role,content,created_at) VALUES('old','user','原消息','now');
            """)
        conn.close()
        store = ChatStore(path)
        store.initialize()
        store.initialize()
        self.assertEqual(store.messages("old")[0]["content"], "原消息")
        self.assertIsNone(store.get("old")["video_id"])
        self.assertEqual(len(store.recent("image")), 1)
        self.assertEqual(store.recent("video"), [])


if __name__ == "__main__":
    unittest.main()

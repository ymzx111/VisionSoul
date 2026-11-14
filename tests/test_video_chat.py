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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.api.routes.chat import get_chat_service
from app.core.config import Settings, get_settings
from app.main import app
from app.services.audio_evidence import AudioTranscript, SpeechSegment
from app.services.chat import ChatService
from app.services.chat_store import ChatStore
from app.services.deepseek import DeepSeekClient, DeepSeekError
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

        def handler(request):
            payload = json.loads(request.content)
            self.calls.append(payload)
            answer = json.dumps(self.payload, ensure_ascii=False) if payload.get("response_format") else "记得，你说去了西湖。"
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
        self.payload["reply"] = f"[语言: Python]\n[文件名: main.py]\n\n{fence}python\n{code}{fence}"
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        self.addCleanup(app.dependency_overrides.clear)

        async def local_app(scope, receive, send):
            await app({**scope, "client": ("127.0.0.1", 1234)}, receive, send)

        body = {"request_id": "1" * 32, "message": "", "space": "work",
                "video_id": self.video.video_id, "open_in_ide": True}
        with TestClient(local_app, base_url="http://127.0.0.1:8765") as client, \
                patch("app.services.local_ide.launch_ide") as launch:
            origin = {"Origin": "http://127.0.0.1:8765"}
            result = client.post("/api/chat/messages", json=body, headers=origin)
            self.assertEqual(result.status_code, 200, result.text)
            response = result.json()
            source = response["local_source"]
            self.assertEqual(source["status"], "opened")
            target = Path(source["path"])
            self.assertEqual(target.parent, root / "workspace" / "Python")
            self.assertEqual(target.name, f"{date.today():%Y.%m.%d}.main.py")
            self.assertEqual(target.read_text(encoding="utf8"), code)
            self.assertEqual(launch.call_args.args[0], [str(launcher), str(target)])
            self.assertIn("[文件名: main.c]", self.calls[0]["messages"][0]["content"])
            replay = client.post("/api/chat/messages", json=body, headers=origin).json()
            self.assertTrue(replay["replayed"])
            self.assertIsNone(replay["local_source"])
            self.assertEqual(len(self.calls), 1)
            foreign = client.post("/api/chat/messages", json={**body, "request_id": "2" * 32},
                                  headers={"Origin": "https://example.invalid"}).json()
            self.assertIsNone(foreign["local_source"])
            launch.assert_called_once()

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
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["local_source"]["python_check"]["status"], "passed")
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()["local_source"]
        target = Path(source["path"])
        self.assertEqual(target.read_text(encoding="utf8"), revised)
        originals = [file for file in target.parent.glob("*.py") if file != target]
        self.assertEqual([file.read_text(encoding="utf8") for file in originals], [original, original])
        self.assertEqual(source["python_check"]["attempts"], 2)
        self.assertEqual(run.call_args.args[-1].approved_file.content, "LOCAL-CANARY")
        self.assertIsNone(verify.call_args.args[-1].approved_file)
        self.assertIn("sample.txt", self.calls[0]["messages"][0]["content"])
        self.assertNotIn("sample.txt", self.calls[1]["messages"][0]["content"])
        self.assertNotIn("LOCAL-CANARY", repr(self.calls))
        self.assertEqual(launch.call_args.args[0], [str(launcher), str(target)])

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

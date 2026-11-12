"""用隔离数据库和模拟模型验证空间边界，不使用用户资料或外部 API。"""
from io import BytesIO
from contextlib import closing
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
from app.api.routes.roles import service as get_roles
from app.core.chat_space import WORK_PROMPT
from app.core.config import Settings, get_settings
from app.main import app
from app.services.chat import ChatService
from app.services.chat_store import ChatStore, ChatConflict
from app.services.deepseek import DeepSeekClient, DeepSeekError, ModelReply
from app.services.images import save_image
from app.services.role_store import RoleConflict, RoleStore
from app.services.roles import RoleService
from app.services.videos import VideoService
from test_video_chat import make_video, result_payload


class ChatSpaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-space-test-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.settings = Settings(_env_file=None, deepseek_api_key="test-only-invalid",
            chat_database_path=root / "chat.sqlite3", role_database_path=root / "roles.sqlite3",
            image_storage_dir=root / "images", video_storage_dir=root / "videos", audio_enabled=False)
        self.calls = []
        def handler(request):
            body = json.loads(request.content)
            self.calls.append(body)
            answer = json.dumps(result_payload(), ensure_ascii=False) if body.get("response_format") else "测试回答"
            return httpx.Response(200, json={"model": "deepseek-flash", "choices": [
                {"message": {"content": answer}, "finish_reason": "stop"}]})
        self.client = DeepSeekClient(self.settings, httpx.MockTransport(handler))
        self.chat = ChatService(self.settings, self.client)
        self.roles = RoleService(self.settings, self.client)
        self.counter = 0

    def send(self, space="work", message="请分析", conversation=None, request=None, **kwargs):
        self.counter += 1
        return self.chat.send(space=space, conversation_id=conversation, request_id=request or f"{self.counter:032x}",
                              message=message, image_id=kwargs.pop("image_id", None), **kwargs)

    def serialized(self):
        return json.dumps(self.calls[-1]["messages"], ensure_ascii=False)

    def api(self):
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.chat
        app.dependency_overrides[get_roles] = lambda: self.roles
        self.addCleanup(app.dependency_overrides.clear)
        return TestClient(app)

    def test_work_never_reads_role_store_and_keeps_own_image_context(self):
        before = self.settings.roles_path.read_bytes()
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "green").save(buffer, "PNG")
        image = save_image(buffer.getvalue(), self.settings).image_id
        with patch("app.services.chat.RoleStore", side_effect=AssertionError("work read role")), \
             patch("app.services.videos.load_persona", side_effect=AssertionError("work read persona")):
            first = self.send(message="工作专用上下文", image_id=image)
            self.send(message="继续解释", conversation=first["conversation_id"])
        self.assertIn("工作专用上下文", self.serialized())
        self.assertTrue(self.calls[-1]["messages"][0]["content"].startswith(WORK_PROMPT + "\n"))
        self.assertNotIn("小灵", self.serialized())
        self.assertEqual(self.settings.roles_path.read_bytes(), before)
        self.assertEqual(first["space"], "work")
        self.assertEqual(first["persona_id"], "work-v1")

    def test_companion_uses_confirmed_memory_work_does_not(self):
        self.roles.store.add_memory("preference", "角色记忆暗号紫色星星")
        self.send("companion")
        self.assertIn("角色记忆暗号紫色星星", self.serialized())
        self.assertIn("活泼俏皮", self.serialized())
        self.send("work")
        self.assertNotIn("角色记忆暗号紫色星星", self.serialized())
        self.assertNotIn("活泼俏皮", self.serialized())

    def test_cross_space_followup_and_replay_rejected_before_model(self):
        first = self.send(request="a" * 32)
        for kwargs in [{"conversation": first["conversation_id"]}, {"request": "a" * 32}]:
            with self.assertRaises(DeepSeekError) as error:
                self.send("companion", **kwargs)
            self.assertEqual(error.exception.status_code, 409)
        replay = self.send(request="a" * 32)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.calls), 1)

    def test_work_is_not_memory_source_and_cannot_extract(self):
        work = self.send(message="工作视频里有人说喜欢蓝色")
        with self.assertRaisesRegex(RoleConflict, "只能整理角色对话"):
            self.roles.extract(work["conversation_id"], "f" * 32)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.roles.store.memories(), [])
        with self.api() as api:
            self.assertEqual(api.get("/api/roles/xiaoling/sources").json(), [])
            rejected = api.post("/api/roles/xiaoling/extract", json={"conversation_id": work["conversation_id"], "request_id": "d" * 32})
            self.assertEqual(rejected.status_code, 409)

    def test_source_bound_memories_only_companion_sources_are_retrieved(self):
        work = self.send()
        companion = self.send("companion")
        self.roles.store.add_memory("fact", "工作来源不可引用暗号", conversation_id=work["conversation_id"])
        self.roles.store.add_memory("fact", "角色来源可引用暗号", conversation_id=companion["conversation_id"])
        self.roles.store.add_memory("fact", "旧来源待分类暗号", conversation_id="0" * 32)
        self.send("companion", message="记得暗号吗")
        self.assertIn("角色来源可引用暗号", self.serialized())
        self.assertNotIn("工作来源不可引用暗号", self.serialized())
        self.assertNotIn("旧来源待分类暗号", self.serialized())
        self.assertEqual(len(self.roles.store.memories()), 3)

    def test_api_status_lists_detail_and_default_are_scoped(self):
        work = self.send()
        companion = self.send("companion")
        with self.api() as api:
            with patch("app.api.routes.chat.RoleStore", side_effect=AssertionError("work status read role")):
                self.assertEqual(api.get("/api/chat/status?space=work").json()["name"], "解析助手")
            for space, result, other in [("work", work, companion), ("companion", companion, work)]:
                rows = api.get(f"/api/chat/conversations?kind=all&space={space}").json()
                self.assertEqual([row["id"] for row in rows], [result["conversation_id"]])
                self.assertEqual(api.get(f"/api/chat/conversations/{result['conversation_id']}?space={space}").status_code, 200)
                self.assertEqual(api.get(f"/api/chat/conversations/{other['conversation_id']}?space={space}").status_code, 409)
            self.assertEqual(api.get("/api/chat/conversations?kind=all").json()[0]["id"], companion["conversation_id"])
            self.assertEqual(api.get("/api/chat/conversations?space=invalid").status_code, 422)

    def test_legacy_migration_preserves_rows_until_explicit_classification(self):
        path = self.settings.chat_database_path
        cid = "e" * 32
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript("""CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,image_id TEXT,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE messages(id INTEGER PRIMARY KEY AUTOINCREMENT,conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,content TEXT NOT NULL,image_id TEXT,created_at TEXT NOT NULL);""")
            conn.execute("INSERT INTO conversations VALUES(?,?,NULL,'then','then',1)", (cid, "旧话题"))
            conn.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(?,'user','保留原话','then')", (cid,))
        self.chat.store.initialize()
        self.chat.store.initialize()
        self.roles.store.add_memory("fact", "旧来源暗号", conversation_id=cid)
        with self.api() as api:
            self.assertEqual(api.get("/api/chat/conversations?kind=all&space=work").json(), [])
            self.assertEqual(api.get("/api/chat/conversations?kind=all&space=legacy").json()[0]["id"], cid)
            self.assertEqual(api.get(f"/api/chat/conversations/{cid}?space=work").status_code, 409)
            classified = api.post(f"/api/chat/conversations/{cid}/classify", json={"space": "work"})
            self.assertEqual(classified.status_code, 200)
            restored = api.get(f"/api/chat/conversations/{cid}?space=work").json()
            self.assertEqual(restored["messages"][0]["content"], "保留原话")
            self.assertFalse(restored["has_older"])
            self.assertEqual(api.post(f"/api/chat/conversations/{cid}/classify", json={"space": "companion"}).status_code, 409)
        self.send("companion")
        self.assertNotIn("旧来源暗号", self.serialized())
        self.assertEqual(len(self.roles.store.memories()), 1)

    def test_memory_cards_explain_disabled_legacy_and_work_sources(self):
        work = self.send()
        companion = self.send("companion")
        self.roles.store.add_memory("fact", "工作来源卡", conversation_id=work["conversation_id"])
        self.roles.store.add_memory("fact", "角色来源卡", conversation_id=companion["conversation_id"])
        self.roles.store.add_memory("fact", "旧来源卡", conversation_id="e" * 32)
        self.roles.store.add_memory("fact", "手动角色卡")
        with self.api() as api:
            enabled = {row["content"]: row["source_enabled"] for row in api.get("/api/roles/xiaoling/memories").json()}
        self.assertEqual(enabled, {"工作来源卡": False, "角色来源卡": True, "旧来源卡": False, "手动角色卡": True})

    def test_legacy_cannot_be_continued_or_extracted_before_classification(self):
        first = self.send()
        with self.chat.store.connection() as conn:
            conn.execute("UPDATE conversations SET space='legacy' WHERE id=?", (first["conversation_id"],))
        with self.assertRaises(DeepSeekError):
            self.send(conversation=first["conversation_id"])
        with self.assertRaises(RoleConflict):
            self.roles.extract(first["conversation_id"], "f" * 32)
        self.assertEqual(len(self.calls), 1)

    def video(self):
        path = Path(self.temp.name) / "clip.mp4"
        make_video(path)
        return self.chat.videos.upload(BytesIO(path.read_bytes())).video_id

    def test_video_prompts_reports_and_followups_do_not_cross_spaces(self):
        vid = self.video()
        self.roles.store.add_memory("preference", "角色专用暗号青苹果")
        with patch("app.services.chat.RoleStore", side_effect=AssertionError("work video read role")):
            work = self.send(video_id=vid, message="工作专用配文")
        self.assertTrue(self.calls[-1]["messages"][0]["content"].startswith(WORK_PROMPT))
        self.assertNotIn("角色专用暗号青苹果", self.serialized())
        self.assertTrue(work["video_context"]["observations"])
        companion = self.send("companion", video_id=vid, message="角色专用配文")
        self.assertIn("角色专用暗号青苹果", self.serialized())
        self.assertNotIn("工作专用配文", self.serialized())
        self.assertEqual(len(self.calls), 2)  # 同素材新空间不能复用旧综合缓存。
        self.send(conversation=work["conversation_id"], message="刚才看到了什么")
        self.assertNotIn("角色专用配文", self.serialized())
        self.assertNotIn("角色专用暗号青苹果", self.serialized())
        self.assertFalse(any(isinstance(item["content"], list) for item in self.calls[-1]["messages"]))
        self.assertEqual(companion["space"], "companion")

    def test_explicit_inspect_and_debug_use_current_space_policy(self):
        vid = self.video()
        work = self.send(video_id=vid)
        self.roles.store.add_memory("preference", "角色检查暗号")
        captured = []
        def inspect(service, video_id, message, history, memory_text, **kwargs):
            captured.append((service.prompt, memory_text, kwargs))
            return work["video_context"], ModelReply(answer="检查回答", model="deepseek-flash")
        with patch.object(VideoService, "inspect", inspect):
            self.send(conversation=work["conversation_id"], inspect_video=True)
            self.send(conversation=work["conversation_id"], debug_code=True)
            self.send("companion", video_id=vid, inspect_video=True)
        self.assertTrue(all(WORK_PROMPT in row[0] and not row[1] for row in captured[:2]))
        self.assertIn("角色检查暗号", captured[2][1])

    def test_preview_metadata_does_not_return_legacy_persona_result(self):
        vid = self.video()
        old = VideoService(self.settings, self.client)
        old.analyze(vid)
        self.assertIsNotNone(old.info(vid).result)
        with self.api() as api:
            data = api.get(f"/api/videos/{vid}").json()
            self.assertIsNone(data["result"])
        first = self.send(video_id=vid)
        self.assertEqual(first["space"], "work")
        self.assertEqual(len(self.calls), 2)

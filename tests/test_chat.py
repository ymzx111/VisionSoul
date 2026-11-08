"""隔离 SQLite 与模拟 API，检查上下文、人格、原子保存和重复请求。"""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.api.routes.chat import get_chat_service
from app.core.config import Settings, get_settings
from app.core.persona import PERSONA_PROMPT
from app.main import app
from app.services.chat import ChatService, context_messages
from app.services.deepseek import DeepSeekClient, DeepSeekError, ModelReply
from app.services.images import save_image


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="visionsoul-chat-test-")
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(_env_file=None, deepseek_api_key="test-key-not-valid",
            image_storage_dir=Path(self.temp.name) / "images", chat_database_path=Path(self.temp.name) / "chat.sqlite3")
        self.payloads = []

        def handler(request):
            payload = json.loads(request.content)
            self.payloads.append(payload)
            return httpx.Response(200, json={"model": "deepseek-flash", "choices": [{"message": {"content": f"小灵回复{len(self.payloads)}"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}})
        self.service = ChatService(self.settings, DeepSeekClient(self.settings, httpx.MockTransport(handler)))

    def send(self, text="你好", request="1", conversation=None, image=None):
        return self.service.send(conversation_id=conversation, request_id=request * 32, message=text, image_id=image)

    def image(self, color="green"):
        buffer = BytesIO()
        Image.new("RGB", (24, 16), color).save(buffer, "PNG")
        return save_image(buffer.getvalue(), self.settings).image_id

    def test_two_turn_context_fixed_persona_and_persistence(self):
        first = self.send("我叫小夏")
        second = self.send("我叫什么？", "2", first["conversation_id"])
        messages = self.payloads[1]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": PERSONA_PROMPT})
        self.assertIn("活泼俏皮", messages[0]["content"])
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1]["content"], "我叫小夏")
        self.assertEqual(messages[2]["content"], first["assistant_message"]["content"])
        fresh_service = ChatService(self.settings)
        fresh_service.store.initialize()
        stored = fresh_service.store.messages(first["conversation_id"])
        self.assertEqual(len(stored), 4)
        self.assertEqual(stored[-1]["content"], second["assistant_message"]["content"])
        self.assertEqual(self.payloads[1]["thinking"], {"type": "disabled"})

    def test_picture_is_single_and_carried_forward_without_duplicate_attachments(self):
        picture = self.image()
        first = self.send("看看这张图", image=picture)
        self.send("里面什么颜色？", "2", first["conversation_id"])
        for payload in self.payloads:
            pictures = [part for message in payload["messages"] if isinstance(message["content"], list)
                        for part in message["content"] if part["type"] == "image_url"]
            self.assertEqual(len(pictures), 1)
            self.assertTrue(pictures[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(sum(bool(item["image_id"]) for item in self.service.store.messages(first["conversation_id"])), 1)
        with self.assertRaises(DeepSeekError) as error:
            self.send("换成这张", "3", first["conversation_id"], self.image("blue"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.payloads), 2)

    def test_text_topic_can_attach_first_picture_later(self):
        first = self.send("今天刚学会画图")
        second = self.send("这是刚画的", "2", first["conversation_id"], self.image())
        self.assertTrue(second["image_id"])
        self.assertEqual(self.payloads[1]["messages"][1]["content"], "今天刚学会画图")

    def test_completed_retry_is_cached_and_reuse_for_other_content_is_rejected(self):
        first = self.send("你好")
        replay = self.send("你好")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["assistant_message"], first["assistant_message"])
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(len(self.service.store.messages(first["conversation_id"])), 2)
        with self.assertRaises(DeepSeekError) as error:
            self.send("另一条内容")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.payloads), 1)

    def test_new_topics_are_isolated(self):
        first = self.send("第一段的私密上下文")
        second = self.send("第二段你好", "2")
        self.assertNotEqual(first["conversation_id"], second["conversation_id"])
        self.assertEqual(len(self.payloads[1]["messages"]), 2)
        self.assertNotIn("第一段", json.dumps(self.payloads[1], ensure_ascii=False))

    def test_failed_call_does_not_save_a_partial_turn(self):
        first = self.send("第一轮")
        failing = ChatService(self.settings, DeepSeekClient(self.settings, httpx.MockTransport(lambda request: httpx.Response(503))))
        with self.assertRaises(DeepSeekError):
            failing.send(conversation_id=first["conversation_id"], request_id="2" * 32, message="失败轮次", image_id=None)
        self.assertEqual(len(self.service.store.messages(first["conversation_id"])), 2)
        self.assertIsNone(self.service.store.cached("2" * 32))
        with self.assertRaises(DeepSeekError):
            failing.send(conversation_id=None, request_id="3" * 32, message="失败的新话题", image_id=None)
        self.assertEqual(len(self.service.store.recent()), 1)

    def test_unknown_image_or_topic_never_calls_provider(self):
        for conversation, image in [("0" * 32, None), (None, "0" * 32)]:
            with self.subTest(conversation=conversation, image=image):
                with self.assertRaises(DeepSeekError) as error:
                    self.send(conversation=conversation, image=image)
                self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(len(self.payloads), 0)

    def test_concurrent_requests_for_same_topic_do_not_make_duplicate_calls(self):
        first = self.send("开始")
        entered, release = threading.Event(), threading.Event()

        class SlowClient:
            calls = 0
            def complete(inner, messages):
                inner.calls += 1; entered.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release request")
                return ModelReply(answer="慢一点也能聊好。", model="deepseek-flash")

        client = SlowClient()
        service = ChatService(self.settings, client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(service.send, conversation_id=first["conversation_id"], request_id="2" * 32, message="等待中", image_id=None)
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(DeepSeekError) as error:
                    service.send(conversation_id=first["conversation_id"], request_id="3" * 32, message="重复点击", image_id=None)
                self.assertEqual(error.exception.status_code, 409)
                self.assertEqual(client.calls, 1)
            finally:
                release.set()
            future.result(timeout=3)
        self.assertEqual(len(service.store.messages(first["conversation_id"])), 4)

    def test_context_budget_keeps_complete_recent_pairs(self):
        history = []
        for number in range(10):
            history.extend([{"role": "user", "content": f"问题{number}"}, {"role": "assistant", "content": f"回复{number}"}])
        result = context_messages(history, self.settings, "新消息")
        self.assertEqual(len(result), 13)
        self.assertEqual(result[1]["content"], "问题4")
        tight = self.settings.model_copy(update={"chat_context_chars": len(PERSONA_PROMPT) + 3 + 9})
        result = context_messages(history, tight, "新消息")
        self.assertEqual([item["role"] for item in result], ["system", "user", "assistant"])
        self.assertEqual(result[-2]["content"], "问题9")

    def test_storage_failure_is_safe_and_does_not_call_provider(self):
        with patch.object(self.service.store, "initialize", side_effect=PermissionError("private-path")):
            with self.assertRaises(DeepSeekError) as error:
                self.send()
        self.assertEqual(error.exception.status_code, 503)
        self.assertNotIn("private-path", str(error.exception))
        self.assertEqual(self.payloads, [])

    def test_http_validation_history_and_non_sensitive_status(self):
        original = app.dependency_overrides.copy()
        self.addCleanup(lambda: (app.dependency_overrides.clear(), app.dependency_overrides.update(original)))
        app.dependency_overrides[get_settings] = lambda: self.settings
        app.dependency_overrides[get_chat_service] = lambda: self.service
        with TestClient(app) as client:
            status = client.get("/api/chat/status")
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["name"], "小灵")
            self.assertNotIn("test-key-not-valid", status.text)
            for extras in [{"role": "system"}, {"messages": []}, {"persona": "replace"}, {"image_ids": []}]:
                response = client.post("/api/chat/messages", json={"request_id": "1" * 32, "message": "你好", **extras})
                self.assertEqual(response.status_code, 422)
            for message in ["   ", "字" * 2001]:
                self.assertEqual(client.post("/api/chat/messages", json={"request_id": "1" * 32, "message": message}).status_code, 422)
            sent = client.post("/api/chat/messages", json={"request_id": "1" * 32, "message": "你好"})
            self.assertEqual(sent.status_code, 200, sent.text)
            target = sent.json()["conversation_id"]
            self.assertEqual(len(client.get(f"/api/chat/conversations/{target}").json()["messages"]), 2)
            self.assertEqual(len(client.get("/api/chat/conversations").json()), 1)
            self.assertEqual(client.get("/api/chat/conversations/" + "0" * 32).status_code, 404)
            self.assertEqual(client.get("/api/chat/conversations/../bad").status_code, 404)
            self.assertNotIn("test-key-not-valid", sent.text)

    def test_deleting_test_topic_also_removes_messages_and_cache(self):
        first = self.send()
        self.service.store.delete(first["conversation_id"])
        self.assertEqual(self.service.store.messages(first["conversation_id"]), [])
        self.assertIsNone(self.service.store.cached("1" * 32))


if __name__ == "__main__":
    unittest.main()

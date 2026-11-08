"""固定人格的连续对话；历史由服务端构造，成功后原子保存一整轮。"""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import threading
from uuid import uuid4

from app.core.config import Settings
from app.core.persona import PERSONA_ID, PERSONA_PROMPT
from app.services.chat_store import ChatConflict, ChatStore
from app.services.deepseek import DeepSeekClient, DeepSeekError, image_part


class TurnLocks:
    """当前本机单进程服务的非阻塞锁，用完释放条目，避免重复付费请求。"""
    def __init__(self):
        self.guard = threading.Lock()
        self.entries = {}

    @contextmanager
    def hold(self, key):
        with self.guard:
            entry = self.entries.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        acquired = entry[0].acquire(blocking=False)
        try:
            if not acquired:
                raise DeepSeekError("这个话题已有消息正在处理，请稍等。", 409)
            yield
        finally:
            if acquired:
                entry[0].release()
            with self.guard:
                entry[1] -= 1
                if not entry[1]:
                    del self.entries[key]


LOCKS = TurnLocks()


def context_messages(history: list[dict], settings: Settings, message: str) -> list[dict]:
    """仅截取完整的问答轮次，不把助手回复当成新话题的开头。"""
    pairs = [history[index:index + 2] for index in range(0, len(history) - 1, 2)]
    selected = []
    budget = max(0, settings.chat_context_chars - len(PERSONA_PROMPT) - len(message))
    for pair in reversed(pairs[-settings.chat_context_turns:]):
        if pair[0]["role"] != "user" or pair[1]["role"] != "assistant":
            continue
        size = sum(len(item["content"]) for item in pair)
        if size > budget:
            break
        selected[0:0] = [{"role": item["role"], "content": item["content"]} for item in pair]
        budget -= size
    return [{"role": "system", "content": PERSONA_PROMPT}, *selected]


class ChatService:
    def __init__(self, settings: Settings, client: DeepSeekClient | None = None):
        self.settings = settings
        self.store = ChatStore(settings.chat_database_path)
        self.client = client or DeepSeekClient(settings)

    def send(self, *, conversation_id: str | None, request_id: str, message: str, image_id: str | None) -> dict:
        try:
            self.store.initialize()
            body = {"conversation_id": conversation_id, "message": message, "image_id": image_id}
            fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            prefix = str(self.store.path)
            with LOCKS.hold(prefix + ":request:" + request_id):
                cached = self.store.cached(request_id)
                if cached:
                    if cached["fingerprint"] != fingerprint:
                        raise DeepSeekError("该消息编号已用于其他内容，请重新发送。", 409)
                    return {**json.loads(cached["response_json"]), "replayed": True}
                target = conversation_id or uuid4().hex
                with LOCKS.hold(prefix + ":conversation:" + target):
                    conversation = self.store.get(target) if conversation_id else None
                    if conversation_id and conversation is None:
                        raise DeepSeekError("话题不存在，请新建话题。", 404)
                    attached = (conversation or {}).get("image_id")
                    if attached and image_id and attached != image_id:
                        raise DeepSeekError("当前话题已关联另一张图片，请新建话题后再换图。", 409)
                    attached = image_id or attached
                    history = self.store.messages(target, self.settings.chat_context_turns * 2) if conversation else []
                    messages = context_messages(history, self.settings, message)
                    content = [{"type": "text", "text": message}, image_part(attached, self.settings)] if attached else message
                    messages.append({"role": "user", "content": content})
                    result = self.client.complete(messages)
                    return self.store.finish_turn(
                        conversation=conversation, conversation_id=target, image_id=attached,
                        message=message, model_reply=result.model_dump(), request_id=request_id,
                        fingerprint=fingerprint, persona_id=PERSONA_ID,
                    )
        except ChatConflict as exc:
            raise DeepSeekError(str(exc), 409) from exc
        except (OSError, sqlite3.Error) as exc:
            raise DeepSeekError("本机聊天记录暂时无法保存，请确认存储目录可写，或从 PyCharm 正常终端启动后端。", 503) from exc

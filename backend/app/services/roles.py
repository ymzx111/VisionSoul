"""仅小灵启用的角色管理；模型操作显式触发、有限上下文、成功请求可恢复。"""
import hashlib
import json
import threading

from app.core.persona import compile_profile, companion_rules, PROMPT_DIR
from app.core.role_card import DraftReply, MemoryExtraction, RoleProfile
from app.services.chat_store import ChatStore
from app.services.deepseek import DeepSeekClient, DeepSeekError
from app.services.role_store import RoleConflict, RoleStore

BUILDER_PROMPT = (PROMPT_DIR / "role_builder.md").read_text(encoding="utf-8")
MEMORY_PROMPT = (PROMPT_DIR / "memory_extract.md").read_text(encoding="utf-8")
OP_LOCK = threading.Lock()


class RoleService:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.store = RoleStore(settings.roles_path)
        self.chats = ChatStore(settings.chat_database_path)
        self.client = client or DeepSeekClient(settings)

    def perform(self, kind, request_id, body, action):
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if not OP_LOCK.acquire(blocking=False):
            raise DeepSeekError("角色页已有操作正在处理，请稍等。",409)
        try:
            cached = self.store.cached_operation(request_id,kind,fingerprint)
            if cached:
                return cached
            return action(request_id,kind,fingerprint)
        finally:
            OP_LOCK.release()

    def build(self, message, expected, request_id):
        def action(rid,kind,fp):
            card = self.store.current()
            if card["draft_revision"] != expected:
                raise RoleConflict("草稿已更新，请重新读取后再讨论。")
            messages = [{"role":"system","content":BUILDER_PROMPT},
                {"role":"user","content":"当前草稿："+json.dumps(card["draft"],ensure_ascii=False)},
                *card["messages"][-8:], {"role":"user","content":message}]
            reply = self.client.complete(messages,json_mode=True,max_tokens=1000)
            try:
                raw = json.loads(reply.answer)
                supplied = set(raw["profile"])
                minimum = {"name", "mode", "definition"} if raw["profile"].get("mode") == "free" else set(RoleProfile.model_fields) - {"mode", "definition"}
                if reply.truncated or not minimum <= supplied:
                    raise ValueError("incomplete profile")
                result = DraftReply.model_validate(raw)
            except (ValueError,KeyError,TypeError) as exc:
                raise DeepSeekError("角色草稿返回不完整，本次可能已有用量；不会自动重试。",502) from exc
            response = {"reply":result.reply,"usage":reply.usage.model_dump() if reply.usage else None,"cached":False}
            self.store.save(result.profile,expected,turn=(message,result.reply),operation=(rid,kind,fp,response))
            return response
        result = self.perform("builder",request_id,{"message":message,"expected":expected},action)
        return {**result,"card":self.store.current()}

    def preview(self, profile, message, request_id):
        def action(rid,kind,fp):
            if profile.mode == "free" and not profile.definition.strip():
                raise RoleConflict("先写一点人物设定，再试试角色回应。")
            prompt = compile_profile(profile)+"\n"+companion_rules(profile)
            prompt += "\n这是隔离的角色预览，不使用正式聊天记忆，不把测试称呼或经历保存为用户信息。用一两句回应测试消息。"
            reply = self.client.complete([{"role":"system","content":prompt},{"role":"user","content":message}],max_tokens=220)
            result = {**reply.model_dump(),"cached":False}
            with self.store.connection() as conn:
                self.store.record_operation(conn,rid,kind,fp,result)
            return result
        return self.perform("preview",request_id,{"profile":profile.model_dump(),"message":message},action)

    def extract(self, conversation_id, request_id):
        self.chats.initialize()
        conversation = self.chats.get(conversation_id)
        history = self.chats.messages(conversation_id,24)
        if not conversation or not history:
            raise RoleConflict("这个话题不存在或没有消息。")
        covered = history[-1]["id"]
        def action(rid,kind,fp):
            with self.store.connection() as conn:
                existing = conn.execute("SELECT response_json FROM memory_extractions WHERE role_id=? AND conversation_id=? AND covered_message_id=?",
                    (self.store.role_id,conversation_id,covered)).fetchone()
            if existing:
                return {**json.loads(existing[0]),"cached":True,"created_count":0}
            selected, budget = [],6000
            for item in reversed(history):
                if len(item["content"]) > budget:
                    break
                selected.insert(0,{key:item[key] for key in ["id","role","content","created_at"]})
                budget -= len(item["content"])
            previous = next((m["content"] for m in self.store.memories() if m["status"]=="confirmed" and m["category"]=="summary" and m["conversation_id"]==conversation_id),"")
            reply = self.client.complete([{"role":"system","content":MEMORY_PROMPT},
                {"role":"user","content":json.dumps({"previous_summary":previous,"messages":selected},ensure_ascii=False)}],json_mode=True,max_tokens=1200)
            try:
                if reply.truncated:
                    raise ValueError("truncated")
                result = MemoryExtraction.model_validate_json(reply.answer)
            except ValueError as exc:
                raise DeepSeekError("记忆整理结果不完整，本次可能已有用量；不会自动重试。",502) from exc
            sources = {m["id"]:m for m in selected if m["role"]=="user"}
            accepted = [m for m in result.memories if m.source_message_id in sources and m.quote in sources[m.source_message_id]["content"]]
            response = {"created_count":1+len(accepted),"skipped_count":len(result.memories)-len(accepted),
                "covered_messages":len(selected),"usage":reply.usage.model_dump() if reply.usage else None,"cached":False}
            with self.store.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.store.add_memory("summary",result.summary,status="pending",conversation_id=conversation_id,
                    source_title=conversation["title"],covered_message_id=covered,conn=conn)
                for memory in accepted:
                    self.store.add_memory(memory.category,memory.content,status="pending",conversation_id=conversation_id,
                        source_message_id=memory.source_message_id,quote=memory.quote,source_title=conversation["title"],conn=conn)
                conn.execute("INSERT INTO memory_extractions VALUES(?,?,?,?)", (self.store.role_id,conversation_id,covered,json.dumps(response)))
                self.store.record_operation(conn,rid,kind,fp,response)
            return response
        return self.perform("extract",request_id,{"conversation_id":conversation_id,"covered":covered},action)

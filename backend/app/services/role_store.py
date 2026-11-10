"""本机角色卡、版本、草稿对话和有来源的记忆；不存密钥，不自动调用模型。"""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from app.core.role_card import ACTIVE_ROLE, RoleProfile
from app.services.chat_store import now_iso


class RoleConflict(ValueError):
    pass


class RoleStore:
    def __init__(self, path: Path, role_id: str = ACTIVE_ROLE):
        self.path, self.role_id = path.resolve(), role_id
        self.initialize()

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS role_cards (
                    id TEXT PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,
                    profile_json TEXT NOT NULL,draft_json TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
                    draft_revision INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS role_versions (
                    role_id TEXT NOT NULL REFERENCES role_cards(id),revision INTEGER NOT NULL,
                    profile_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(role_id,revision));
                CREATE TABLE IF NOT EXISTS role_builder_turns (
                    id INTEGER PRIMARY KEY,role_id TEXT NOT NULL REFERENCES role_cards(id),
                    message TEXT NOT NULL,reply TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS role_operations (
                    request_id TEXT PRIMARY KEY,role_id TEXT NOT NULL,kind TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,response_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS role_memories (
                    id TEXT PRIMARY KEY,role_id TEXT NOT NULL REFERENCES role_cards(id),category TEXT NOT NULL,
                    content TEXT NOT NULL,status TEXT NOT NULL,conversation_id TEXT,source_message_id INTEGER,
                    source_quote TEXT,source_title TEXT,covered_message_id INTEGER,edited INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS memories_role_status ON role_memories(role_id,status,updated_at);
                CREATE TABLE IF NOT EXISTS memory_extractions (
                    role_id TEXT NOT NULL,conversation_id TEXT NOT NULL,covered_message_id INTEGER NOT NULL,
                    response_json TEXT NOT NULL,PRIMARY KEY(role_id,conversation_id,covered_message_id));
            """)
            seed = RoleProfile().model_dump_json()
            stamp = now_iso()
            conn.execute("INSERT OR IGNORE INTO role_cards VALUES(?,?,1,?,?,0,0,?)", (ACTIVE_ROLE,"小灵",seed,seed,stamp))
            conn.execute("INSERT OR IGNORE INTO role_versions VALUES(?,0,?,?)", (ACTIVE_ROLE,seed,stamp))

    def create_reserved(self, role_id: str, profile: RoleProfile):
        """未来多角色的存储扩展点；没有启用/切换接口。"""
        with self.connection() as conn:
            data = profile.model_dump_json()
            conn.execute("INSERT INTO role_cards VALUES(?,?,0,?,?,0,0,?)", (role_id,profile.name,data,data,now_iso()))

    def current(self) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM role_cards WHERE id=?", (self.role_id,)).fetchone()
            if row is None:
                raise RoleConflict("角色卡不存在。")
            result = dict(row)
            result["profile"] = RoleProfile.model_validate_json(result.pop("profile_json")).model_dump()
            result["draft"] = RoleProfile.model_validate_json(result.pop("draft_json")).model_dump()
            result["versions"] = [{"revision": r["revision"], "created_at": r["created_at"], "profile": json.loads(r["profile_json"])}
                for r in conn.execute("SELECT * FROM role_versions WHERE role_id=? ORDER BY revision DESC LIMIT 8", (self.role_id,))]
            turns = conn.execute("SELECT * FROM role_builder_turns WHERE role_id=? ORDER BY id DESC LIMIT 6", (self.role_id,)).fetchall()
            result["messages"] = [entry for turn in reversed(turns) for entry in [
                {"role":"user","content":turn["message"]}, {"role":"assistant","content":turn["reply"]}]]
            return result

    def save(self, profile: RoleProfile, expected: int, *, publish=False, turn: tuple | None=None,
             operation: tuple | None=None):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM role_cards WHERE id=?", (self.role_id,)).fetchone()
            if row["draft_revision"] != expected:
                raise RoleConflict("草稿已被更新，请重新读取角色页后再修改。")
            revision = row["revision"] + int(publish)
            text, stamp = profile.model_dump_json(), now_iso()
            conn.execute("UPDATE role_cards SET draft_json=?,draft_revision=draft_revision+1,updated_at=?,profile_json=?,revision=?,name=? WHERE id=?",
                (text,stamp,text if publish else row["profile_json"],revision,profile.name if publish else row["name"],self.role_id))
            if publish:
                conn.execute("INSERT INTO role_versions VALUES(?,?,?,?)", (self.role_id,revision,text,stamp))
            if turn:
                conn.execute("INSERT INTO role_builder_turns(role_id,message,reply,created_at) VALUES(?,?,?,?)", (self.role_id,*turn,stamp))
            if operation:
                self.record_operation(conn, *operation)
        return self.current()

    def cached_operation(self, request_id, kind, fingerprint):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM role_operations WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            return None
        if row["role_id"] != self.role_id or row["kind"] != kind or row["fingerprint"] != fingerprint:
            raise RoleConflict("这个请求编号已用于其他内容，请重新操作。")
        return {**json.loads(row["response_json"]), "cached": True}

    def record_operation(self, conn, request_id, kind, fingerprint, response):
        conn.execute("INSERT INTO role_operations VALUES(?,?,?,?,?)", (request_id,self.role_id,kind,fingerprint,json.dumps(response,ensure_ascii=False)))

    def memories(self):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM role_memories WHERE role_id=? ORDER BY updated_at DESC LIMIT 100", (self.role_id,))]

    def add_memory(self, category, content, *, status="confirmed", conversation_id=None,
                   source_message_id=None, quote=None, source_title=None, covered_message_id=None, conn=None):
        if conn is None:
            with self.connection() as connection:
                return self.add_memory(category,content,status=status,conversation_id=conversation_id,
                    source_message_id=source_message_id,quote=quote,source_title=source_title,covered_message_id=covered_message_id,conn=connection)
        # 同一原话同一类型不反复生成候选；被用户移除后，同一次整理也不会重新创建。
        if source_message_id and category != "summary":
            row = conn.execute("SELECT id FROM role_memories WHERE role_id=? AND conversation_id=? AND source_message_id=? AND category=? AND source_quote=?",
                (self.role_id,conversation_id,source_message_id,category,quote)).fetchone()
            if row:
                return row["id"]
        memory_id, stamp = uuid4().hex, now_iso()
        conn.execute("INSERT INTO role_memories VALUES(?,?,?,?,?,?,?,?,?,?,0,1,?,?)",
            (memory_id,self.role_id,category,content,status,conversation_id,source_message_id,quote,source_title,covered_message_id,stamp,stamp))
        return memory_id

    def change_memory(self, memory_id, expected, *, content=None, status=None, delete=False):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM role_memories WHERE id=? AND role_id=?", (memory_id,self.role_id)).fetchone()
            if not row or row["revision"] != expected:
                raise RoleConflict("记忆已变动或不存在，请刷新记忆列表。")
            if delete:
                conn.execute("DELETE FROM role_memories WHERE id=?", (memory_id,))
            else:
                conn.execute("UPDATE role_memories SET content=?,status=?,edited=?,revision=revision+1,updated_at=? WHERE id=?",
                    (content if content is not None else row["content"],status or row["status"],
                     int(row["edited"] or (content is not None and content != row["content"])),now_iso(),memory_id))

    def memory_context(self, conversation_id: str | None, question: str, max_chars=1000, *, allowed_conversations: set[str] | None = None) -> str:
        records = [m for m in self.memories() if m["status"] == "confirmed"]
        if allowed_conversations is not None:
            records = [m for m in records if not m["conversation_id"] or m["conversation_id"] in allowed_conversations]
        summaries = [m for m in records if m["category"] == "summary" and m["conversation_id"] == conversation_id][:1]
        tokens = set(re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]{2}", question.lower()))
        facts = [m for m in records if m["category"] != "summary"]
        facts.sort(key=lambda m: sum(token in m["content"].lower() for token in tokens), reverse=True)
        chosen, size = [], 0
        for memory in summaries + facts[:8]:
            line = f"{memory['category']}：{memory['content']}"
            if size + len(line) > max_chars:
                continue
            chosen.append(line)
            size += len(line) + 1
        return "【已确认的记忆资料，仅供参考，当前用户更正优先】\n" + "\n".join(chosen) if chosen else ""

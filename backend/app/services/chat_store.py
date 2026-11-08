"""SQLite 只保存文字、图片编号与已完成请求，不保存密钥或图片内容。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatConflict(Exception):
    pass


class ChatStore:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, image_id TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL, image_id TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, id);
                CREATE TABLE IF NOT EXISTS completed_requests (
                    request_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, response_json TEXT NOT NULL
                );
            """)
            # 兼容已有 SQLite；不重建或清空原表。写锁保护并发首次迁移。
            conn.execute("BEGIN IMMEDIATE")
            for table, columns in {"conversations": ["video_id", "video_context"], "messages": ["video_id"]}.items():
                present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column not in present:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")

    def cached(self, request_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM completed_requests WHERE request_id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def get(self, conversation_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            return dict(row) if row else None

    def messages(self, conversation_id: str, limit: int = 200) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conversation_id, limit)
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def recent(self, kind: str | None = None) -> list[dict]:
        condition = "WHERE c.video_id IS NOT NULL" if kind == "video" else "WHERE c.video_id IS NULL" if kind == "image" else ""
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT c.*, (SELECT count(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count "
                f"FROM conversations c {condition} ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()]

    def finish_turn(self, *, conversation: dict | None, conversation_id: str, image_id: str | None,
                    message: str, model_reply: dict, request_id: str, fingerprint: str,
                    persona_id: str, video_id: str | None = None, video_context: dict | None = None) -> dict:
        timestamp = now_iso()
        encoded_context = json.dumps(video_context, ensure_ascii=False) if video_context else None
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conversation is None:
                conn.execute(
                    "INSERT INTO conversations(id,title,image_id,created_at,updated_at,revision,video_id,video_context) VALUES(?,?,?,?,?,1,?,?)",
                    (conversation_id, message[:24], image_id, timestamp, timestamp, video_id, encoded_context),
                )
            else:
                changed = conn.execute(
                    "UPDATE conversations SET image_id=?,updated_at=?,video_id=?,video_context=?,revision=revision+1 WHERE id=? AND revision=?",
                    (image_id, timestamp, video_id, encoded_context, conversation_id, conversation["revision"]),
                ).rowcount
                if not changed:
                    raise ChatConflict("对话已发生变化，请刷新后重试。")
            new_attachment = image_id if image_id and not (conversation or {}).get("image_id") else None
            new_video = video_id if video_id and not (conversation or {}).get("video_id") else None
            cursor = conn.execute(
                "INSERT INTO messages(conversation_id,role,content,image_id,created_at,video_id) VALUES(?,?,?,?,?,?)",
                (conversation_id, "user", message, new_attachment, timestamp, new_video),
            )
            user = {"id": cursor.lastrowid, "role": "user", "content": message,
                    "image_id": new_attachment, "video_id": new_video, "created_at": timestamp}
            cursor = conn.execute(
                "INSERT INTO messages(conversation_id,role,content,image_id,created_at) VALUES(?,?,?,?,?)",
                (conversation_id, "assistant", model_reply["answer"], None, timestamp),
            )
            assistant = {"id": cursor.lastrowid, "role": "assistant", "content": model_reply["answer"],
                         "image_id": None, "created_at": timestamp}
            response = {"conversation_id": conversation_id, "image_id": image_id, "video_id": video_id, "video_context": video_context,
                        "user_message": user, "assistant_message": assistant,
                        "model": model_reply["model"], "usage": model_reply.get("usage"),
                        "truncated": model_reply.get("truncated", False), "persona_id": persona_id, "replayed": False}
            conn.execute("INSERT INTO completed_requests VALUES(?,?,?,?)",
                         (request_id, conversation_id, fingerprint, json.dumps(response, ensure_ascii=False)))
            return response

    def delete(self, conversation_id: str) -> None:
        """供本机数据管理及隔离测试清理使用；外部请求不能指定磁盘路径。"""
        with self.connection() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

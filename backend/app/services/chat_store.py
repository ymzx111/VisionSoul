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


def media_row(row) -> dict:
    result = dict(row)
    result["image_ids"] = json.loads(result["image_ids"]) if result.get("image_ids") else (
        [result["image_id"]] if result.get("image_id") else [])
    return result


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
            for table, columns in {"conversations": ["video_id", "video_context", "image_ids"],
                                   "messages": ["video_id", "image_ids"]}.items():
                present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column not in present:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            present = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
            if "space" not in present:
                conn.execute("ALTER TABLE conversations ADD COLUMN space TEXT NOT NULL DEFAULT 'legacy'")

    def companion_ids(self) -> set[str]:
        with self.connection() as conn:
            return {row[0] for row in conn.execute("SELECT id FROM conversations WHERE space='companion'")}

    def classify(self, conversation_id: str, space: str) -> dict:
        if space not in {"work", "companion"}:
            raise ChatConflict("请选择工作解析或角色对话。")
        with self.connection() as conn:
            changed = conn.execute("UPDATE conversations SET space=?,revision=revision+1 WHERE id=? AND space='legacy'",
                                   (space, conversation_id)).rowcount
            if not changed:
                raise ChatConflict("话题不存在或已分类，请刷新；已分类话题不能跨空间移动。")
        return self.get(conversation_id)

    def cached(self, request_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM completed_requests WHERE request_id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def get(self, conversation_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            return media_row(row) if row else None

    def messages(self, conversation_id: str, limit: int = 200) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?", (conversation_id, limit)
            ).fetchall()
            return [media_row(row) for row in reversed(rows)]

    def recent_image_turns(self, conversation_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT content, image_id, image_ids FROM messages WHERE conversation_id=? AND role='user' "
                "AND (image_ids IS NOT NULL OR image_id IS NOT NULL) ORDER BY id DESC LIMIT 12",
                (conversation_id,),
            ).fetchall()
            return [media_row(row) for row in rows]

    def task_history(self, conversation_id: str) -> tuple[str, list[str]]:
        with self.connection() as conn:
            opening = conn.execute(
                "SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id LIMIT 1",
                (conversation_id,),
            ).fetchone()
            replies = conn.execute(
                "SELECT content FROM messages WHERE conversation_id=? AND role='assistant' "
                "AND instr(content, ?) > 0 ORDER BY id DESC LIMIT 12",
                (conversation_id, "```"),
            ).fetchall()
            return opening["content"] if opening else "", [row["content"] for row in replies]

    def recent(self, kind: str | None = None, space: str | None = None) -> list[dict]:
        filters = ["c.video_id IS NOT NULL"] if kind == "video" else ["c.video_id IS NULL"] if kind == "image" else []
        if space is not None:
            filters.append("c.space=?")
        condition = "WHERE " + " AND ".join(filters) if filters else ""
        with self.connection() as conn:
            return [media_row(row) for row in conn.execute(
                "SELECT c.*, (SELECT count(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count "
                f"FROM conversations c {condition} ORDER BY updated_at DESC LIMIT 20", (space,) if space is not None else ()
            ).fetchall()]

    def finish_turn(self, *, conversation: dict | None, conversation_id: str, image_id: str | None,
                    message: str, model_reply: dict, request_id: str, fingerprint: str,
                    persona_id: str, video_id: str | None = None, video_context: dict | None = None, space: str = "companion",
                    image_ids: list[str] | None = None) -> dict:
        timestamp = now_iso()
        encoded_context = json.dumps(video_context, ensure_ascii=False) if video_context else None
        new_attachment = image_id if image_id and not (conversation or {}).get("image_id") else None
        turn_images = image_ids or ([new_attachment] if new_attachment else [])
        primary_images = turn_images if new_attachment else (conversation or {}).get("image_ids") or []
        encoded_images = json.dumps(primary_images) if primary_images else None
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conversation is None:
                conn.execute(
                    "INSERT INTO conversations(id,title,image_id,created_at,updated_at,revision,video_id,video_context,space,image_ids) VALUES(?,?,?,?,?,1,?,?,?,?)",
                    (conversation_id, message[:24], image_id, timestamp, timestamp, video_id, encoded_context, space, encoded_images),
                )
            else:
                changed = conn.execute(
                    "UPDATE conversations SET image_id=?,updated_at=?,video_id=?,video_context=?,image_ids=?,revision=revision+1 WHERE id=? AND revision=?",
                    (image_id, timestamp, video_id, encoded_context, encoded_images, conversation_id, conversation["revision"]),
                ).rowcount
                if not changed:
                    raise ChatConflict("对话已发生变化，请刷新后重试。")
            new_video = video_id if video_id and not (conversation or {}).get("video_id") else None
            cursor = conn.execute(
                "INSERT INTO messages(conversation_id,role,content,image_id,created_at,video_id,image_ids) VALUES(?,?,?,?,?,?,?)",
                (conversation_id, "user", message, new_attachment, timestamp, new_video, json.dumps(turn_images) if turn_images else None),
            )
            user = {"id": cursor.lastrowid, "role": "user", "content": message,
                    "image_id": new_attachment, "image_ids": turn_images, "video_id": new_video, "created_at": timestamp}
            cursor = conn.execute(
                "INSERT INTO messages(conversation_id,role,content,image_id,created_at) VALUES(?,?,?,?,?)",
                (conversation_id, "assistant", model_reply["answer"], None, timestamp),
            )
            assistant = {"id": cursor.lastrowid, "role": "assistant", "content": model_reply["answer"],
                         "image_id": None, "created_at": timestamp}
            response = {"space": space, "conversation_id": conversation_id, "image_id": image_id, "image_ids": primary_images,
                        "video_id": video_id, "video_context": video_context,
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

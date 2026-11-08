"""由服务端固定的人格；客户端只能发送普通用户消息。"""
from pathlib import Path

PERSONA_ID = "xiaoling-v1"
PERSONA_NAME = "小灵"
PERSONA_STYLE = "活泼俏皮，轻度幽默"
PERSONA_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "companion.md").read_text(encoding="utf-8").strip()

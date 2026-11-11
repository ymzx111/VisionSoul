"""本机伙伴对话。客户端只提供用户文字，不能注入历史角色或系统提示。"""
import json
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings, get_settings
from app.core.chat_space import ChatSpace, WORK_PROMPT
from app.core.persona import PERSONA_ID, PERSONA_NAME, PERSONA_STYLE, load_persona
from app.services.role_store import RoleStore
from app.services.chat import ChatService, LOCKS
from app.services.chat_store import ChatStore, ChatConflict
from app.services.deepseek import DeepSeekError, TokenUsage, has_api_key
from app.services.videos import VideoService, VideoError
from app.services.audio_evidence import current_context

router = APIRouter(prefix="/api/chat", tags=["伙伴对话"])
Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class SendMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", title="发送伙伴消息")
    conversation_id: Identifier | None = Field(default=None, title="话题编号", description="第一次发送留空，后续使用返回的话题编号。")
    request_id: Identifier = Field(title="消息请求编号", description="前端自动生成；原样重试已完成的请求不会再调用模型。")
    message: str = Field(max_length=2000, title="你的消息", description="新视频话题可留空，由模型从视频判断任务。")
    space: ChatSpace = Field(default="companion", title="对话空间", description="工作解析不读取角色或记忆；同话题不能跨空间。")
    inspect_video: bool = Field(default=False,title="重新细看视频并比较候选",description="显式重新发送画面，可能增加模型用量；普通追问为false。")
    debug_code: bool = Field(default=False,title="从视频辅助排查代码报错",description="显式读取最多4张高清并生成建议，不执行代码；产生API用量。")
    image_id: Identifier | None = Field(default=None, title="可选图片编号", description="一段话题最多关联一张图片。")
    video_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(default=None, title="可选视频编号", description="先上传视频，再与文字一起发送；同话题后续可不再填写。")

    @model_validator(mode="after")
    def one_media(self):
        if self.image_id and self.video_id:
            raise ValueError("一次只能分享一张图片或一个视频。")
        if self.inspect_video and self.debug_code:
            raise ValueError('一次只能选择一种视频分析任务。')
        return self


class ChatMessage(BaseModel):
    model_config = ConfigDict(title="对话消息")
    id: int
    role: Literal["user", "assistant"]
    content: str
    image_id: str | None = None
    video_id: str | None = None
    created_at: str


class ChatTurn(BaseModel):
    model_config = ConfigDict(title="伙伴回复")
    conversation_id: str
    space: ChatSpace
    image_id: str | None
    video_id: str | None = None
    video_context: dict | None = None
    user_message: ChatMessage
    assistant_message: ChatMessage
    model: str
    usage: TokenUsage | None = None
    truncated: bool = False
    persona_id: str
    replayed: bool = False


def get_chat_service(settings: Annotated[Settings, Depends(get_settings)]) -> ChatService:
    return ChatService(settings)


def open_store(settings: Settings) -> ChatStore:
    store = ChatStore(settings.chat_database_path)
    try:
        store.initialize()
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "本机聊天记录暂不可用，请确认存储目录可写。") from exc
    return store


@router.get("/status", summary="查看伙伴配置（不调用模型）")
def chat_status(settings: Annotated[Settings, Depends(get_settings)], space: ChatSpace = "companion") -> dict:
    open_store(settings)
    if space == "work":
        return {"configured": has_api_key(settings), "persona_id": "work-v1", "name": "解析助手",
                "style": "客观、清晰、直接", "space": space, "context_turns": settings.chat_context_turns}
    roles = RoleStore(settings.roles_path)
    card = roles.current()
    profile = card["profile"]
    style = "自定义人物" if profile["mode"] == "free" else profile["traits"][:32] if card["revision"] else PERSONA_STYLE
    return {"configured": has_api_key(settings), "persona_id": load_persona(settings,roles)[2], "name": profile["name"],
            "style": style, "space": space, "context_turns": settings.chat_context_turns}


@router.get("/conversations", summary="查看本机最近二十个话题")
def conversations(settings: Annotated[Settings, Depends(get_settings)], kind: Literal["image", "video", "all"] = "image",
                  space: Literal["work", "companion", "legacy"] = "companion") -> list[dict]:
    try:
        return open_store(settings).recent(kind, space)
    except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        raise HTTPException(503, "暂时无法读取本机话题。") from exc


@router.get("/conversations/{conversation_id}", summary="恢复一段话题（不调用模型）")
def conversation(
    conversation_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", title="话题编号")],
    settings: Annotated[Settings, Depends(get_settings)],
    space: ChatSpace = "companion",
) -> dict:
    try:
        store = open_store(settings)
        item = store.get(conversation_id)
        if item is None:
            raise HTTPException(404, "话题不存在，请新建话题。")
        if item["space"] != space:
            raise HTTPException(409, "话题不属于当前空间，未分类旧话题需先分类。")
        messages = store.messages(conversation_id, 201)
        video_context = item.pop("video_context", None)
        context = json.loads(video_context) if video_context else None
        if context and item.get("video_id"):
            record = VideoService(settings, system_prompt=WORK_PROMPT, isolated=True).audio_record(item["video_id"])
            context = current_context(context, record["audio"], record["revision"], record["sound_clues"])
        return {**item, "video_context": context,
                "messages": messages[-200:], "has_older": len(messages) > 200}
    except VideoError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        raise HTTPException(503, "暂时无法读取本机话题。") from exc


class ClassifyConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    space: ChatSpace


@router.post("/conversations/{conversation_id}/classify", summary="明确归类旧话题（不调用模型，不移动已分类话题）")
def classify_conversation(conversation_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
                          body: ClassifyConversation, settings: Annotated[Settings, Depends(get_settings)]):
    store = open_store(settings)
    try:
        with LOCKS.hold(str(store.path) + ":conversation:" + conversation_id):
            item = store.classify(conversation_id, body.space)
        # 不返回旧综合报告；工作空间保留的旧文本可能仍有角色口吻，继续回复采用新策略。
        return {key: item[key] for key in ("id", "title", "space")}
    except (ChatConflict, DeepSeekError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "旧话题暂时无法分类，请稍后重试。") from exc


@router.post("/messages", response_model=ChatTurn, summary="发送消息给小灵（产生 API 用量）")
def send_message(request: SendMessage, service: Annotated[ChatService, Depends(get_chat_service)]) -> dict:
    try:
        return service.send(**request.model_dump())
    except DeepSeekError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

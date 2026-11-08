"""本机小灵角色与记忆管理，无其他角色启用入口。"""
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.core.role_card import RoleProfile
from app.services.deepseek import DeepSeekError, has_api_key
from app.services.role_store import RoleConflict
from app.services.roles import RoleService

router = APIRouter(prefix="/api/roles/xiaoling",tags=["角色与记忆"])
ID = Annotated[str,Field(pattern=r"^[0-9a-f]{32}$")]


class Request(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)


class SaveCard(Request):
    profile: RoleProfile
    expected: int = Field(ge=0,title="当前草稿版本")


class BuildCard(Request):
    message: str = Field(min_length=1,max_length=1500,title="想讨论的设定")
    expected: int = Field(ge=0)
    request_id: ID


class Preview(Request):
    profile: RoleProfile
    message: str = Field(min_length=1,max_length=1000,title="预览消息")
    request_id: ID


class Extract(Request):
    conversation_id: ID
    request_id: ID


class AddMemory(Request):
    category: Literal["preference","fact"]
    content: str = Field(min_length=1,max_length=240,title="明确记住的信息")


class EditMemory(Request):
    expected: int = Field(ge=1)
    content: str | None = Field(default=None,min_length=1,max_length=600)
    status: Literal["pending","confirmed"] | None = None


def service(settings: Annotated[Settings,Depends(get_settings)]):
    try:
        return RoleService(settings)
    except (OSError,sqlite3.Error) as exc:
        raise HTTPException(503,"本机角色资料暂时无法读取，请检查存储权限。") from exc


def run(action):
    try:
        return action()
    except RoleConflict as exc:
        raise HTTPException(409,str(exc)) from exc
    except DeepSeekError as exc:
        raise HTTPException(exc.status_code,str(exc)) from exc
    except (OSError,sqlite3.Error,ValueError) as exc:
        raise HTTPException(503,"角色资料暂时无法保存或读取；刚才若调用模型，仍可能已有用量。") from exc


@router.get("",summary="查看小灵角色卡与草稿（不调用模型）")
def current(s: Annotated[RoleService,Depends(service)]):
    return run(lambda:{"card":s.store.current(),"configured":has_api_key(s.settings)})


@router.put("/draft",summary="只保存角色草稿（不生效、不调用模型）")
def draft(body: SaveCard,s: Annotated[RoleService,Depends(service)]):
    def action():
        s.check_profile(body.profile)
        return s.store.save(body.profile,body.expected)
    return run(action)


@router.post("/publish",summary="保存并启用小灵设定（不调用模型）")
def publish(body: SaveCard,s: Annotated[RoleService,Depends(service)]):
    def action():
        s.check_profile(body.profile)
        return s.store.save(body.profile,body.expected,publish=True)
    return run(action)


@router.post("/discuss",summary="对话整理人格草稿（调用模型）")
def discuss(body: BuildCard,s: Annotated[RoleService,Depends(service)]):
    return run(lambda:s.build(body.message,body.expected,body.request_id))


@router.post("/preview",summary="隔离预览草稿表现（调用模型，不写聊天记忆）")
def preview(body: Preview,s: Annotated[RoleService,Depends(service)]):
    return run(lambda:s.preview(body.profile,body.message,body.request_id))


@router.get("/sources",summary="选择可整理的本机话题（不调用模型）")
def sources(s: Annotated[RoleService,Depends(service)]):
    def action():
        s.chats.initialize()
        return [{key:r[key] for key in ["id","title","updated_at","message_count"]} for r in s.chats.recent()]
    return run(action)


@router.get("/memories",summary="查看小灵的记忆卡（不调用模型）")
def memories(s: Annotated[RoleService,Depends(service)]):
    return run(s.store.memories)


@router.post("/memories",summary="手动添加明确记忆（不调用模型）")
def add_memory(body: AddMemory,s: Annotated[RoleService,Depends(service)]):
    return run(lambda:{"id":s.store.add_memory(body.category,body.content)})


@router.patch("/memories/{memory_id}",summary="编辑或确认记忆（不调用模型）")
def edit_memory(memory_id: ID,body: EditMemory,s: Annotated[RoleService,Depends(service)]):
    return run(lambda:s.store.change_memory(memory_id,body.expected,content=body.content,status=body.status))


@router.delete("/memories/{memory_id}",summary="移除记忆卡条目（不删除原聊天）")
def delete_memory(memory_id: ID,expected: Annotated[int,Query(ge=1)],s: Annotated[RoleService,Depends(service)]):
    return run(lambda:s.store.change_memory(memory_id,expected,delete=True))


@router.post("/extract",summary="从选定话题整理摘要和候选记忆（调用模型）")
def extract(body: Extract,s: Annotated[RoleService,Depends(service)]):
    return run(lambda:s.extract(body.conversation_id,body.request_id))

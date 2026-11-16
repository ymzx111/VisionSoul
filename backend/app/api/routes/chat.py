"""本机伙伴对话。客户端只提供用户文字，不能注入历史角色或系统提示。"""
import json
import re
import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
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
from app.services.local_ide import (
    REQUIRES_DATA_FILE, open_generated_source, row_acceptance, sandbox_input_mode, show_sandbox_result, split_model_cases,
)
from app.services.c_sandbox import add_missing_standard_headers, debug_c, run_c_sandbox
from app.services.code_reruns import source_snapshot
from app.services.python_sandbox import (
    ApprovedFile, PythonCheck, PythonTest, check_python_before_data, debug_python, run_python_sandbox,
)
from app.services.sandbox_output import (
    c_text_output, declared_text_output, python_text_output, save_sandbox_output,
)
from app.services.source_files import CODE_REQUEST, DELIVERY_LANGUAGE_MESSAGE
from app.services.sandbox_diagnostics import diagnose_run
from app.services.file_row_check import requests_row_check

router = APIRouter(prefix="/api/chat", tags=["伙伴对话"])
Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class SendMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", title="发送伙伴消息")
    conversation_id: Identifier | None = Field(default=None, title="话题编号", description="第一次发送留空，后续使用返回的话题编号。")
    request_id: Identifier = Field(title="消息请求编号", description="前端自动生成；原样重试已完成的请求不会再调用模型。")
    message: str = Field(max_length=2000, title="你的消息", description="新视频话题可留空，由模型从视频判断任务。")
    space: ChatSpace = Field(default="companion", title="对话空间", description="工作解析不读取角色或记忆；同话题不能跨空间。")
    inspect_video: bool = Field(default=False,title="重新细看视频并比较候选",description="显式重新发送画面，可能增加模型用量；普通追问为false。")
    open_in_ide: bool = Field(default=False, title="在本机打开生成的源码")
    python_test: PythonTest | None = Field(default=None, title="本轮本机 Python 或 C 验收样例")
    debug_code: bool = Field(default=False,title="从视频辅助排查代码报错",description="显式读取最多4张高清并生成建议，不执行代码；产生API用量。")
    image_id: Identifier | None = Field(default=None, title="可选主素材图片编号")
    image_ids: list[Identifier] = Field(default_factory=list, max_length=5, title="本轮图片编号（含主素材，最多五张）")
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
    image_ids: list[str] = Field(default_factory=list)
    video_id: str | None = None
    created_at: str


class LocalSourceResult(BaseModel):
    status: Literal["opened", "saved", "error"]
    detail: str
    path: str | None = None
    ide: str | None = None
    python_check: PythonCheck | None = None
    python_run_id: Identifier | None = None
    sandbox_run_id: Identifier | None = None
    desktop_result_opened: bool | None = None


class ChatTurn(BaseModel):
    model_config = ConfigDict(title="伙伴回复")
    conversation_id: str
    space: ChatSpace
    image_id: str | None
    image_ids: list[str] = Field(default_factory=list)
    video_id: str | None = None
    video_context: dict | None = None
    user_message: ChatMessage
    assistant_message: ChatMessage
    model: str
    usage: TokenUsage | None = None
    truncated: bool = False
    persona_id: str
    replayed: bool = False
    local_source: LocalSourceResult | None = None


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


@router.get("/conversations", summary="查看本机最近话题（工作解析最多十五条）")
def conversations(settings: Annotated[Settings, Depends(get_settings)], kind: Literal["image", "video", "all"] = "image",
                  space: Literal["work", "companion", "legacy"] = "companion") -> list[dict]:
    try:
        return open_store(settings).recent(kind, space, limit=15 if space == "work" else 20)
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


def is_local_browser(request: Request) -> bool:
    loopback = {"127.0.0.1", "::1", "localhost"}
    return bool(request.client and request.client.host in loopback and request.url.hostname in loopback
                and request.headers.get("origin") == f"{request.url.scheme}://{request.url.netloc}")


class SandboxRerun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: Identifier
    stdin: str = Field(default="", max_length=4096)
    expected: str | None = Field(default=None, max_length=4096)
    approved_file: ApprovedFile | None = None
    open_result_window: bool = True


class SandboxRerunResult(PythonCheck):
    desktop_result_opened: bool


def route_sandbox_input(test: PythonTest, mode: str) -> PythonTest:
    if mode == "stdin" and test.approved_file:
        # The file is authorized for this run. Feed its text through stdin, without mounting it.
        return test.model_copy(update={"stdin": test.approved_file.content, "approved_file": None, "cases": []})
    if mode == "file":
        return test.model_copy(update={"stdin": "", "cases": [case.model_copy(update={"stdin": ""}) for case in test.cases]})
    return test


@router.post("/sandbox-runs", response_model=SandboxRerunResult, summary="补充输入并在本机重新试跑源码")
@router.post("/python-runs", response_model=SandboxRerunResult, include_in_schema=False)
def rerun_source(body: SandboxRerun, request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    if not is_local_browser(request):
        raise HTTPException(403, "请从本机工作台发起试跑。")
    snapshot = source_snapshot(body.run_id)
    if snapshot is None:
        raise HTTPException(404, "这份源码的试跑入口已失效，请在新的代码回复中试跑。")
    code, source, language, requires_file, output_name, row_check = snapshot
    if language not in {"python", "c"}:
        raise HTTPException(422, DELIVERY_LANGUAGE_MESSAGE)
    original_code = code
    mode = sandbox_input_mode(code, language, requires_file=requires_file)
    test = route_sandbox_input(PythonTest(stdin=body.stdin, expected=body.expected, approved_file=body.approved_file), mode)
    file_problem = row_check.file_problem(body.approved_file.content) if row_check and body.approved_file else None
    if mode in {"file", "both"} and not body.approved_file:
        check = PythonCheck(status="awaiting_file", detail="这道题需要本地数据。请在文件选择器里打开存放数据的目录，选中文件并授权本轮试跑。", attempts=0)
    elif file_problem:
        check = PythonCheck(status="file_unused", detail=file_problem, attempts=0)
    elif language == "python":
        if not settings.python_sandbox_enabled:
            raise HTTPException(503, "本机 Python 沙箱已关闭。")
        code, output_name = python_text_output(code, output_name)
        check = run_python_sandbox(settings, code, test)
    else:
        if not settings.c_sandbox_enabled:
            raise HTTPException(503, "本机 C 沙箱已关闭。")
        code, output_name = c_text_output(code, output_name)
        check = run_c_sandbox(settings, code, language, test)
    check = save_sandbox_output(settings.local_source_workspace, output_name, check)
    check = diagnose_run(check, original_code, language, code)
    check = check.model_copy(update={"input_mode": mode})
    if body.approved_file:
        if mode == "stdin":
            check = check.model_copy(update={"stdin_file": body.approved_file.filename,
                "detail": f"标准输入来自：{body.approved_file.filename} · {check.detail}"})
        else:
            check = check.model_copy(update={"detail": f"本轮选择：{body.approved_file.filename} · {check.detail}"})
    opened = body.open_result_window and show_sandbox_result(source, check, run_id=body.run_id,
                                                              origin=f"{request.url.scheme}://{request.url.netloc}")
    return {**check.model_dump(), "desktop_result_opened": opened}


@router.post("/messages", response_model=ChatTurn, summary="发送消息给小灵（产生 API 用量）")
def send_message(body: SendMessage, request: Request, service: Annotated[ChatService, Depends(get_chat_service)]) -> dict:
    local_delivery = (body.open_in_ide and is_local_browser(request) and body.space == "work"
                      and not body.inspect_video and not body.debug_code)
    approved = body.python_test.approved_file if body.python_test else None
    try:
        turn = service.send(**body.model_dump(exclude={"open_in_ide", "python_test"}),
                            sandbox_filename=approved.filename if local_delivery and approved else None,
                            auto_sample_retry=local_delivery and not (body.python_test and body.python_test.has_local_data))
    except DeepSeekError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    if (local_delivery and turn["space"] == "work"
            and (turn.get("_source_delivery") or turn["image_id"] or turn["video_id"] or CODE_REQUEST.search(body.message))
            and not turn["replayed"] and not turn["truncated"]):
        manual_test = body.python_test or PythonTest()
        reply = turn["assistant_message"]["content"]
        _, model_test = split_model_cases(reply)
        output_name = declared_text_output(reply)
        workspace = service.settings.local_source_workspace
        file_in_request = re.search(
            r"(?:[a-zA-Z]:[\\/]|\\\\)[^\n]*?\.(?:txt|csv|json)\b", body.message, re.IGNORECASE)
        requires_file = bool(model_test.approved_file or file_in_request or REQUIRES_DATA_FILE.search(reply))
        video_context = turn.get("video_context") or {}
        task = "\n".join([body.message, video_context.get("summary", ""),
                          *(row.get("description", "") for row in video_context.get("observations", []))])
        row_required = requests_row_check(reply) or (requires_file and requests_row_check("", task))
        row_check, row_problem = row_acceptance(reply, model_test, required=row_required)
        require_row_acceptance = row_required or row_check is not None or row_problem is not None
        requires_file |= require_row_acceptance
        sample_has_stdin = any(case.stdin.strip() for case in model_test.cases)

        def show_approved_file(check: PythonCheck, mode: str) -> PythonCheck:
            if not approved:
                return check
            if mode == "stdin":
                return check.model_copy(update={"stdin_file": approved.filename,
                    "detail": f"标准输入来自：{approved.filename} · {check.detail}"})
            return check.model_copy(update={"detail": f"本轮选择：{approved.filename} · {check.detail}"})

        def verify_delivery(run, result_name: str | None, mode: str, formal_test: PythonTest,
                            code: str, runtime_code: str, language: str) -> PythonCheck | None:
            needs_file = mode in {"file", "both"}
            if row_problem:
                return PythonCheck(status="failed", case_source="model", attempts=0,
                                   detail=f"逐行自测未通过。{row_problem}")
            sample_available = bool(model_test.approved_file) if needs_file else bool(model_test.cases)
            if sample_available:
                sample = save_sandbox_output(workspace, result_name, run(model_test), persist=False)
                sample = diagnose_run(sample, code, language, runtime_code)
                if sample.status not in {"ran", "passed"} or (row_check and sample.status != "passed"):
                    return sample.model_copy(update={
                        "case_source": "model", "data_file": None, "sample_file": model_test.approved_file,
                        "detail": f"沙箱自测未通过。{sample.detail}",
                    })
                summary = ("沙箱样例试跑成功，已对照样例预期。" if sample.status == "passed"
                           else "沙箱样例试跑成功，部分或全部样例未核对预期。")
                if row_check:
                    summary += " 已核对坏行警告和后续合法记录。"
                if needs_file and not approved:
                    return PythonCheck(status="awaiting_file", case_source="model",
                                       detail=f"{summary} 请选择真实数据文件，再点击「授权并试跑」。")
                if not needs_file and sample_has_stdin and not formal_test.cases and not formal_test.stdin and not approved:
                    return PythonCheck(status="awaiting_input", case_source="model",
                                       case_results=sample.case_results,
                                       detail=f"{summary} 请填写真实输入后再试跑。")
            if needs_file and not approved:
                return None
            if row_check and approved:
                file_problem = row_check.file_problem(approved.content)
                if file_problem:
                    return show_approved_file(PythonCheck(status="file_unused", detail=file_problem, attempts=0), mode)
            if sample_available or approved or result_name:
                formal = save_sandbox_output(workspace, result_name, run(formal_test))
                return show_approved_file(diagnose_run(formal, code, language, runtime_code), mode)
            return None

        def input_settings(code: str, language: str) -> tuple[str, PythonTest]:
            mode = sandbox_input_mode(code, language, requires_file=requires_file)
            if mode == "none" and approved:
                mode = "file"
            return mode, route_sandbox_input(manual_test, mode)

        def verify_python(code: str) -> PythonCheck:
            runtime_code, result_name = python_text_output(code, output_name)
            mode, formal_test = input_settings(code, "python")
            if mode in {"stdin", "stdin_optional_file"} and not sample_has_stdin and not manual_test.has_local_data:
                check = check_python_before_data(code)
                if check.status != "awaiting_file":
                    return diagnose_run(check, code, "python").model_copy(update={"input_mode": mode})
                return PythonCheck(status="awaiting_input", phase="compile", attempts=0,
                                   input_mode=mode,
                                   detail="语法检查通过，但未取得非空自测输入。可重新生成代码和样例，或填写输入和参考输出试跑。")
            check = verify_delivery(
                lambda test: run_python_sandbox(service.settings, runtime_code, test),
                result_name, mode, formal_test, code, runtime_code, "python")
            if check is None:
                if mode in {"file", "both"}:
                    check = diagnose_run(check_python_before_data(code), code, "python")
                else:
                    check = diagnose_run(debug_python(service.settings, service.client, turn["user_message"]["content"],
                                                      code, formal_test), code, "python")
            return check.model_copy(update={"input_mode": mode})

        def verify_c(code: str, language: str) -> PythonCheck:
            runtime_code, result_name = c_text_output(code, output_name)
            mode, formal_test = input_settings(code, language)
            repaired_source = None
            header_attempted = False

            def run(test: PythonTest, *, compile_only: bool = False) -> PythonCheck:
                nonlocal runtime_code, repaired_source, header_attempted
                check = run_c_sandbox(service.settings, runtime_code, language, test, compile_only=compile_only)
                if header_attempted:
                    return check
                candidate = add_missing_standard_headers(code, check)
                if candidate == code:
                    return check
                header_attempted = True
                candidate_runtime, _ = c_text_output(candidate, output_name)
                verified = run_c_sandbox(service.settings, candidate_runtime, language, test, compile_only=compile_only)
                if verified.status not in {"passed", "ran", "awaiting_file", "awaiting_input"}:
                    return check
                repaired_source, runtime_code = candidate, candidate_runtime
                return verified

            check = verify_delivery(run, result_name, mode, formal_test, code, runtime_code, language)
            if check is None:
                if mode in {"file", "both"}:
                    check = run(PythonTest(), compile_only=True)
                else:
                    check = debug_c(service.settings, service.client, turn["user_message"]["content"], code,
                                    language, formal_test, initial_check=run(formal_test))
                check = diagnose_run(check, repaired_source or code, language, runtime_code)
            if repaired_source:
                check = check.model_copy(update={
                    "repaired_code": repaired_source,
                    "detail": f"已按编译器提示补齐标准头文件。{check.detail}",
                })
            return check.model_copy(update={"input_mode": mode})

        turn["local_source"] = open_generated_source(
            service.settings, turn,
            verify_python if service.settings.python_sandbox_enabled else None,
            verify_c if service.settings.c_sandbox_enabled else None,
            origin=f"{request.url.scheme}://{request.url.netloc}", requires_file=requires_file, output_name=output_name,
            row_check=row_check, require_row_acceptance=require_row_acceptance)
    return turn

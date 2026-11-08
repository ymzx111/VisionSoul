"""图片与聊天共用的官方 API 客户端。"""
import base64

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.services.images import find_image

API_URL = "https://api.deepseek.com/chat/completions"


class DeepSeekError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class TokenUsage(BaseModel):
    model_config = ConfigDict(title="模型用量")
    prompt_tokens: int = Field(ge=0, title="输入用量", description="图片、对话和提问的输入 token 数量。")
    completion_tokens: int = Field(ge=0, title="输出用量", description="回复的输出 token 数量。")
    total_tokens: int = Field(ge=0, title="总用量", description="本次 token 总数，不是金额。")


class ModelReply(BaseModel):
    answer: str
    model: str
    usage: TokenUsage | None = None
    truncated: bool = False


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message
    finish_reason: str | None = None


class _Completion(BaseModel):
    choices: list[_Choice] = Field(min_length=1)
    model: str
    usage: TokenUsage | None = None


def has_api_key(settings: Settings) -> bool:
    return bool(settings.deepseek_api_key.get_secret_value().strip())


def image_part(image_id: str, settings: Settings) -> dict:
    found = find_image(image_id, settings)
    if found is None:
        message = "图片编号未找到，请从上传结果复制 image_id，或在工作台重新选择图片。"
        if image_id == "0" * 32:
            message = "当前填写的是全零示例编号，请先上传图片并使用实际返回的 image_id。"
        raise DeepSeekError(message, 404)
    path, media_type = found
    try:
        with path.open("rb") as source:
            data = source.read(settings.upload_max_bytes + 1)
    except OSError as exc:
        raise DeepSeekError("图片暂时无法读取，请重新上传。", 500) from exc
    if not data or len(data) > settings.upload_max_bytes:
        raise DeepSeekError("已保存的图片内容异常，请重新上传。", 400)
    encoded = base64.b64encode(data).decode("ascii")
    return {"type": "image_url", "image_url": {
        "url": f"data:{media_type};base64,{encoded}", "detail": settings.deepseek_image_detail,
    }}


class DeepSeekClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    def complete(self, messages: list[dict]) -> ModelReply:
        if not has_api_key(self.settings):
            raise DeepSeekError("尚未配置 DeepSeek API Key，请在 backend/.env 中填写后重启服务。", 503)
        key = self.settings.deepseek_api_key.get_secret_value().strip()
        if not key.isascii() or any(c.isspace() for c in key):
            raise DeepSeekError("DeepSeek 密钥格式不正确，请检查后端配置。", 503)
        payload = {"model": self.settings.deepseek_model, "messages": messages,
                   "thinking": {"type": "disabled"}, "max_tokens": self.settings.deepseek_max_tokens, "stream": False}
        try:
            timeout = httpx.Timeout(self.settings.deepseek_timeout_seconds, connect=10.0)
            with httpx.Client(timeout=timeout, transport=self.transport, follow_redirects=False) as client:
                response = client.post(API_URL, headers={"Authorization": "Bearer " + key}, json=payload)
        except httpx.TimeoutException as exc:
            raise DeepSeekError("DeepSeek 响应超时，请稍后再试。", 504) from exc
        except httpx.RequestError as exc:
            raise DeepSeekError("暂时无法连接 DeepSeek，请检查网络后重试。", 502) from exc
        if response.status_code in {401, 403}:
            raise DeepSeekError("DeepSeek 密钥认证失败，请检查后端 API Key 配置。", 503)
        if response.status_code == 402:
            raise DeepSeekError("DeepSeek API 余额不足，请检查开放平台账户。", 503)
        if response.status_code == 429:
            raise DeepSeekError("DeepSeek 请求过于频繁，请稍后再试。", 503)
        if not 200 <= response.status_code < 300:
            raise DeepSeekError("DeepSeek 暂时无法完成请求，请稍后再试。", 502)
        try:
            completion = _Completion.model_validate(response.json())
        except ValueError as exc:
            raise DeepSeekError("DeepSeek 返回了无法解析的结果，请稍后再试。", 502) from exc
        choice = completion.choices[0]
        answer = (choice.message.content or "").strip()
        if not answer:
            raise DeepSeekError("DeepSeek 没有返回有效回复，请换一个问题重试。", 502)
        return ModelReply(answer=answer, model=completion.model, usage=completion.usage,
                          truncated=choice.finish_reason == "length")

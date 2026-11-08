"""单次图片理解，复用固定人格与官方 API 客户端。"""
import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.persona import PERSONA_PROMPT, load_persona
from app.services.role_store import RoleStore
from app.services.deepseek import API_URL, DeepSeekClient, DeepSeekError, TokenUsage, has_api_key, image_part

# 保留既有导入名称，保证接口和原有验证可继续使用。
VisionServiceError = DeepSeekError
SYSTEM_PROMPT = PERSONA_PROMPT + "\n本次是单张图片理解：先回答图片中的事实，再按用户问题自然回应。"


class VisionAnswer(BaseModel):
    model_config = ConfigDict(title="图片理解结果")
    image_id: str = Field(title="图片编号", description="本次分析的图片。")
    answer: str = Field(title="理解回复", description="模型对图片和问题的回答。")
    model: str = Field(title="使用模型", description="DeepSeek 实际返回的模型名。")
    usage: TokenUsage | None = Field(default=None, title="模型用量", description="服务商返回的 token 用量。")
    truncated: bool = Field(default=False, title="回复是否被截断", description="true 表示达到输出上限。")


class DeepSeekVisionService:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.client = DeepSeekClient(settings, transport)

    def analyze(self, image_id: str, question: str) -> VisionAnswer:
        picture = image_part(image_id, self.settings)
        _, prompt, _ = load_persona(self.settings)
        memory = RoleStore(self.settings.roles_path).memory_context(None,question)
        result = self.client.complete([
            {"role": "system", "content": prompt + "\n本次是单张图片理解：先回答图片中的事实，再按用户问题自然回应。"},
            *([{"role":"user","content":memory}] if memory else []),
            {"role": "user", "content": [{"type": "text", "text": question}, picture]},
        ])
        return VisionAnswer(image_id=image_id, **result.model_dump())

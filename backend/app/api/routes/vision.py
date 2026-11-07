"""图片理解接口；只接收已上传图片的标识，不暴露密钥。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.services.vision import DeepSeekVisionService, VisionAnswer, VisionServiceError, has_api_key

router = APIRouter(prefix="/api/vision", tags=["图片理解"])


class VisionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid", title="图片理解请求")

    image_id: str = Field(pattern=r"^[0-9a-f]{32}$", title="图片编号",
                          description="把示例中的编号替换为上传成功返回的 image_id。", examples=["0" * 32])
    question: str = Field(
        default="请描述这张图片的主要场景、物体和值得注意的细节。",
        min_length=1, max_length=2000, title="提问内容", description="你想了解图片的什么内容？最多 2000 个字符。",
    )


class VisionStatus(BaseModel):
    model_config = ConfigDict(title="图片理解配置")

    configured: bool = Field(title="是否已填密钥", description="true 表示本机已填写密钥，不代表认证或余额已验证。")
    model: str = Field(title="使用模型", description="当前配置的 DeepSeek API 模型名。")
    image_detail: str = Field(title="图片精度", description="low 为节省用量模式；high 保留更多细节。")
    max_tokens: int = Field(title="输出上限", description="单次回复最多使用的 token 数量。")


def get_vision_service(settings: Annotated[Settings, Depends(get_settings)]) -> DeepSeekVisionService:
    return DeepSeekVisionService(settings)


@router.get("/status", response_model=VisionStatus, summary="查看图片理解配置状态（不调用模型）",
            description="免费检查后端是否填写密钥、使用什么模型和图片精度。不会返回密钥，也不会访问模型。",
            response_description="密钥填写状态和模型配置")
def vision_status(settings: Annotated[Settings, Depends(get_settings)]) -> VisionStatus:
    return VisionStatus(
        configured=has_api_key(settings), model=settings.deepseek_model,
        image_detail=settings.deepseek_image_detail, max_tokens=settings.deepseek_max_tokens,
    )


@router.post(
    "/analyze", response_model=VisionAnswer, summary="理解已上传图片（调用 DeepSeek，产生 API 用量）",
    description="第二步：填写上传后返回的 image_id 和你的 question（问题）。点击发送后，图片和问题将交给 DeepSeek。",
    response_description="图片理解回复及本次用量",
    responses={404: {"description": "图片编号不存在，请先上传图片"}, 422: {"description": "图片编号或问题格式不正确"},
               503: {"description": "密钥未配置、认证失败、余额不足或请求频繁"},
               502: {"description": "连接失败或模型返回异常结果"}, 504: {"description": "等待模型响应超时"}},
)
def analyze_image(
    request: VisionRequest,
    service: Annotated[DeepSeekVisionService, Depends(get_vision_service)],
) -> VisionAnswer:
    try:
        return service.analyze(request.image_id, request.question)
    except VisionServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

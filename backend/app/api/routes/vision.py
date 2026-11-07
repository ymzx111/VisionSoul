"""图片理解接口；只接收已上传图片的标识，不暴露密钥。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.services.vision import DeepSeekVisionService, VisionAnswer, VisionServiceError, has_api_key

router = APIRouter(prefix="/api/vision", tags=["图片理解"])


class VisionRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    image_id: str = Field(pattern=r"^[0-9a-f]{32}$", description="图片上传接口返回的 image_id")
    question: str = Field(
        default="请描述这张图片的主要场景、物体和值得注意的细节。",
        min_length=1, max_length=2000,
    )


class VisionStatus(BaseModel):
    configured: bool
    model: str
    image_detail: str
    max_tokens: int


def get_vision_service(settings: Annotated[Settings, Depends(get_settings)]) -> DeepSeekVisionService:
    return DeepSeekVisionService(settings)


@router.get("/status", response_model=VisionStatus, summary="查看图片理解配置状态（不调用模型）")
def vision_status(settings: Annotated[Settings, Depends(get_settings)]) -> VisionStatus:
    return VisionStatus(
        configured=has_api_key(settings), model=settings.deepseek_model,
        image_detail=settings.deepseek_image_detail, max_tokens=settings.deepseek_max_tokens,
    )


@router.post("/analyze", response_model=VisionAnswer, summary="理解已上传图片（调用 DeepSeek，产生 API 用量）")
def analyze_image(
    request: VisionRequest,
    service: Annotated[DeepSeekVisionService, Depends(get_vision_service)],
) -> VisionAnswer:
    try:
        return service.analyze(request.image_id, request.question)
    except VisionServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

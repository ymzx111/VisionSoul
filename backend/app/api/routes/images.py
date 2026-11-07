"""图片上传和预览接口。"""

import logging
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.services.images import ImageUploadError, find_image, save_image

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/images", tags=["图片"])


class ImageUploaded(BaseModel):
    model_config = ConfigDict(title="图片上传结果")

    image_id: str = Field(title="图片编号", description="用于预览或图片理解的随机编号；复制给后续接口。")
    media_type: str = Field(title="图片格式", description="实际保存的图片类型，例如 image/png。")
    size_bytes: int = Field(title="文件大小", description="处理后的文件大小，单位为字节。")
    width: int = Field(title="图片宽度", description="处理后的宽度，单位为像素。")
    height: int = Field(title="图片高度", description="处理后的高度，单位为像素。")
    url: str = Field(title="预览地址", description="接在当前服务器地址后即可打开图片。")


@router.post(
    "", status_code=201, response_model=ImageUploaded, summary="上传一张图片",
    description="第一步：选择一张静态图片上传。成功后复制返回的 image_id（图片编号），供预览或图片理解使用。",
    response_description="上传成功，返回图片编号和预览地址",
    responses={400: {"description": "图片为空、损坏或为动图"}, 413: {"description": "大小或分辨率超限"},
               415: {"description": "不支持的图片格式"}, 422: {"description": "缺少图片文件"},
               500: {"description": "图片保存失败"}},
)
def upload_image(
    file: Annotated[UploadFile, File(title="图片文件", description="选择 JPG、PNG 或 WebP 静态图片；默认不超过 10 MB。")],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ImageUploaded:
    try:
        data = file.file.read(settings.upload_max_bytes + 1)
        saved = save_image(data, settings)
    except ImageUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except OSError as exc:
        logger.exception("无法保存上传图片")
        raise HTTPException(status_code=500, detail="图片暂时无法保存，请稍后重试。") from exc
    finally:
        file.file.close()
    return ImageUploaded(**asdict(saved), url=f"/api/images/{saved.image_id}")


@router.get("/{image_id}", response_class=FileResponse, summary="预览已上传图片",
            description="填写上传时返回的图片编号，查看已保存的图片。", response_description="图片内容",
            responses={404: {"description": "没有找到这个编号的图片"}})
def get_image(
    image_id: Annotated[str, Path(title="图片编号", description="填写上传成功后返回的 image_id，不是文件名。")],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    result = find_image(image_id, settings)
    if result is None:
        raise HTTPException(status_code=404, detail="图片不存在。")
    path, media_type = result
    return FileResponse(
        path,
        media_type=media_type,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )

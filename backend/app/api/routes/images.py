"""图片上传和预览接口。"""

import logging
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.services.images import ImageUploadError, find_image, save_image

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/images", tags=["图片"])


class ImageUploaded(BaseModel):
    image_id: str
    media_type: str
    size_bytes: int
    width: int
    height: int
    url: str


@router.post("", status_code=201, response_model=ImageUploaded, summary="上传一张图片")
def upload_image(
    file: Annotated[UploadFile, File(description="JPG、PNG 或 WebP 静态图片；默认不超过 10 MB")],
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


@router.get("/{image_id}", response_class=FileResponse, summary="预览已上传图片")
def get_image(
    image_id: str,
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

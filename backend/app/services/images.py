"""图片内容验证、规范化保存及按标识查找。"""

import re
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import Settings

FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}


class ImageUploadError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SavedImage:
    image_id: str
    media_type: str
    size_bytes: int
    width: int
    height: int


def save_image(data: bytes, settings: Settings) -> SavedImage:
    """不信任文件名/MIME；解码后重新编码，移除 EXIF、尾随内容等元数据。"""
    if not data:
        raise ImageUploadError("图片内容为空。")
    if len(data) > settings.upload_max_bytes:
        raise ImageUploadError("图片超过大小限制。", 413)

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                image_format = image.format
                if image_format not in FORMATS:
                    raise ImageUploadError("只支持 JPG、PNG 和 WebP 图片。", 415)
                width, height = image.size
                if (
                    max(width, height) > settings.image_max_dimension
                    or width * height > settings.image_max_pixels
                ):
                    raise ImageUploadError("图片分辨率过大，请缩小后上传。", 413)
                if getattr(image, "is_animated", False):
                    raise ImageUploadError("当前仅支持静态图片。")
                image.verify()

            with Image.open(BytesIO(data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
                if image_format == "JPEG":
                    mode = "RGB"
                pixels = oriented.convert(mode)
                # 新建像素画布，防止原文件 info/exif 在保存时被自动沿用。
                clean = Image.new(mode, pixels.size)
                clean.paste(pixels)
                buffer = BytesIO()
                options = {"quality": 90} if image_format in {"JPEG", "WEBP"} else {}
                clean.save(buffer, format=image_format, **options)
                encoded = buffer.getvalue()
                width, height = clean.size
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageUploadError("图片分辨率过大，请缩小后上传。", 413) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError) as exc:
        if isinstance(exc, ImageUploadError):
            raise
        raise ImageUploadError("文件不是有效图片，或图片已经损坏。") from exc

    if len(encoded) > settings.upload_max_bytes:
        raise ImageUploadError("处理后的图片超过大小限制，请缩小后上传。", 413)

    extension, media_type = FORMATS[image_format]
    image_id = uuid4().hex
    directory = settings.image_storage_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{image_id}.{extension}"
    # 打开失败时不删除文件，避免极端情况下 UUID 碰撞导致删除已有图片。
    target = destination.open("xb")
    try:
        with target:
            target.write(encoded)
    except OSError:
        # 清理由本次写入留下的不完整文件。
        destination.unlink(missing_ok=True)
        raise
    return SavedImage(image_id, media_type, len(encoded), width, height)


def find_image(image_id: str, settings: Settings) -> tuple[Path, str] | None:
    if not re.fullmatch(r"[0-9a-f]{32}", image_id):
        return None
    directory = settings.image_storage_dir.resolve()
    for extension, media_type in FORMATS.values():
        candidate = directory / f"{image_id}.{extension}"
        if candidate.is_file() and not candidate.is_symlink() and candidate.resolve().parent == directory:
            return candidate, media_type
    return None

"""本机短视频工作流：上传抽帧不计模型用量，手动分析才调用 API。"""
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Path, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.core.chat_space import WORK_PROMPT
from app.core.video_policy import MAX_SAMPLES, SAMPLE_INTERVAL_SECONDS
from app.services.deepseek import DeepSeekError, has_api_key
from app.services.videos import VideoAnswer, VideoError, VideoInfo, VideoService, video_directory
from app.services.video_details import detail_path, adjusted_path
from app.services.video_focus import focus_path
from app.services.audio_evidence import SpeechSegment, AudioTranscript

router = APIRouter(prefix="/api/videos", tags=["短视频观察"])
logger = logging.getLogger(__name__)
VideoID = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$", title="视频编号")]


def get_video_service(settings: Annotated[Settings, Depends(get_settings)]) -> VideoService:
    return VideoService(settings, system_prompt=WORK_PROMPT, isolated=True)


class VideoStatus(BaseModel):
    configured: bool = Field(title="是否已配置密钥")
    max_bytes: int = Field(title="视频大小上限")
    max_seconds: float = Field(title="时长上限")
    max_frames: int = Field(title="新视频采样上限，旧半秒记录仍可读取")
    sample_interval_seconds: float = Field(default=SAMPLE_INTERVAL_SECONDS,title="采样间隔秒")


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", title="视频分析请求")
    video_id: str = Field(pattern=r"^[0-9a-f]{64}$", title="已上传的视频编号")


class AudioEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    segments: list[SpeechSegment] = Field(min_length=1, max_length=64)


class AudioRecord(BaseModel):
    original: AudioTranscript
    audio: AudioTranscript
    revision: int
    sound_clues: dict | None = None


@router.get("/{video_id}/audio", response_model=AudioRecord, summary="读取原始与校对白（不调用模型）")
def audio_record(video_id: VideoID, service: Annotated[VideoService, Depends(get_video_service)]):
    try:
        return service.audio_record(video_id)
    except (VideoError, OSError) as exc:
        raise safe_error(exc) from exc


@router.put("/{video_id}/audio", response_model=AudioRecord, summary="保存校对白（不调用模型、不重写历史回复）")
def edit_audio(video_id: VideoID, request: AudioEdit, service: Annotated[VideoService, Depends(get_video_service)]):
    try:
        return service.correct_audio(video_id, request.expected_revision, request.segments)
    except (VideoError, OSError) as exc:
        raise safe_error(exc) from exc


@router.get("/status", response_model=VideoStatus, summary="查看视频限制与密钥填写状态（不调用模型）")
def status(settings: Annotated[Settings, Depends(get_settings)]):
    return VideoStatus(configured=has_api_key(settings), max_bytes=settings.video_max_bytes,
                       max_seconds=settings.video_max_seconds, max_frames=settings.video_max_frames)


def safe_error(error):
    if isinstance(error, (VideoError, DeepSeekError)):
        return HTTPException(status_code=error.status_code, detail=str(error))
    logger.exception("本机视频存储操作失败")
    return HTTPException(status_code=500, detail="本机视频存储暂时不可用，请检查权限。若刚调用分析，可能已有模型用量。")


@router.post("", response_model=VideoInfo, status_code=201, summary="本地抽帧与对白识别（不调用付费 API）")
def upload(file: Annotated[UploadFile, File(description="H.264 MP4，不超过30秒、50 MB；每1秒采样并补末帧，最多31次；本机转写对白，不调用付费模型。")],
           service: Annotated[VideoService, Depends(get_video_service)]):
    try:
        return service.upload(file.file)
    except (VideoError, DeepSeekError, OSError) as exc:
        raise safe_error(exc) from exc
    finally:
        file.file.close()


@router.post("/analyze", response_model=VideoAnswer, summary="识别主要事物并生成角色回应（可能产生 API 用量）",
             description="首次分析合并采样画面为一次模型请求；成功后保存结果，同视频同配置直接复用，不自动重试。")
def analyze(request: AnalyzeRequest, service: Annotated[VideoService, Depends(get_video_service)]):
    try:
        return service.analyze(request.video_id)
    except (VideoError, DeepSeekError, OSError) as exc:
        raise safe_error(exc) from exc


@router.get("/{video_id}", response_model=VideoInfo, summary="读取本机视频及已有结果（不调用模型）")
def info(video_id: VideoID, service: Annotated[VideoService, Depends(get_video_service)]):
    try:
        return service.info(video_id)
    except (VideoError, OSError) as exc:
        raise safe_error(exc) from exc


@router.get("/{video_id}/file", summary="播放本机已上传视频", response_class=FileResponse)
def video_file(video_id: VideoID, settings: Annotated[Settings, Depends(get_settings)]):
    try:
        path = video_directory(video_id, settings) / "source.mp4"
        if not path.is_file():
            raise VideoError("视频文件不存在。", 404)
        return FileResponse(path, media_type="video/mp4", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
    except (VideoError, OSError) as exc:
        raise safe_error(exc) from exc


@router.get("/{video_id}/frames/{index}", summary="查看对应时间的采样截图", response_class=FileResponse)
def frame_file(video_id: VideoID, index: Annotated[int, Path(ge=0, le=MAX_SAMPLES-1)],
               settings: Annotated[Settings, Depends(get_settings)]):
    try:
        path = video_directory(video_id, settings) / f"frame-{index}.jpg"
        if not path.is_file():
            raise VideoError("采样画面不存在。", 404)
        return FileResponse(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
    except (VideoError, OSError) as exc:
        raise safe_error(exc) from exc


@router.get("/{video_id}/frames/{index}/detail", summary="查看较清晰的采样图", response_class=FileResponse)
def detail_frame(video_id: VideoID,index: Annotated[int,Path(ge=0,le=MAX_SAMPLES-1)],settings: Annotated[Settings,Depends(get_settings)],
                 variant: Literal["nearby", "enhanced"] | None = None):
    try:
        directory=video_directory(video_id,settings)
        path = adjusted_path(directory, index, variant) if variant else detail_path(directory, index)
        if variant is None and not path.is_file():
            path = directory / f"hq-source-1920-v1-{index}.jpg"
        if variant is None and not path.is_file():
            path = directory / f"detail-{index}.jpg"
        if not path.is_file():
            raise VideoError("这个视频没有细节图，可重新选择原视频生成。",404)
        return FileResponse(path,media_type="image/jpeg",headers={"X-Content-Type-Options":"nosniff","Cache-Control":"private, no-store"})
    except (VideoError,OSError) as exc:
        raise safe_error(exc) from exc


@router.get("/{video_id}/focus-frames/{milliseconds}", summary="查看重点区间的原始画面", response_class=FileResponse)
def focus_frame_file(video_id: VideoID, milliseconds: Annotated[int, Path(ge=0, le=30000)],
                     settings: Annotated[Settings, Depends(get_settings)]):
    try:
        path = focus_path(video_directory(video_id, settings), milliseconds)
        if not path.is_file():
            raise VideoError("重点画面不存在。", 404)
        return FileResponse(path, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
    except (VideoError, OSError) as exc:
        raise safe_error(exc) from exc

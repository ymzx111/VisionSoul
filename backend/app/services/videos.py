"""短视频保存、隔离抽帧和一次请求分析；成功结果按视频与配置缓存。"""
import base64
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.services.deepseek import DeepSeekClient, DeepSeekError, TokenUsage, has_api_key

logger = logging.getLogger(__name__)
VIDEO_PROMPT = (Path(__file__).resolve().parents[1] / "prompts" / "video.md").read_text(encoding="utf-8")
PIPELINE_VERSION = "video-v2-uniform-30-exact-dedup"
UPLOAD_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()


@contextmanager
def upload_workspace(root: Path):
    # Windows Python 3.13 的 TemporaryDirectory 使用 0700，持久化重命名后仍保留
    # 创建者专用 ACL。普通 mkdir 继承项目目录访问规则，适合后续进程读取。
    directory = (root / (".upload-" + uuid4().hex)).resolve()
    if directory.parent != root:
        raise VideoError("视频存储位置异常。", 500)
    directory.mkdir()
    try:
        yield directory
    finally:
        # 重命名成功后源目录已不存在；只清理本次、项目视频根目录内的临时目录。
        if directory.exists():
            if directory.resolve().parent != root or not directory.name.startswith(".upload-"):
                raise VideoError("视频临时目录位置异常，未清理。", 500)
            shutil.rmtree(directory)


class VideoError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class SampleFrame(BaseModel):
    index: int = Field(ge=0, le=29, title="采样编号")
    time_seconds: float = Field(ge=0, le=30, title="采样时间")
    seen_at: list[float] = Field(min_length=1, max_length=30, title="相同画面的采样时间")
    url: str = Field(title="截图地址")


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    frame_index: int = Field(ge=0, le=29, title="依据采样编号")
    keywords: list[str] = Field(min_length=1, max_length=4, title="主要事物")
    description: str = Field(min_length=1, max_length=120, title="画面描述")
    uncertain: bool = Field(title="是否待确认")


class VideoContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    observations: list[Observation] = Field(min_length=1, max_length=8, title="观察记录")
    summary: str = Field(min_length=1, max_length=240, title="视频概述")
    reply: str = Field(min_length=1, max_length=240, title="小灵回应")


class VideoAnswer(VideoContent):
    video_id: str = Field(title="视频编号")
    model: str = Field(title="使用模型")
    usage: TokenUsage | None = Field(default=None, title="原分析用量")
    cached: bool = Field(default=False, title="是否复用结果")


class VideoInfo(BaseModel):
    video_id: str = Field(title="视频编号")
    duration_seconds: float = Field(gt=0, le=30, title="视频时长")
    width: int = Field(gt=0, title="画面宽度")
    height: int = Field(gt=0, title="画面高度")
    sample_interval_seconds: float = Field(ge=0, title="目标采样间隔")
    sample_count: int = Field(ge=1, le=30, title="采样次数")
    frames: list[SampleFrame] = Field(min_length=1, max_length=30, title="去重后的画面")
    video_url: str = Field(title="播放地址")
    result: VideoAnswer | None = Field(default=None, title="已有分析结果")


def video_directory(video_id: str, settings: Settings) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", video_id):
        raise VideoError("视频不存在，请重新上传。", 404)
    root = settings.video_storage_dir.resolve()
    directory = (root / video_id).resolve()
    if directory.parent != root or not (directory / "manifest.json").is_file():
        raise VideoError("视频不存在，请重新上传。", 404)
    return directory


class VideoService:
    def __init__(self, settings: Settings, client: DeepSeekClient | None = None):
        self.settings = settings
        self.client = client or DeepSeekClient(settings)

    def _cache_path(self, directory: Path) -> Path:
        # 密钥不参与指纹；提示/输出预算/抽帧记录变化时不会误用旧结果。
        fingerprint = hashlib.sha256((PIPELINE_VERSION + VIDEO_PROMPT + self.settings.deepseek_model
            + str(self.settings.video_max_tokens) + (directory / "manifest.json").read_text(encoding="utf-8")).encode()).hexdigest()[:24]
        return directory / f"analysis-{fingerprint}.json"

    def _cached(self, directory: Path) -> VideoAnswer | None:
        path = self._cache_path(directory)
        if not path.is_file():
            return None
        try:
            return VideoAnswer.model_validate_json(path.read_text(encoding="utf-8")).model_copy(update={"cached": True})
        except (ValueError, OSError) as exc:
            # 缓存损坏时不静默重新付费，要求人工处理。
            raise VideoError("已保存的分析结果无法读取，请检查本机存储。", 500) from exc

    def info(self, video_id: str) -> VideoInfo:
        directory = video_directory(video_id, self.settings)
        try:
            data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if data.get("pipeline_version") != PIPELINE_VERSION:
                raise VideoError("采样策略已更新，请重新选择原视频，生成整段均匀采样的画面。旧视频和结果仍保存在本机。", 409)
            for frame in data["frames"]:
                frame["url"] = f"/api/videos/{video_id}/frames/{frame['index']}"
            return VideoInfo(**data, video_id=video_id, video_url=f"/api/videos/{video_id}/file", result=self._cached(directory))
        except (KeyError, ValidationError, json.JSONDecodeError) as exc:
            raise VideoError("本机视频记录无法读取，请重新上传。", 500) from exc

    def upload(self, source) -> VideoInfo:
        if not UPLOAD_LOCK.acquire(blocking=False):
            raise VideoError("已有视频正在抽帧，请稍后再上传。", 409)
        try:
            root = self.settings.video_storage_dir.resolve()
            root.mkdir(parents=True, exist_ok=True)
            with upload_workspace(root) as directory:
                if directory.parent != root:
                    raise VideoError("视频存储位置异常。", 500)
                digest = hashlib.sha256((PIPELINE_VERSION + str(self.settings.video_max_frames)
                    + str(self.settings.video_max_seconds)).encode())
                total = 0
                with (directory / "source.mp4").open("wb") as output:
                    while chunk := source.read(1024 * 1024):
                        total += len(chunk)
                        if total > self.settings.video_max_bytes:
                            raise VideoError("视频超过大小限制，默认最多 50 MB。", 413)
                        digest.update(chunk)
                        output.write(chunk)
                if not total:
                    raise VideoError("视频内容为空。")
                video_id = digest.hexdigest()
                target = root / video_id
                if (target / "manifest.json").is_file():
                    return self.info(video_id)
                command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("video_extract.py")),
                    str(directory), str(self.settings.video_max_seconds), str(self.settings.video_max_frames)]
                try:
                    process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=self.settings.video_decode_timeout_seconds,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except subprocess.TimeoutExpired as exc:
                    raise VideoError("视频处理超时，请缩短视频或降低分辨率后上传。", 422) from exc
                if process.returncode:
                    error_path = directory / "extract-error.json"
                    message = json.loads(error_path.read_text(encoding="utf-8"))["detail"] if error_path.is_file() else "视频无法解码，请重新导出。"
                    raise VideoError(message, 422)
                if not (directory / "manifest.json").is_file():
                    raise VideoError("视频抽帧没有完成，请重新上传。", 422)
                manifest_path = directory / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest["pipeline_version"] = PIPELINE_VERSION
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                directory.rename(target)
                return self.info(video_id)
        finally:
            UPLOAD_LOCK.release()

    def analyze(self, video_id: str) -> VideoAnswer:
        directory = video_directory(video_id, self.settings)
        cached = self._cached(directory)
        if cached:
            return cached
        if not ANALYSIS_LOCK.acquire(blocking=False):
            raise VideoError("已有视频正在分析，请等待完成后再试。", 409)
        try:
            cached = self._cached(directory)
            if cached:
                return cached
            if not has_api_key(self.settings):
                raise DeepSeekError("尚未配置 DeepSeek API Key，请配置后重启服务。", 503)
            info = self.info(video_id)
            parts = [{"type": "text", "text": "请按上述 json 格式分析以下有序视频采样。只观察画面，不包含音频。"}]
            for frame in info.frames:
                parts.append({"type": "text", "text": f"帧编号 {frame.index}；采样秒数 {frame.seen_at}"})
                encoded = base64.b64encode((directory / f"frame-{frame.index}.jpg").read_bytes()).decode("ascii")
                parts.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded, "detail": "low"}})
            reply = self.client.complete([{"role": "system", "content": VIDEO_PROMPT},
                {"role": "user", "content": parts}], json_mode=True, max_tokens=self.settings.video_max_tokens)
            try:
                if reply.truncated:
                    raise ValueError("truncated")
                content = VideoContent.model_validate_json(reply.answer)
                indices = [item.frame_index for item in content.observations]
                if len(set(indices)) != len(indices) or any(index >= len(info.frames) for index in indices):
                    raise ValueError("unknown frame")
                for observation in content.observations:
                    if any(not keyword.strip() or len(keyword) > 24 for keyword in observation.keywords):
                        raise ValueError("invalid keyword")
                content.observations.sort(key=lambda item: item.frame_index)
                # 部分模型仍会重复描述邻近画面；仅合并关键词和确定程度相同的相邻记录。
                merged = []
                for observation in content.observations:
                    signature = (set(observation.keywords), observation.uncertain)
                    if merged and signature == (set(merged[-1].keywords), merged[-1].uncertain):
                        continue
                    merged.append(observation)
                content.observations = merged
            except ValueError as exc:
                raise DeepSeekError("模型返回的结构化结果不完整，本次可能已有用量；不会自动重试。", 502) from exc
            answer = VideoAnswer(**content.model_dump(), video_id=video_id, model=reply.model, usage=reply.usage)
            path = self._cache_path(directory)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".analysis-", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(answer.model_dump_json())
                os.replace(temporary, path)
            except OSError as exc:
                # 明确提示可能已计费，防止用户以为没有调用而反复点击。
                raise VideoError("分析已调用模型，但本机结果保存失败；请检查存储权限，重试可能再次产生用量。", 500) from exc
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return answer
        finally:
            ANALYSIS_LOCK.release()

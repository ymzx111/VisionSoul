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
from app.core.video_policy import MAX_SAMPLES, PIPELINE_VERSION, LEGACY_PIPELINE, LEGACY_HALF_PIPELINE
from app.core.persona import PERSONA_CORE, load_persona
from app.services.deepseek import DeepSeekClient, DeepSeekError, ModelReply, TokenUsage, has_api_key
from app.services.video_comparison import Comparison, DETAIL_VERSION, compact_times
from app.services.video_selection import select_from_directory
from app.services.video_details import detail_path
from app.services.audio_evidence import AudioTranscript, AudioLink, AUDIO_VERSION, MAX_AUDIO_LINKS, audio_prompt, validate_links, normalize_model_links, add_time_citations, current_context
from app.services.audio_corrections import read_audio, save_correction, CorrectionError
from app.services.sound_clues import measure_sound, sound_prompt
from app.services.transcript_quality import transcript_warnings
from app.services.video_debug import CodeDebugReport, debug_reply
from app.services.video_tasks import task_prompt

logger = logging.getLogger(__name__)
VIDEO_TASK = (Path(__file__).resolve().parents[1] / "prompts" / "video.md").read_text(encoding="utf-8").strip()
VIDEO_PROMPT = PERSONA_CORE + "\n\n" + VIDEO_TASK
COMPARISON_TASK = (Path(__file__).resolve().parents[1] / "prompts" / "video_comparison.md").read_text(encoding="utf-8").strip()
AUDIO_TASK = (Path(__file__).resolve().parents[1] / "prompts" / "audio.md").read_text(encoding="utf-8").strip()
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
    index: int = Field(ge=0, le=MAX_SAMPLES-1, title="采样编号")
    time_seconds: float = Field(ge=0, le=30, title="采样时间")
    seen_at: list[float] = Field(min_length=1, max_length=MAX_SAMPLES, title="相同画面的采样时间")
    url: str = Field(title="截图地址")
    clarity: float | None = Field(default=None, ge=0, le=255, title="本地清晰度排序值")
    detail_url: str | None = Field(default=None, title="较清晰的采样图地址")


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    frame_index: int = Field(ge=0, le=MAX_SAMPLES-1, title="依据采样编号")
    keywords: list[str] = Field(min_length=1, max_length=4, title="主要事物")
    description: str = Field(min_length=1, max_length=120, title="画面描述")
    uncertain: bool = Field(title="是否待确认")


class VideoEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    observations: list[Observation] = Field(min_length=1, max_length=8, title="观察记录")
    summary: str = Field(min_length=1, max_length=240, title="视频概述")
    comparison: Comparison | None = Field(default=None, title="多视角辨识与候选比对")
    audio_links: list[AudioLink] = Field(default_factory=list, max_length=MAX_AUDIO_LINKS, title="音画依据关系（模型判断）")
    evidence_notes: list[str] = Field(default_factory=list, max_length=4, title="依据处理说明")
    code_debug: CodeDebugReport | None = None


class VideoContent(VideoEvidence):
    reply: str = Field(min_length=1, max_length=240, title="小灵回应")


class VideoAnswer(VideoContent):
    reply: str = Field(min_length=1, max_length=6000, title="回应、排错建议及按需附加的原始时间依据")
    video_id: str = Field(title="视频编号")
    model: str = Field(title="使用模型")
    usage: TokenUsage | None = Field(default=None, title="原分析用量")
    cached: bool = Field(default=False, title="是否复用结果")
    audio_revision: int = Field(default=0, ge=0)


class VideoInfo(BaseModel):
    video_id: str = Field(title="视频编号")
    duration_seconds: float = Field(gt=0, le=30, title="视频时长")
    width: int = Field(gt=0, title="画面宽度")
    height: int = Field(gt=0, title="画面高度")
    sample_interval_seconds: float = Field(ge=0, title="目标采样间隔")
    sample_count: int = Field(ge=1, le=MAX_SAMPLES, title="采样次数")
    frames: list[SampleFrame] = Field(min_length=1, max_length=MAX_SAMPLES, title="去重后的画面")
    sampling_policy: str = Field(default="one_second",title="采样策略")
    audio: AudioTranscript = Field(default_factory=AudioTranscript, title="本机对白转写")
    audio_revision: int = Field(default=0, ge=0)
    audio_warnings: list[dict] = Field(default_factory=list, max_length=64)
    video_url: str = Field(title="播放地址")
    sound_clues: dict | None = None
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
    def __init__(self, settings: Settings, client: DeepSeekClient | None = None, *, system_prompt: str | None = None, isolated: bool = False):
        self.settings = settings
        self.client = client or DeepSeekClient(settings)
        self.prompt = (load_persona(settings)[0] if system_prompt is None else system_prompt) + "\n\n" + VIDEO_TASK
        self.isolated = isolated

    def audio_record(self, video_id):
        try:
            directory = video_directory(video_id, self.settings)
            record = read_audio(directory)
            record["sound_clues"] = measure_sound(directory, record["audio"])
            record['audio_warnings'] = transcript_warnings(record['audio'])
            return record
        except VideoError:
            raise
        except CorrectionError as exc:
            raise VideoError(str(exc), exc.status_code) from exc
        except ValueError as exc:
            raise VideoError("本机对白或校对记录无法读取，未重新识别。", 500) from exc

    def correct_audio(self, video_id, expected_revision, segments):
        if not UPLOAD_LOCK.acquire(blocking=False):
            raise VideoError("正在处理视频，请稍后保存校对。", 409)
        try:
            if not ANALYSIS_LOCK.acquire(blocking=False):
                raise VideoError("正在分析视频，请等待完成后再保存校对。", 409)
            try:
                directory = video_directory(video_id, self.settings)
                duration = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["duration_seconds"]
                return save_correction(directory, expected_revision, segments, duration)
            except VideoError:
                raise
            except CorrectionError as exc:
                raise VideoError(str(exc), exc.status_code) from exc
            except ValueError as exc:
                raise VideoError("校对内容或时间顺序无效，未保存。", 400) from exc
            finally:
                ANALYSIS_LOCK.release()
        finally:
            UPLOAD_LOCK.release()

    def _prepare_audio(self, directory: Path):
        transcript = AudioTranscript(status="unavailable", message="本机音频识别已关闭，仅分析画面。")
        if self.settings.audio_enabled:
            command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("audio_extract.py")),
                       str(directory), str(self.settings.audio_model_dir.resolve()), str(self.settings.audio_cpu_threads)]
            try:
                process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=self.settings.audio_timeout_seconds,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if process.returncode == 0 and (directory / "audio.json").is_file():
                    AudioTranscript.model_validate_json((directory / "audio.json").read_text(encoding="utf-8"))
                    return
                transcript = AudioTranscript(status="failed", message="本机对白处理失败，仅分析画面；重新上传可重试。")
            except subprocess.TimeoutExpired:
                transcript = AudioTranscript(status="failed", message="本机对白处理超时，仅分析画面；重新上传可重试。")
        temporary = directory / ".audio-status.json"
        temporary.write_text(transcript.model_dump_json(), encoding="utf-8")
        os.replace(temporary, directory / "audio.json")

    def _detail_frames(self, info: VideoInfo, directory: Path) -> dict[int, Path]:
        selected = select_from_directory(info.frames, directory)
        paths = {index: directory / f"detail-{index}.jpg" for index in selected}
        missing = [frame for frame in info.frames if frame.index in paths and not detail_path(directory, frame.index).is_file()]
        if missing and (directory / "source.mp4").is_file():
            # 一个隔离子进程单遍读取，只保存选中帧；普通观察、上传和恢复不触发。
            with upload_workspace(directory) as workspace:
                (workspace / "targets.json").write_text(json.dumps([
                    {"index": frame.index, "time_seconds": frame.time_seconds} for frame in missing
                ]), encoding="utf-8")
                command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("video_details.py")),
                           str(directory), str(workspace)]
                try:
                    process = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, timeout=self.settings.video_decode_timeout_seconds,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if process.returncode == 0 and all(detail_path(workspace, frame.index).is_file() for frame in missing):
                        for frame in missing:
                            os.replace(detail_path(workspace, frame.index), detail_path(directory, frame.index))
                    else:
                        logger.warning("原视频高清提取未完成，本次使用已有细节图")
                except subprocess.TimeoutExpired:
                    logger.warning("原视频高清提取超时，本次使用已有细节图")
        return {index: detail_path(directory, index) if detail_path(directory, index).is_file() else path
                for index, path in paths.items()}

    def _observe(self, info: VideoInfo, caption: str = "", history: list[dict] | None = None, memory_text: str = "", inspect: bool = False, debug_code: bool = False) -> VideoAnswer:
        directory = video_directory(info.video_id, self.settings)
        parts = [{"type": "text", "text": "图号@原视频秒，区间是重复图的采样点，不代表连续动作。"}]
        detailed = self._detail_frames(info, directory) if inspect or debug_code else {}
        for frame in info.frames:
            high = frame.index in detailed
            parts.append({"type": "text", "text": f"{frame.index}@{compact_times(frame.seen_at, info.sample_interval_seconds)}"})
            path = detailed[frame.index] if high else directory / f"frame-{frame.index}.jpg"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded, "detail": "high" if high else "low"}})
        prompt = self.prompt
        parts.append({"type": "text", "text": audio_prompt(info.audio, info.frames)})
        if info.sound_clues:
            parts.append({"type": "text", "text": sound_prompt(info.sound_clues)})
        prompt = task_prompt(prompt, audio_task=AUDIO_TASK if info.audio.status == 'ready' else '',
                             comparison_task=COMPARISON_TASK if inspect and not debug_code else '', debug_code=debug_code)
        if caption:
            parts.append({"type": "text", "text": caption})
        if inspect or debug_code:
            if not detailed:
                prompt += "\n此视频只有旧版概览图，没有额外细节图；不要声称看到了叶脉、小字等缺失细节。必要时提示重新上传原视频。"
        reply = self.client.complete([{"role": "system", "content": prompt},
            *([{"role":"user","content":memory_text}] if memory_text else []), *(history or []),
            {"role": "user", "content": parts}], json_mode=True, max_tokens=self.settings.video_max_tokens)
        try:
            if reply.truncated:
                raise ValueError("truncated")
            content = VideoContent.model_validate_json(reply.answer)
            content.audio_links, content.evidence_notes = normalize_model_links(content.audio_links, info.audio, info.frames)
            if transcript_warnings(info.audio):
                content.evidence_notes.append('部分自动转写待校对，分析已遮蔽异常文字；可回听并校对白。')
            if len(content.audio_links) > 4:
                content.evidence_notes.append(f"已保存{len(content.audio_links)}条音画依据，页面默认展示前4条，其余可展开。")
            if debug_code and content.code_debug is None:
                raise ValueError('missing code debug report')
            if inspect and not debug_code and content.comparison is None:
                raise ValueError("missing comparison")
            if content.comparison:
                content.comparison.check_references({frame.index for frame in info.frames})
            if content.code_debug:
                content.code_debug.check_references({frame.index for frame in info.frames})
            indices = [item.frame_index for item in content.observations]
            # 一帧可以有主体、背景等多条不同观察；只拒绝不存在的图号。
            if any(index >= len(info.frames) for index in indices):
                raise ValueError("unknown frame")
            for observation in content.observations:
                if any(not keyword.strip() or len(keyword) > 24 for keyword in observation.keywords):
                    raise ValueError("invalid keyword")
            content.observations.sort(key=lambda item: item.frame_index)
            merged = []
            for observation in content.observations:
                signature = (set(observation.keywords), observation.description, observation.uncertain)
                if merged and signature == (set(merged[-1].keywords), merged[-1].description, merged[-1].uncertain):
                    continue
                merged.append(observation)
            content.observations = merged
        except ValueError as exc:
            raise DeepSeekError("模型返回的结构化结果不完整，本次可能已有用量；不会自动重试。", 502) from exc
        payload = content.model_dump()
        payload["reply"] = add_time_citations(debug_reply(content.code_debug, info.frames) if debug_code else content.reply, caption, info.audio, content.audio_links)
        return VideoAnswer(**payload, video_id=info.video_id, model=reply.model, usage=reply.usage, audio_revision=info.audio_revision)

    @staticmethod
    def _snapshot(info: VideoInfo, result: VideoEvidence) -> dict:
        frames = {frame.index: frame for frame in info.frames}
        return {"video_id": info.video_id, "duration_seconds": info.duration_seconds, "summary": result.summary,
                "observations": [{**row.model_dump(), "time_seconds": frames[row.frame_index].time_seconds}
                                 for row in result.observations],
                "comparison": result.comparison.model_dump() if result.comparison else None,
                "audio": info.audio.model_dump(), "audio_links": [row.model_dump() for row in result.audio_links],
                "evidence_notes": result.evidence_notes,
                "code_debug": result.code_debug.model_dump() if result.code_debug else None,
                "audio_warnings": transcript_warnings(info.audio),
                "analysis_audio_revision": info.audio_revision, "analysis_stale": False, "sound_clues": info.sound_clues}

    def inspect(self, video_id: str, message: str, history: list[dict], memory_text: str = "", *, debug_code: bool = False):
        """显式重新读画面，一次请求完成辨识、比对和接话；结果只随当前话题保存。"""
        if not ANALYSIS_LOCK.acquire(blocking=False):
            raise VideoError("已有视频正在观察，请稍等后再发送。",409)
        try:
            info = self.info(video_id)
            answer = self._observe(info,message,history,memory_text,inspect=not debug_code,debug_code=debug_code)
            return self._snapshot(info,answer), ModelReply(answer=answer.reply,model=answer.model,usage=answer.usage)
        finally:
            ANALYSIS_LOCK.release()

    def start_conversation(self, video_id: str, message: str, history: list[dict], memory_text: str = "") -> tuple[dict, ModelReply | None]:
        """首次无观察时一次请求同时看图和接话；已有观察时让聊天服务仅用文字接话。"""
        info = self.info(video_id)
        if self.isolated:
            # 新空间的分析只随当前话题保存，不跨话题复用可能混入配文/角色记忆的综合观察。
            if not ANALYSIS_LOCK.acquire(blocking=False):
                raise VideoError("已有视频正在观察，请稍等后再发送。", 409)
            try:
                answer = self._observe(info, message, history, memory_text)
                return self._snapshot(info, answer), ModelReply(answer=answer.reply, model=answer.model, usage=answer.usage)
            finally:
                ANALYSIS_LOCK.release()
        directory = video_directory(video_id, self.settings)
        evidence_path = self._cache_path(directory).with_suffix(".evidence.json")

        def available():
            cached = self._cached(directory)
            if cached:
                return self._snapshot(info, cached)
            if evidence_path.is_file():
                try:
                    content = VideoEvidence.model_validate_json(evidence_path.read_text(encoding="utf-8"))
                    if any(row.frame_index >= len(info.frames) for row in content.observations):
                        raise ValueError("unknown frame")
                    validate_links(content.audio_links, info.audio, info.frames)
                    return self._snapshot(info, content)
                except ValueError as exc:
                    raise VideoError("保存的视频观察无法读取，不会自动重新付费分析。", 500) from exc
            return None

        snapshot = available()
        if snapshot:
            return snapshot, None
        # 校对后新建话题也不悄悄重读图片：先沿用旧视觉观察并标旧综合报告。
        if info.audio_revision:
            prior_paths = sorted(directory.glob("analysis-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
            for path in prior_paths[:32]:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    old_revision = payload.get("audio_revision", 0)
                    if old_revision >= info.audio_revision:
                        continue
                    evidence = VideoEvidence.model_validate({key: payload[key] for key in ("observations", "summary", "comparison") if key in payload})
                    if any(row.frame_index >= len(info.frames) for row in evidence.observations):
                        continue
                    snapshot = self._snapshot(info, evidence)
                    snapshot["analysis_audio_revision"] = old_revision
                    return current_context(snapshot, info.audio, info.audio_revision, info.sound_clues), None
                except (ValueError, KeyError):
                    continue
            if prior_paths:
                raise VideoError("旧观察暂不可复用，请检查本机记录；不会自动重读图片。", 500)
        if not ANALYSIS_LOCK.acquire(blocking=False):
            raise VideoError("已有视频正在观察，请稍等后再发送。", 409)
        try:
            info = self.info(video_id)
            # 等待请求锁前若其他话题已完成，只取事实，不复用对另一个用户消息的回应。
            snapshot = available()
            if snapshot:
                return snapshot, None
            answer = self._observe(info,message,history,memory_text)
            snapshot = self._snapshot(info, answer)
            # 只缓存视觉事实，不把针对这一条消息的个性回应带进其他话题。
            evidence = {"observations": [row.model_dump() for row in answer.observations],
                        "summary": answer.summary, "audio_links": [row.model_dump() for row in answer.audio_links],
                        "evidence_notes": answer.evidence_notes}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".evidence-", delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(evidence, output, ensure_ascii=False)
                os.replace(temporary, evidence_path)
            except OSError:
                # 已完成模型回复优先入聊天事务；附加事实缓存失败不能诱导再次付费。
                logger.warning("视频观察复用缓存暂未保存；本轮仍由聊天记录保存")
            finally:
                if temporary:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("视频观察临时缓存待后续清理")
            return snapshot, ModelReply(answer=answer.reply, model=answer.model, usage=answer.usage)
        finally:
            ANALYSIS_LOCK.release()

    def _cache_path(self, directory: Path) -> Path:
        # 密钥不参与指纹；提示/输出预算/抽帧记录变化时不会误用旧结果。
        manifest = (directory / "manifest.json").read_text(encoding="utf-8")
        pipeline = json.loads(manifest).get("pipeline_version", PIPELINE_VERSION)
        # 旧采样缓存沿用旧指纹；新音频缓存内容及任务提示参与新版本指纹。
        audio = ((directory / "audio.json").read_text(encoding="utf-8") + AUDIO_TASK) if pipeline == PIPELINE_VERSION and (directory / "audio.json").is_file() else ""
        if (directory / "audio-corrections.json").is_file():
            audio += (directory / "audio-corrections.json").read_text(encoding="utf-8")
        fingerprint = hashlib.sha256((pipeline + self.prompt + self.settings.deepseek_model
            + str(self.settings.video_max_tokens) + manifest + audio).encode()).hexdigest()[:24]
        return directory / f"analysis-{fingerprint}.json"

    def _cached(self, directory: Path) -> VideoAnswer | None:
        path = self._cache_path(directory)
        if not path.is_file():
            return None
        try:
            answer = VideoAnswer.model_validate_json(path.read_text(encoding="utf-8"))
            manifest=json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
            transcript = read_audio(directory)["audio"]
            validate_links(answer.audio_links, transcript, [SampleFrame(**{**frame, "url": ""}) for frame in manifest["frames"]])
            if answer.comparison:
                answer.comparison.check_references({frame['index'] for frame in manifest['frames']})
            if answer.code_debug:
                answer.code_debug.check_references({frame['index'] for frame in manifest['frames']})
            return answer.model_copy(update={"cached": True})
        except (ValueError, OSError) as exc:
            # 缓存损坏时不静默重新付费，要求人工处理。
            raise VideoError("已保存的分析结果无法读取，请检查本机存储。", 500) from exc

    def info(self, video_id: str) -> VideoInfo:
        directory = video_directory(video_id, self.settings)
        try:
            data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if data.get("pipeline_version") not in {PIPELINE_VERSION, LEGACY_PIPELINE, LEGACY_HALF_PIPELINE}:
                raise VideoError("采样策略已更新，请重新选择原视频，生成整段均匀采样的画面。旧视频和结果仍保存在本机。", 409)
            data["sampling_policy"] = {PIPELINE_VERSION: "one_second", LEGACY_HALF_PIPELINE: "half_second"}.get(data["pipeline_version"], "legacy_uniform")
            record = self.audio_record(video_id)
            data["audio"] = record["audio"]
            data["audio_revision"] = record["revision"]
            data["sound_clues"] = record["sound_clues"]
            data['audio_warnings'] = record['audio_warnings']
            for frame in data["frames"]:
                frame["url"] = f"/api/videos/{video_id}/frames/{frame['index']}"
                frame["detail_url"] = f"/api/videos/{video_id}/frames/{frame['index']}/detail" if (directory / f"detail-{frame['index']}.jpg").is_file() else None
            return VideoInfo(**data, video_id=video_id, video_url=f"/api/videos/{video_id}/file", result=None if self.isolated else self._cached(directory))
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
                digest = hashlib.sha256((PIPELINE_VERSION + DETAIL_VERSION + AUDIO_VERSION + str(self.settings.audio_enabled) + str(self.settings.audio_model_dir.resolve()) + str(self.settings.video_max_frames)
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
                    audio = self.info(video_id).audio
                    if audio.status in {"failed", "unavailable"} and self.settings.audio_enabled:
                        self._prepare_audio(target)
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
                self._prepare_audio(directory)
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
            answer = self._observe(self.info(video_id))
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

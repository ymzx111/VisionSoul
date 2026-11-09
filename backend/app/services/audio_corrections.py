"""人工校对独立存储；原始audio.json只读，修订有乐观并发检查。"""
from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile

from pydantic import BaseModel, ConfigDict, Field
from app.services.audio_evidence import AudioTranscript, SpeechSegment


class CorrectionError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class CorrectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    corrected_at: str
    transcript: AudioTranscript


def read_audio(directory):
    raw_path = directory / "audio.json"
    raw = AudioTranscript.model_validate_json(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else AudioTranscript()
    path = directory / "audio-corrections.json"
    if not path.is_file():
        return {"original": raw, "audio": raw, "revision": 0}
    record = CorrectionRecord.model_validate_json(path.read_text(encoding="utf-8"))
    if not raw_path.is_file() or record.raw_sha256 != hashlib.sha256(raw_path.read_bytes()).hexdigest():
        raise CorrectionError("原始转写已变化，现有校对需人工检查，未覆盖任何记录。", 409)
    return {"original": raw, "audio": record.transcript, "revision": record.revision}


def save_correction(directory, expected_revision: int, segments: list[SpeechSegment], duration: float):
    current = read_audio(directory)
    if current["revision"] != expected_revision:
        raise CorrectionError("对白已在其他页面修改，请重新打开校对后再保存。", 409)
    if current["original"].status != "ready":
        raise CorrectionError("当前没有可校对的对白，请先上传带讲话的视频。")
    if len(segments) != len(current["original"].segments):
        raise CorrectionError("首版保留原分段数量，请只修改文字和时间。")
    if any(segment.end > duration or segment.start >= segment.end for segment in segments):
        raise CorrectionError("对白时间必须位于视频内，结束时间须晚于开始时间。")
    corrected = AudioTranscript(status="ready", source="corrected", language=current["original"].language,
        message="用户校对白；仍需结合画面核对。", segments=segments)
    if corrected.segments == current["audio"].segments:
        return current
    record = CorrectionRecord(revision=current["revision"] + 1,
        raw_sha256=hashlib.sha256((directory / "audio.json").read_bytes()).hexdigest(),
        corrected_at=datetime.now(timezone.utc).isoformat(), transcript=corrected)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".audio-edit-", delete=False) as output:
            temporary = output.name
            output.write(record.model_dump_json())
        os.replace(temporary, directory / "audio-corrections.json")
    finally:
        if temporary and os.path.isfile(temporary):
            os.unlink(temporary)
    return {"original": current["original"], "audio": corrected, "revision": record.revision}

"""有来源的对白与音画候选。时间接近不是语义验证，模型关系单独校验。"""
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator

AUDIO_VERSION = "whisper-small-int8-timeline-v1"
PRIMARY_SECONDS = 1.5
SECONDARY_SECONDS = 3.0


class SpeechSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: int = Field(ge=0, le=63)
    start: float = Field(ge=0, le=30)
    end: float = Field(ge=0, le=30)
    text: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start or not self.text.strip():
            raise ValueError("对白时间或内容无效")
        return self


class AudioTranscript(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: str = AUDIO_VERSION
    status: Literal["ready", "no_audio", "no_speech", "unavailable", "failed", "legacy"] = "legacy"
    message: str = "旧视频尚未处理音频，重新上传可启用。"
    language: str | None = None
    segments: list[SpeechSegment] = Field(default_factory=list, max_length=64)
    source: Literal["automatic", "corrected"] = "automatic"

    @model_validator(mode="after")
    def coherent(self):
        if bool(self.segments) != (self.status == "ready"):
            raise ValueError("对白状态与内容不一致")
        if [segment.id for segment in self.segments] != list(range(len(self.segments))):
            raise ValueError("对白编号无效")
        if any(b.start < a.start for a, b in zip(self.segments, self.segments[1:])):
            raise ValueError("对白时间顺序无效")
        if sum(len(segment.text) for segment in self.segments) > 6000:
            raise ValueError("对白内容过长")
        return self


class AudioLink(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    segment_id: int = Field(ge=0, le=63)
    frame_index: int | None = Field(default=None, ge=0, le=60)
    frame_time: float | None = Field(default=None, ge=0, le=30)
    relation: Literal["supports", "contradicts", "uncertain", "audio_only"]
    detail: str = Field(min_length=1, max_length=180)


def frame_candidates(segment: SpeechSegment, frames):
    candidates = []
    for frame in frames:
        # 精确去重的同一图片可能在很晚再次出现，必须检查全部出现时间。
        for timestamp in frame.seen_at:
            gap = max(segment.start - timestamp, timestamp - segment.end, 0)
            if gap <= SECONDARY_SECONDS:
                candidates.append({"frame_index": frame.index, "frame_time": timestamp,
                    "window": "primary" if gap <= PRIMARY_SECONDS else "secondary", "gap": round(gap, 3)})
    midpoint = (segment.start + segment.end) / 2
    candidates.sort(key=lambda row: (row["gap"], abs(row["frame_time"] - midpoint), row["frame_time"]))
    return ([row for row in candidates if row["window"] == "primary"][:8]
            + [row for row in candidates if row["window"] == "secondary"][:4])


def validate_links(links, transcript: AudioTranscript, frames):
    if transcript.status == "ready" and not links:
        raise ValueError("缺少对白依据")
    for link in links:
        if link.segment_id >= len(transcript.segments):
            raise ValueError("引用了不存在的对白")
        if link.relation == "audio_only":
            if link.frame_index is not None or link.frame_time is not None:
                raise ValueError("纯音频依据不能伪造画面")
        elif not any(row["frame_index"] == link.frame_index and row["frame_time"] == link.frame_time
                     for row in frame_candidates(transcript.segments[link.segment_id], frames)):
            raise ValueError("引用画面不在音画候选窗口内")


def audio_prompt(transcript: AudioTranscript, frames):
    if transcript.status != "ready":
        return "音频状态：" + transcript.message + " 不得编造对白或声音情绪。"
    rows = []
    for segment in transcript.segments:
        candidates = frame_candidates(segment, frames)
        labels = lambda window: ",".join(f"{row['frame_index']}@{row['frame_time']:g}" for row in candidates if row["window"] == window)
        rows.append(f"S{segment.id}@{segment.start:g}-{segment.end:g} {segment.text}\n近:{labels('primary')}；补:{labels('secondary')}")
    label = "用户校对白（优先于旧转写与历史回复，仍需按画面核对）" if transcript.source == "corrected" else "自动转写（可能有误）"
    return label + "（原视频秒数；近±1.5秒，补±3秒，仅为时间候选）：\n" + "\n".join(rows)


def add_time_citations(reply: str, question: str, transcript: AudioTranscript, links) -> str:
    """用户主动索要依据时由代码附原始引用，避免只靠提示词要求模型报时间。"""
    if transcript.status != "ready" or not any(word in question.lower() for word in
            ("为什么", "依据", "论证", "几秒", "什么时候", "什么时间", "时间点", "说了什么", "怎么判断", "如何判断", "哪一秒", "哪段", "回听", "回看", "evidence", "timestamp")):
        return reply
    link_map = {}
    for link in links:
        link_map.setdefault(link.segment_id, link)
    times = [float(value) for value in re.findall(r"(\d+(?:\.\d+)?)\s*秒", question)]
    # 简单文字重合只用于引用排序，不自动确认语义；最多附两段，保持简洁。
    def rank(segment):
        distance = min(max(segment.start - timestamp, timestamp - segment.end, 0) for timestamp in times) if times else 0
        overlap = len(set(segment.text) & set(question))
        return (distance, segment.id not in link_map, -overlap, segment.start)
    references = []
    labels = {"supports": "模型判断相符", "contradicts": "模型判断不一致", "uncertain": "关联待确认"}
    for segment in sorted(transcript.segments, key=rank)[:2]:
        excerpt = segment.text[:60] + ("…" if len(segment.text) > 60 else "")
        line = f"{segment.start:g}—{segment.end:g}秒：“{excerpt}”"
        link = link_map.get(segment.id)
        if link and link.frame_time is not None:
            line += f"；画面{link.frame_time:g}秒（{labels[link.relation]}）"
        references.append(line)
    label = "用户校对白" if transcript.source == "corrected" else "自动转写，可能有误"
    return reply + f"\n\n时间依据（{label}）：\n" + "\n".join(references)


def current_context(context, audio, revision, sound_clues=None):
    """保留原分析快照；新校对单独附加，读取操作不改写历史。"""
    if context is None:
        return None
    result = dict(context)
    stale = context.get("analysis_audio_revision", 0) != revision
    result["analysis_stale"] = stale
    if stale:
        result["current_audio"] = audio.model_dump()
        result["current_audio_revision"] = revision
        result["current_sound_clues"] = sound_clues
    else:
        result.pop("current_audio", None)
        result.pop("current_audio_revision", None)
        result.pop("current_sound_clues", None)
        result["sound_clues"] = sound_clues
    return result


def model_context(context):
    """旧综合报告可能建立在错字上；模型只读取最新校对和视觉观察。"""
    result = dict(context)
    if result.get("analysis_stale"):
        result["audio"] = result.pop("current_audio")
        result["sound_clues"] = result.pop("current_sound_clues", None)
        result["audio_links"] = []
        result["summary"] = "旧综合报告基于旧对白，待用户主动重新分析；不得沿用其推断。"
        result["comparison"] = None
    return result

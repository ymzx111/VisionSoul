"""有来源的对白与音画候选。时间接近不是语义验证，模型关系单独校验。"""
from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.video_policy import MAX_EVIDENCE_FRAMES
from app.services.transcript_quality import transcript_warnings, trusted_transcript, uncertain_words

AUDIO_VERSION = "whisper-small-int8-timeline-v4"
PRIMARY_SECONDS = 1.5
SECONDARY_SECONDS = 3.0
MAX_AUDIO_LINKS = 64


class AudioCandidateWindowError(ValueError):
    """对白存在，但模型提供的画面与时间无法绑定到本轮候选。"""


class SpeechWord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=80)
    start: float = Field(ge=0, le=30)
    end: float = Field(ge=0, le=30)
    score: float = Field(ge=0, le=1)


class SpeechRetry(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0, le=30)
    end: float = Field(ge=0, le=30)
    text: str = Field(min_length=1, max_length=180)


class SpeechSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: int = Field(ge=0, le=63)
    start: float = Field(ge=0, le=30)
    end: float = Field(ge=0, le=30)
    text: str = Field(min_length=1, max_length=300)
    words: list[SpeechWord] = Field(default_factory=list, max_length=128)
    retry: SpeechRetry | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start or not self.text.strip():
            raise ValueError("对白时间或内容无效")
        if any(not self.start <= word.start <= word.end <= self.end for word in self.words):
            raise ValueError("词时间超出对白范围")
        if self.retry and not (self.retry.start <= self.start <= self.end <= self.retry.end):
            raise ValueError("复识别窗口没有覆盖原对白")
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
    candidate_id: int | None = Field(default=None, ge=0, le=11)
    frame_index: int | None = Field(default=None, ge=0, lt=MAX_EVIDENCE_FRAMES)
    frame_time: float | None = Field(default=None, ge=0, le=30)
    relation: Literal["supports", "contradicts", "uncertain", "audio_only"]
    detail: str = Field(min_length=1, max_length=180)
    content_visible: bool = False


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
    selected = ([row for row in candidates if row["window"] == "primary"][:8]
                + [row for row in candidates if row["window"] == "secondary"][:4])
    return [{**row, "candidate_id": index} for index, row in enumerate(selected)]


def validate_links(links, transcript: AudioTranscript, frames):
    if len(links) > MAX_AUDIO_LINKS:
        raise ValueError("音画依据数量过多")
    if transcript.status == "ready" and not links:
        raise ValueError("缺少对白依据")
    for link in links:
        if link.segment_id >= len(transcript.segments):
            raise ValueError("引用了不存在的对白")
        if link.relation == "audio_only":
            if link.frame_index is not None or link.frame_time is not None or link.candidate_id is not None:
                raise ValueError("纯音频依据不能伪造画面")
        else:
            candidates = frame_candidates(transcript.segments[link.segment_id], frames)
            if link.candidate_id is not None:
                if link.candidate_id >= len(candidates):
                    raise ValueError("引用了不存在的时间候选")
                chosen = candidates[link.candidate_id]
                if ((link.frame_index is not None and link.frame_index != chosen["frame_index"])
                        or (link.frame_time is not None and link.frame_time != chosen["frame_time"])):
                    raise ValueError("候选编号与附带图号或时间冲突")
                link.frame_index, link.frame_time = chosen["frame_index"], chosen["frame_time"]
            if not any(row["frame_index"] == link.frame_index and row["frame_time"] == link.frame_time for row in candidates):
                raise AudioCandidateWindowError("引用画面不在音画候选窗口内")


def normalize_model_links(links, transcript: AudioTranscript, frames):
    """仅在新模型回复入口保守清理；缓存读取仍严格校验，不修改原始对白。"""
    if not links or len(links) > MAX_AUDIO_LINKS:
        validate_links(links, transcript, frames)
    normalized = []
    cleared = 0
    unmatched = 0
    for original in links:
        link = original.model_copy(deep=True)
        extra_picture = link.relation == "audio_only" and any(
            value is not None for value in (link.candidate_id, link.frame_index, link.frame_time))
        probe = link.model_copy(update={"relation": "uncertain"}) if extra_picture else link
        try:
            validate_links([probe], transcript, frames)
        except AudioCandidateWindowError:
            # 原对白编号已通过校验。丢掉无法核对的图号、时间和关联理由。
            link = AudioLink(segment_id=link.segment_id, relation="audio_only",
                             detail="画面时间未匹配该段对白。请回听原视频核对转写。")
            unmatched += 1
        else:
            if extra_picture:
                link.candidate_id = link.frame_index = link.frame_time = None
                link.content_visible = False
                cleared += 1
        normalized.append(link)
    untrusted = {row['segment_id'] for row in transcript_warnings(transcript)}
    downgraded = 0
    for link in normalized:
        if link.relation == 'supports' and (not link.content_visible or link.segment_id in untrusted
                or any(term in link.detail for term in ('无法验证', '无法证实', '无法确认', '不能验证', '不能证实', '无法由画面'))):
            link.relation = 'uncertain'
            downgraded += 1
    picture_notes = []
    if cleared:
        picture_notes.append(f"{cleared}条仅对白依据已去除多余画面绑定，未建立画面验证关系。")
    if unmatched:
        picture_notes.append(f"{unmatched}条画面引用未匹配对白的时间候选，已仅保留对白依据。")
    notes = ["".join(picture_notes)] if picture_notes else []
    if downgraded:
        notes.append(f'{downgraded}条支持关系缺少可靠内容依据，已标为关联待确认。')
    return normalized, notes


def audio_prompt(transcript: AudioTranscript, frames):
    if transcript.status != "ready":
        return "音频状态：" + transcript.message + " 不得编造对白或声音情绪。"
    rows = []
    warnings = transcript_warnings(transcript)
    for segment in trusted_transcript(transcript, warnings).segments:
        candidates = frame_candidates(segment, frames)
        labels = lambda window: ",".join(f"C{row['candidate_id']}={row['frame_index']}@{row['frame_time']:g}" for row in candidates if row["window"] == window)
        row = f"S{segment.id}@{segment.start:g}-{segment.end:g} {segment.text}"
        uncertain = uncertain_words(segment)
        if uncertain:
            row += "［低分：" + "、".join(f"{word.text}@{word.start:g}" for word in uncertain) + "］"
        if segment.retry:
            retry = segment.retry
            row += f"［同音轨复识别候选 {retry.start:g}–{retry.end:g}秒：{retry.text}］"
        rows.append(f"{row}\n近:{labels('primary')}；补:{labels('secondary')}")
    label = "用户校对白（优先于旧转写与历史回复，仍需按画面核对）" if transcript.source == "corrected" else "自动转写（可能有误）"
    warning = '部分分段存在重复/时间密度异常，已遮蔽待校对文字；不得猜测原文。\n' if warnings else ''
    return warning + label + "（原视频秒数；近±1.5秒，补±3秒，仅为时间候选）：\n" + "\n".join(rows)


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
    unreliable = {row['segment_id'] for row in transcript_warnings(transcript)}
    for segment in sorted(transcript.segments, key=rank)[:2]:
        if segment.id in unreliable:
            references.append(f'{segment.start:g}—{segment.end:g}秒：自动转写待校对，请回听原声，不引用为内容依据。')
            continue
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
        result['code_debug'] = None
    if result.get('audio'):
        audio = AudioTranscript.model_validate(result['audio'])
        warnings = transcript_warnings(audio)
        safe = trusted_transcript(audio, warnings)
        result['audio'] = safe.model_dump(exclude={'segments': {'__all__': {'words'}}}, exclude_none=True)
        for row, segment in zip(result['audio']['segments'], safe.segments):
            words = uncertain_words(segment)
            if words:
                row['low_score_words'] = [word.model_dump(exclude={'score'}) for word in words]
        result['audio_warnings'] = warnings
        if warnings:
            # 旧概述/诊断可能建立在异常转写上，保留视觉事实，推断暂不提供给模型。
            result['summary'] = '部分对白转写待校对，仅参考视觉观察与用户当前说明。'
            result['comparison'] = None
            bad_ids = {row['segment_id'] for row in warnings}
            result['audio_links'] = [row for row in result.get('audio_links', []) if row['segment_id'] not in bad_ids]
    return result

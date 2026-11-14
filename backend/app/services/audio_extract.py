"""受限子进程：音频重采样到视频共同时间轴，再离线转写；不访问API。"""
import json
import math
import os
from pathlib import Path
import re
import sys
import unicodedata
import wave

import av
import numpy as np

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.services.audio_evidence import AudioTranscript, SpeechRetry, SpeechSegment, SpeechWord
from app.services.transcript_quality import LOW_WORD_SCORE, transcript_warnings

RATE = 16000
TASK_CUES = re.compile(r"帮我|麻烦|请(?:你|帮|问|看|分析|写)|想让|要求|任务|题目|这(?:一)?道题|代码|程序|报错|怎么|多少|"
                       r"第\s*\d+\s*题|\d+(?:\.\d+)?\s*(?:秒|分钟|行|个|元|%|％)")
CODE_CUES = re.compile(r"代码|程序|编程|报错|题干|流程图")
LANGUAGE_SLOT = re.compile(
    r"(?:用|采用)\s*(?P<language>JavaScript|Python|Java|Rust|C\+\+|C#|C)\s*"
    r"(?P<suffix>[一-鿿]{2,4}?)(?=\s*(?:来|去)?\s*(?:实现|编写|解决|完成))",
    re.IGNORECASE,
)
LANGUAGE_NAMES = re.compile(
    r"(?<![a-zA-Z0-9_])(?:(?P<cpp>C\s*(?:\+\s*\+|加\s*加|plus\s+plus))|"
    r"(?P<csharp>C\s*(?:#|井号|sharp))|(?P<c>C\s*[语語]言)|"
    r"(?P<other>JavaScript|Python|Java|Rust))(?![a-zA-Z0-9_+#])",
    re.IGNORECASE,
)
BARE_C_REQUEST = re.compile(r"(?:用|采用)\s*C\s*(?=(?:来|去)?\s*(?:实现|编写|写|解决|完成))", re.IGNORECASE)
LANGUAGE_SUFFIXES = ("语言", "程序", "代码", "脚本", "库", "框架", "工具", "接口", "数组", "指针", "结构体", "循环", "函数", "原生", "语法")
CODE_VOCABULARY = "编程任务常用词：代码、补全、C 语言、输入输出、流程图。"


def decode_timeline(source: Path, duration: float):
    """不删除静音、不拼接间隙；音轨延后/提前均映射回视频0秒。"""
    samples = np.zeros(math.ceil(duration * RATE), dtype=np.float32)
    with av.open(str(source), format="mov", options={"protocol_whitelist": "file",
            "enable_drefs": "0", "use_absolute_path": "0"}) as container:
        if len(container.streams.video) != 1 or len(container.streams.audio) > 1:
            raise ValueError("不支持多音轨视频")
        if not container.streams.audio:
            return None
        video = container.streams.video[0]
        origin = float((video.start_time or 0) * video.time_base)
        earliest = -max(2, min(30, origin) + 0.1)
        stream = container.streams.audio[0]
        if stream.codec_context.sample_rate > 192000 or stream.codec_context.channels > 8:
            raise ValueError("音频资源上限异常")
        stream.codec_context.thread_count = 2
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=RATE)
        previous, decoded = -math.inf, 0

        def keep(frame):
            nonlocal previous, decoded
            if frame.time is None:
                raise ValueError("音频没有时间戳")
            timestamp = float(frame.time) - origin
            if not math.isfinite(timestamp) or timestamp < previous or not earliest <= timestamp <= duration + 2:
                raise ValueError("音频时间戳异常")
            previous = timestamp
            values = frame.to_ndarray().reshape(-1)
            decoded += len(values)
            if decoded > RATE * 34 or not np.isfinite(values).all():
                raise ValueError("音频解码数量异常")
            start = round(timestamp * RATE)
            left, right = max(0, start), min(len(samples), start + len(values))
            if right > left:
                samples[left:right] = values[left - start:right - start]

        for count, frame in enumerate(container.decode(stream), 1):
            if count > 10000:
                raise ValueError("音频帧数异常")
            for converted in resampler.resample(frame):
                keep(converted)
        for converted in resampler.resample(None):
            keep(converted)
    return samples


def speech_segments(segments, duration):
    """用词时间戳切成短句，防止整段30秒都成为一个宽泛的对应窗口。"""
    result = []
    for segment in segments:
        if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
            continue
        words = segment.words or []
        chunks, chunk = [], []
        for word in words:
            chunk.append(word)
            if word.end - chunk[0].start >= 4 or word.word.rstrip().endswith(("。", "！", "？", ".", "!", "?", "；")):
                chunks.append(chunk)
                chunk = []
        if chunk:
            chunks.append(chunk)
        ranges = [(group[0].start, group[-1].end, "".join(word.word for word in group), group) for group in chunks]
        if not words:
            ranges = [(segment.start, segment.end, segment.text, [])]
        for start, end, text, group in ranges:
            text = text.strip()
            if not text or end <= 0 or start >= duration:
                continue
            start, end = round(max(0, start), 3), round(min(duration, end), 3)
            scored = []
            for word in group:
                score = getattr(word, "probability", None)
                left, right = round(max(start, word.start), 3), round(min(end, word.end), 3)
                token = word.word.strip()
                if score is not None and 0 <= score <= 1 and left <= right and 0 < len(token) <= 80:
                    scored.append(SpeechWord(text=token, start=left, end=right, score=round(score, 4)))
            result.append(SpeechSegment(id=len(result), start=start, end=end, text=text, words=scored[:128]))
    return result


def retry_priority(segment):
    """低分词在任务线索附近才有复识别优先级。"""
    text = "".join(word.text for word in segment.words)
    cues = list(TASK_CUES.finditer(text))
    offset = 0
    priority = 1.0
    for word in segment.words:
        end = offset + len(word.text)
        token = re.sub(r"[\W_]+", "", word.text)
        if word.score < LOW_WORD_SCORE and token and token not in {"啊", "嗯", "哦", "吧", "呢", "呀", "呃", "哈"}:
            if any(max(cue.start() - end, offset - cue.end()) <= 5 for cue in cues):
                priority = min(priority, word.score)
        offset = end
    return priority


def programming_languages(text):
    """区分会改变任务的名称，保留加号和井号，也识别常见口述写法。"""
    normalized = unicodedata.normalize("NFKC", text)
    aliases = {"cpp": "c++", "csharp": "c#", "c": "c"}
    names = {aliases.get(match.lastgroup, match[0].casefold()) for match in LANGUAGE_NAMES.finditer(normalized)}
    if BARE_C_REQUEST.search(normalized):
        names.add("c")
    return names


def unusual_language(segment, preceding):
    """有明确任务时，复核「用某语言实现」位置的异常后缀，词分数可能仍然很高。"""
    if not TASK_CUES.search(segment.text + preceding):
        return None
    text = unicodedata.normalize("NFKC", segment.text)
    for match in LANGUAGE_SLOT.finditer(text):
        suffix = match["suffix"]
        # 「C 加加」「C 井号」中 C 只是全名的一部分，不能单独作为提示。
        if suffix.endswith(LANGUAGE_SUFFIXES) or programming_languages(match[0]) - {match["language"].casefold()}:
            continue
        return match["language"]
    return None


def retry_uncertain_speech(model, samples, audio):
    """同一视频最多复识别一个窗口，保留初次转写和时间轴。"""
    flagged = {warning["segment_id"] for warning in transcript_warnings(audio)}
    candidates = []
    for index, segment in enumerate(audio.segments):
        if segment.id in flagged or not 0 < segment.end - segment.start <= 5:
            continue
        previous = audio.segments[index - 1] if index else None
        preceding = previous.text if (previous and previous.id not in flagged
                                      and segment.start - previous.end <= 2) else ""
        language = unusual_language(segment, preceding)
        priority = retry_priority(segment)
        if language or priority < 1:
            # 明确语言槽位优先，避免一次机会用在无关的低分词上。
            candidates.append((not language, priority, index, segment, language, preceding))
    if not candidates:
        return
    _, _, _, target, language, preceding = min(candidates)
    start = max(0, round((target.start - 0.5) * RATE))
    end = min(samples.size, round((target.end + 0.5) * RATE))
    original_languages = programming_languages(target.text)
    context_languages = original_languages or programming_languages(preceding)
    if language and context_languages and language.casefold() not in context_languages:
        language = None
    hint = None
    if language:
        hint = f"请用 {language} 语言实现。"
    elif context_languages:
        terms = "、".join(name.upper() if name.startswith("c") else name for name in sorted(context_languages))
        hint = f"编程术语：{terms}、补全代码、输入输出、流程图。"
    elif CODE_CUES.search(target.text):
        hint = CODE_VOCABULARY
    try:
        segments, _ = model.transcribe(samples[start:end], language="zh", beam_size=5,
            condition_on_previous_text=False, temperature=0, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}, initial_prompt=hint)
        text = " ".join(segment.text.strip() for segment in segments).strip()
    except (RuntimeError, ValueError):
        return
    normalized = lambda value: re.sub(r"[^\w+#]|_", "", unicodedata.normalize("NFKC", value)).casefold()
    if not 0 < len(text) <= 180 or not normalized(text) or normalized(text) == normalized(target.text):
        return
    candidate_languages = programming_languages(text)
    required_languages = original_languages | ({language.casefold()} if language else set())
    if required_languages and candidate_languages != required_languages:
        return
    if not required_languages and context_languages and not candidate_languages <= context_languages:
        return
    if hint and not (candidate_languages or CODE_CUES.search(text) or "编写" in text):
        # 已知是编程任务时，不把丢失领域线索的改写交给下游。
        return
    target.retry = SpeechRetry(start=start / RATE, end=end / RATE, text=text)


def transcribe(directory: Path, model_dir: Path, threads: int):
    duration = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["duration_seconds"]
    if not math.isfinite(duration) or not 0 < duration <= 30:
        raise ValueError("视频时长无效")
    samples = decode_timeline(directory / "source.mp4", duration)
    if samples is None:
        return AudioTranscript(status="no_audio", message="视频没有音轨，仅分析画面。")
    with wave.open(str(directory / "audio.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    if float(np.max(np.abs(samples), initial=0)) < 0.0005:
        return AudioTranscript(status="no_speech", message="音轨接近静音，没有可用对白。")
    if not (model_dir / "model.bin").is_file():
        return AudioTranscript(status="unavailable", message="本机语音模型未就绪，仅分析画面；安装模型后重新上传可重试。")
    from faster_whisper import WhisperModel
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=threads,
                         num_workers=1, local_files_only=True)
    segments, info = model.transcribe(samples, language="zh", word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}, condition_on_previous_text=False,
        beam_size=3, temperature=0, hallucination_silence_threshold=1.0)
    rows = speech_segments(segments, duration)
    transcript = AudioTranscript(status="ready" if rows else "no_speech", language=info.language,
        message="本机自动转写，关键数字、名称和时间请回听确认。" if rows else "未识别到可靠对白，仅分析画面。", segments=rows)
    retry_uncertain_speech(model, samples, transcript)
    return transcript


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    try:
        transcript = transcribe(directory, Path(sys.argv[2]), int(sys.argv[3]))
    except Exception:
        transcript = AudioTranscript(status="failed", message="本机对白处理失败，仅分析画面；可重新上传重试。")
    temporary = directory / ".audio-result.json"
    temporary.write_text(transcript.model_dump_json(), encoding="utf-8")
    os.replace(temporary, directory / "audio.json")

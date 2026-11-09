"""本地录音线索：相对音量、转写密度、段间间隔；不是情绪识别。"""
from array import array
import math
import re
from statistics import median
import sys
import wave


def measure_sound(directory, transcript):
    path = directory / "audio.wav"
    if transcript.status != "ready" or not path.is_file():
        return None
    try:
        with wave.open(str(path), "rb") as source:
            if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000) or source.getnframes() > 480000:
                return None
            values = array("h", source.readframes(source.getnframes()))
        if sys.byteorder != "little":
            values.byteswap()
    except (OSError, EOFError, wave.Error):
        return None
    rows, levels = [], []
    previous = None
    for segment in transcript.segments:
        left, right = round(segment.start * 16000), round(segment.end * 16000)
        if left >= right or right > len(values):
            return None
        samples = values[left:right]
        rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
        level = 20 * math.log10(max(rms, 1e-6))
        levels.append(level)
        count = len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", segment.text))
        rows.append({"segment_id": segment.id, "text_units_per_second": round(count / max(segment.end - segment.start, 0.1), 2),
            "gap_before": round(max(0, segment.start - previous), 3) if previous is not None else None})
        previous = segment.end
    center = median(levels)
    for row, level in zip(rows, levels):
        difference = round(level - center, 1)
        row["relative_db"] = difference
        row["volume"] = "not_compared" if len(rows) < 2 else "quieter" if difference < -4 else "louder" if difference > 4 else "similar"
    return {"version": "relative-rms-text-density-v1", "segments": rows,
        "note": "相对音量受环境声与录音增益影响；字词密度和间隔来自转写时间，不是准确语速或情绪结论，未测音高。"}


def sound_prompt(clues):
    if not clues:
        return ""
    labels = {"not_compared": "单段不作音量比较", "quieter": "相对较轻", "louder": "相对较响", "similar": "相对接近"}
    rows = [f"S{row['segment_id']}:{labels[row['volume']]}，字词密度{row['text_units_per_second']}/秒"
            + (f"，前间隔{row['gap_before']}秒" if row["gap_before"] is not None else "") for row in clues["segments"]]
    return "本机声音线索（仅可辅助结合对白、场景和用户配文回应，不据此断言焦虑、生气或其他真实心情）：" + clues["note"] + "\n" + "；".join(rows)

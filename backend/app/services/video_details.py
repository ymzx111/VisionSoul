"""隔离进程按时间从原视频提取最多4张高清图；无模型/网络访问。"""
import json
import math
from pathlib import Path
import sys

import av

DETAIL_CACHE_VERSION = "source-1920-v2"
DETAIL_MAX_SIDE = 1920


def detail_path(directory: Path, index: int) -> Path:
    return directory / f"hq-{DETAIL_CACHE_VERSION}-{index}.jpg"


def adjusted_path(directory: Path, index: int, kind: str) -> Path:
    return detail_path(directory, index).with_suffix(f".{kind}.jpg")


def adjustment_path(directory: Path, index: int) -> Path:
    return detail_path(directory, index).with_suffix(".json")


def extract_details(directory: Path, output: Path, targets: list[dict], *, refine=False):
    if not 1 <= len(targets) <= 4:
        raise ValueError("高清帧数量无效")
    indices = [item["index"] for item in targets]
    if len(set(indices)) != len(indices) or any(type(index) is not int or not 0 <= index <= 60 for index in indices):
        raise ValueError("高清帧编号无效")
    if any(not math.isfinite(item["time_seconds"]) or not 0 <= item["time_seconds"] <= 30 for item in targets):
        raise ValueError("高清帧时间无效")
    if refine:
        from app.services.video_refinement import refine_details
        refine_details(directory, output, targets)
        return
    pending = sorted(targets, key=lambda item: item["time_seconds"])
    for timestamp, frame in source_frames(directory):
        target = pending[0]
        if timestamp < target["time_seconds"]:
            continue
        if timestamp != target["time_seconds"]:
            raise ValueError("源帧与采样记录不匹配")
        source_image(frame).save(detail_path(output, target["index"]), "JPEG", quality=92)
        pending.pop(0)
        if not pending:
            return
    raise ValueError("原视频缺少所选画面")


def source_image(frame):
    factor = min(1.0, DETAIL_MAX_SIDE / max(frame.width, frame.height))
    image = frame.reformat(width=max(1, round(frame.width * factor)),
                           height=max(1, round(frame.height * factor)), format="rgb24").to_image()
    return image.rotate(frame.rotation, expand=True) if frame.rotation else image


def source_frames(directory: Path):
    with av.open(str(directory / "source.mp4"), format="mov", options={
        "protocol_whitelist": "file", "enable_drefs": "0", "use_absolute_path": "0",
    }) as container:
        if len(container.streams.video) != 1:
            raise ValueError("视频流无效")
        stream = container.streams.video[0]
        if stream.codec_context.name != "h264":
            raise ValueError("视频编码无效")
        stream.codec_context.thread_count = 2
        origin = float((stream.start_time or 0) * stream.time_base)
        previous = -1.0
        for count, frame in enumerate(container.decode(stream), 1):
            if count > 1860 or min(frame.width, frame.height) < 16 or max(frame.width, frame.height) > 4096 or frame.width * frame.height > 3840 * 2160:
                raise ValueError("视频资源上限异常")
            if frame.time is None:
                raise ValueError("视频没有时间戳")
            timestamp = float(frame.time) - origin
            if not math.isfinite(timestamp) or timestamp < -0.1 or timestamp < previous or timestamp > 30.1:
                raise ValueError("视频时间戳异常")
            previous = timestamp
            yield round(max(0, timestamp), 3), frame


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    directory, output = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        extract_details(directory, output, json.loads((output / "targets.json").read_text(encoding="utf-8")), refine=True)
    except Exception:
        # 父进程只看返回码；不给上传方暴露本地路径或底层解码信息。
        sys.exit(1)

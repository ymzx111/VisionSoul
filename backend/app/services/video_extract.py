"""隔离进程中的本地 MP4 解码；不读取密钥，不访问模型。"""
import hashlib
import json
import math
from pathlib import Path
import sys

import av
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.video_policy import SAMPLE_INTERVAL_SECONDS
from app.services.video_comparison import clarity_score, DETAIL_VERSION


def extract(directory: Path, max_seconds: float, max_frames: int) -> dict:
    source = directory / "source.mp4"
    with source.open("rb") as file:
        if file.read(12)[4:8] != b"ftyp":
            raise ValueError("请选择有效的 MP4 视频。")
    # 限制 demuxer 和协议，禁用外部引用；上传文件只能读取本地这一个 MP4。
    with av.open(str(source), format="mov", options={
        "protocol_whitelist": "file", "enable_drefs": "0", "use_absolute_path": "0",
    }) as container:
        if len(container.streams.video) != 1:
            raise ValueError("请上传只包含一个视频画面的 MP4。")
        stream = container.streams.video[0]
        if stream.codec_context.name != "h264":
            raise ValueError("第一版支持 H.264 编码的 MP4，请将视频导出为此格式后上传。")
        width, height = stream.codec_context.width, stream.codec_context.height
        if min(width, height) < 16 or max(width, height) > 4096 or width * height > 3840 * 2160:
            raise ValueError("视频分辨率过大或无效，请使用不超过 4K 的视频。")
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else 0
        if not math.isfinite(duration) or not 0 < duration <= max_seconds:
            raise ValueError(f"请使用时长不超过 {max_seconds:g} 秒、具有有效时长的短视频。")
        fps = float(stream.average_rate or 0)
        if not math.isfinite(fps) or not 0 < fps <= 60.1:
            raise ValueError("第一版支持每秒不超过 60 帧的视频。")
        stream.codec_context.thread_count = 2
        interval = SAMPLE_INTERVAL_SECONDS
        target = 0.0
        frames = []
        sampled = 0
        hashes = {}
        last_sample_time = -1.0
        origin = float((stream.start_time or 0) * stream.time_base)
        previous_time = -1.0
        last_frame = None

        def keep(frame, timestamp):
            nonlocal sampled, last_sample_time
            if sampled >= max_frames:
                raise ValueError("采样数量超过配置上限，请将 VIDEO_MAX_FRAMES 设置为31。")
            sampled += 1
            last_sample_time = timestamp
            factor = min(1.0, 1280 / max(frame.width, frame.height))
            image = frame.reformat(width=max(1, round(frame.width * factor)),
                                   height=max(1, round(frame.height * factor)), format="rgb24").to_image()
            if frame.rotation:
                image = image.rotate(frame.rotation, expand=True)
            digest = hashlib.sha256(image.tobytes()).hexdigest()
            timestamp = round(max(0, timestamp), 3)
            if digest in hashes:
                frames[hashes[digest]]["seen_at"].append(timestamp)
                return
            index = len(frames)
            hashes[digest] = index
            image.save(directory / f"detail-{index}.jpg", "JPEG", quality=90)
            image.thumbnail((768, 768))
            image.save(directory / f"frame-{index}.jpg", "JPEG", quality=82)
            frames.append({"index": index, "time_seconds": timestamp, "seen_at": [timestamp], "clarity": clarity_score(image)})

        for decoded_count, frame in enumerate(container.decode(stream), 1):
            if decoded_count > 1860:
                raise ValueError("视频帧数异常，请重新导出短视频。")
            if max(frame.width, frame.height) > 4096 or frame.width * frame.height > 3840 * 2160:
                raise ValueError("视频中出现超大分辨率画面，请降低分辨率后上传。")
            if frame.time is None:
                raise ValueError("视频缺少时间戳，请重新导出。")
            timestamp = float(frame.time) - origin
            if not math.isfinite(timestamp) or timestamp < -0.1 or timestamp < previous_time:
                raise ValueError("视频时间戳异常，请重新导出。")
            previous_time = timestamp
            last_frame = frame
            if timestamp > max_seconds + 0.1 or timestamp > duration + 0.2:
                raise ValueError("视频实际时长超过声明时长或上传限制。")
            if timestamp + 0.001 < target:
                continue
            keep(frame, timestamp)
            # 低帧率或可变帧率不重复同一源帧凑数。
            while target <= timestamp + 0.001:
                target += interval
        if not frames or previous_time + max(0.25, 2 / fps) < duration:
            raise ValueError("视频为空、已损坏或未能完整解码，请重新导出。")
        if last_frame is not None and previous_time > last_sample_time + 0.001:
            keep(last_frame, previous_time)
        return {"duration_seconds": round(duration, 3), "width": width, "height": height,
                "sample_interval_seconds": interval, "sample_count": sampled, "frames": frames,
                "detail_version": DETAIL_VERSION}


if __name__ == "__main__":
    try:
        result = extract(Path(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3]))
        (Path(sys.argv[1]) / "manifest.json").write_text(json.dumps(result), encoding="utf-8")
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else "视频无法解码，可能已经损坏或格式不支持。"
        (Path(sys.argv[1]) / "extract-error.json").write_text(json.dumps({"detail": message}), encoding="utf-8")
        sys.exit(1)

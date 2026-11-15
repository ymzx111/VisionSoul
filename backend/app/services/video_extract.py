"""隔离进程中的本地 MP4 解码；不读取密钥，不访问模型。"""
import hashlib
import json
import math
from pathlib import Path
import sys

import av
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.video_policy import SAMPLE_INTERVAL_SECONDS, VIDEO_CODECS, VIDEO_PREVIEW_FILENAME
from app.services.video_comparison import clarity_score, DETAIL_VERSION

MP4_OPTIONS = {"protocol_whitelist": "file", "enable_drefs": "0", "use_absolute_path": "0"}


def make_compatible_preview(source: Path, destination: Path, rotation: int, end: float):
    """Encode the browser copy. The upload remains the source for frame/audio evidence."""
    with (av.open(str(source), format="mov", options=MP4_OPTIONS) as original,
          av.open(str(destination), "w", format="mp4", options={"movflags": "+faststart"}) as preview):
        video = original.streams.video[0]
        video.codec_context.thread_count = 2
        rendered = preview.add_stream("libx264", rate=video.average_rate, options={
            "preset": "veryfast", "crf": "21", "maxrate": "8M", "bufsize": "8M",
        })
        scale = min(1.0, 1920 / max(video.width, video.height))
        rendered.width = 2 * round(video.width * scale / 2)
        rendered.height = 2 * round(video.height * scale / 2)
        rendered.pix_fmt = "yuv420p"
        rendered.codec_context.time_base = video.time_base
        rendered.codec_context.thread_count = 2
        if rotation:
            rendered.set_display_rotation(rotation)
        audio = next(iter(original.streams.audio), None)
        audio_output = None
        audio_resampler = None
        if audio is not None:
            if audio.codec_context.sample_rate > 192000 or audio.codec_context.channels > 8:
                raise ValueError("视频音轨超出处理限制，请重新导出。")
            if audio.codec_context.name == "aac":
                audio_output = preview.add_stream_from_template(audio)
            else:
                audio_output = preview.add_stream("aac", rate=48000)
                audio_output.layout = "stereo"
                audio_resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)

        streams = (video, audio) if audio is not None else (video,)
        for packet in original.demux(*streams):
            if packet.stream == video:
                for frame in packet.decode():
                    resized = frame.reformat(width=rendered.width, height=rendered.height, format="yuv420p")
                    for encoded in rendered.encode(resized):
                        preview.mux(encoded)
            elif packet.pts is not None and float(packet.pts * packet.time_base) <= end + 0.1:
                if audio_resampler is None:
                    if packet.dts is not None:
                        packet.stream = audio_output
                        preview.mux(packet)
                else:
                    for frame in packet.decode():
                        for converted in audio_resampler.resample(frame):
                            for encoded in audio_output.encode(converted):
                                preview.mux(encoded)
        if audio_resampler is not None:
            for converted in audio_resampler.resample(None):
                for encoded in audio_output.encode(converted):
                    preview.mux(encoded)
            for encoded in audio_output.encode():
                preview.mux(encoded)
        for encoded in rendered.encode():
            preview.mux(encoded)


def extract(directory: Path, max_seconds: float, max_frames: int) -> dict:
    source = directory / "source.mp4"
    with source.open("rb") as file:
        if file.read(12)[4:8] != b"ftyp":
            raise ValueError("请选择有效的 MP4 视频。")
    # 限制 demuxer 和协议，禁用外部引用；上传文件只能读取本地这一个 MP4。
    with av.open(str(source), format="mov", options=MP4_OPTIONS) as container:
        if len(container.streams.video) != 1:
            raise ValueError("请上传只包含一个视频画面的 MP4。")
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        if codec not in VIDEO_CODECS:
            raise ValueError("请上传 H.264 或 H.265 编码的 MP4 视频。")
        width, height = stream.codec_context.width, stream.codec_context.height
        if min(width, height) < 16 or max(width, height) > 4096 or width * height > 3840 * 2160:
            raise ValueError("视频分辨率过大或无效，请使用不超过 4K 的视频。")
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else 0
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('视频时长无效，请重新导出视频。')
        if duration > max_seconds:
            raise ValueError(f'超过视频时长上限，上限为{max_seconds:g}秒')
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
        rotation = 0

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
            if decoded_count == 1:
                rotation = frame.rotation
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
            if timestamp > max_seconds + 0.1:
                raise ValueError(f'超过视频时长上限，上限为{max_seconds:g}秒')
            if timestamp > duration + 0.2:
                raise ValueError("视频实际时长超过声明时长或上传限制。")
            if timestamp + 0.001 < target:
                continue
            keep(frame, timestamp)
            # 低帧率或可变帧率不重复同一源帧凑数。
            while target <= timestamp + 0.001:
                target += interval
        last_duration = float((last_frame.duration or 0) * last_frame.time_base) if last_frame is not None else 0
        covered_until = previous_time + max(0, last_duration)
        # Windows 剪辑可能含非零起点与编辑列表。只有所有声明帧均成功解码，
        # 且偏差能由起点解释时，接受这种时长差，不放宽中途截断检查。
        complete_edit = (last_frame is not None and decoded_count == stream.frames and origin > 0
                         and covered_until + origin + max(0.25, 2 / fps) >= duration)
        if not frames or (covered_until + max(0.25, 2 / fps) < duration and not complete_edit):
            raise ValueError("视频为空、已损坏或未能完整解码，请重新导出。")
        if last_frame is not None and previous_time > last_sample_time + 0.001:
            keep(last_frame, previous_time)
        if codec == "hevc":
            try:
                make_compatible_preview(source, directory / VIDEO_PREVIEW_FILENAME, rotation, origin + duration)
            except (av.error.FFmpegError, OverflowError) as exc:
                raise ValueError("H.265 视频的本地预览转换失败，请重新导出为 MP4。") from exc
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

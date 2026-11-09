"""下载已核许可的公开素材并制作短MP4；不安装模型、不调用AI。"""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

import av
import httpx
import numpy as np
from PIL import ImageFilter

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "video_eval"
UA = "VisionSoulEvaluation/0.1 (local educational project; policy: https://meta.wikimedia.org/wiki/User-Agent_policy) python-httpx/0.28.1"


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def download(source):
    path = DATA / "raw" / (source["id"] + ".webm")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        temporary = path.with_suffix(".part")
        total = 0
        with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True) as client:
            with client.stream("GET", source["url"]) as response:
                response.raise_for_status()
                if "video/" not in response.headers.get("content-type", ""):
                    raise ValueError("下载不是视频，未保存为素材")
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > 80 * 1024 * 1024:
                            raise ValueError("源素材超过本次下载预算")
                        output.write(chunk)
        temporary.replace(path)
    return path


def read_audio(path, duration):
    with av.open(str(path)) as container:
        if not container.streams.audio:
            return None
        video = container.streams.video[0]
        origin = float((video.start_time or 0) * video.time_base)
        track = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        samples = np.zeros(round(duration * 16000), dtype=np.float32)
        def keep(frame):
            if frame.time is None:
                raise ValueError("音轨没有原始时间戳")
            values = frame.to_ndarray().reshape(-1)
            start = round((float(frame.time) - origin) * 16000)
            left, right = max(0, start), min(len(samples), start + len(values))
            if right > left:
                samples[left:right] = values[left - start:right - start]
        for frame in container.decode(track):
            if frame.time is not None and float(frame.time) - origin > duration + 0.2:
                break
            for converted in resampler.resample(frame):
                keep(converted)
        for converted in resampler.resample(None):
            keep(converted)
        return samples


def transcode(source, target, seconds, *, audio_source=None, audio_delay=0, silent=False, blur_windows=()):
    audio = read_audio(audio_source or source, seconds)
    if silent:
        audio = np.zeros(round(seconds * 16000), dtype=np.float32)
    if audio is not None and audio_delay:
        shift = round(audio_delay * 16000)
        audio = np.concatenate((np.zeros(shift, dtype=np.float32), audio))[:round(seconds * 16000)]
    target.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(source)) as input_container, av.open(str(target), "w", format="mp4") as output:
        stream = input_container.streams.video[0]
        origin = float((stream.start_time or 0) * stream.time_base)
        factor = min(1.0, 1280 / max(stream.width, stream.height))
        width, height = [max(2, round(value * factor / 2) * 2) for value in (stream.width, stream.height)]
        video = output.add_stream("libx264", rate=25)
        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
        video.options = {"crf": "23", "preset": "fast"}
        sound = output.add_stream("aac", rate=16000) if audio is not None else None
        if sound:
            sound.layout = "mono"
        count, last = 0, None
        def encode(image, index):
            if any(start <= index / 25 < end for start, end in blur_windows):
                image = image.filter(ImageFilter.GaussianBlur(6))
            frame = av.VideoFrame.from_image(image)
            frame.pts, frame.time_base = index, Fraction(1, 25)
            for packet in video.encode(frame):
                output.mux(packet)
        for frame in input_container.decode(stream):
            if frame.time is None:
                raise ValueError("视频没有原始时间戳")
            timestamp = float(frame.time) - origin
            if timestamp >= seconds:
                break
            if timestamp + 0.001 < count / 25:
                continue
            last = frame.reformat(width=width, height=height, format="rgb24").to_image()
            while count / 25 <= timestamp + 0.001 and count < round(seconds * 25):
                encode(last, count)
                count += 1
        if last is None or count / 25 < seconds - 0.2:
            raise ValueError("素材不足或截取未完成")
        while count < round(seconds * 25):
            encode(last, count)
            count += 1
        for packet in video.encode():
            output.mux(packet)
        if sound:
            for start in range(0, len(audio), 1024):
                values = np.clip(audio[start:start + 1024], -1, 1).astype(np.float32)[None, :]
                frame = av.AudioFrame.from_ndarray(values, format="fltp", layout="mono")
                frame.sample_rate, frame.pts, frame.time_base = 16000, start, Fraction(1, 16000)
                for packet in sound.encode(frame):
                    output.mux(packet)
            for packet in sound.encode():
                output.mux(packet)


def prepare(split):
    if split == "test":
        from evaluate_video_corpus import verify_freeze
        verify_freeze()
    sources = json.loads((DATA / "sources.json").read_text(encoding="utf-8"))["sources"]
    records = []
    for source in sources:
        if source["split"] != split:
            continue
        raw = download(source)
        target = DATA / split / (source["id"] + ".mp4")
        if not target.is_file():
            transcode(raw, target, source["seconds"])
        record = {**source, "file": f"{split}/{target.name}", "source_ids": [source["id"]],
                  "variant": "original_excerpt", "sha256": digest(target), "raw_sha256": digest(raw),
                  "changes": "前段截取、最长边1280、不放大、25fps H.264 MP4/AAC；原音轨按视频共同起点映射。"}
        records.append(record)
        print(f"prepared {split}/{target.name}", flush=True)
    by_id = {row["id"]: row for row in records}
    speech_id, visual_id = ("tao_mandarin", "lake_closeup_editor") if split == "dev" else ("ying_henan", "skaha_lake")
    for name, parent_ids, options in [
        (speech_id + "_delay", [speech_id], {"audio_delay": 1 if split == "dev" else 2}),
        (visual_id + "_speech_mismatch", [visual_id, speech_id], {"audio_source": DATA / split / f"{speech_id}.mp4"}),
        (("trees_ready_street" if split == "dev" else visual_id) + "_blur", ["trees_ready_street" if split == "dev" else visual_id],
         {"blur_windows": [[3, 6], [8, 10]] if split == "dev" else [[4, 8], [12, 16]]}),
    ] + ([(speech_id + "_silent", [speech_id], {"silent": True})] if split == "test" else []):
        parent = by_id[parent_ids[0]]
        seconds = parent["seconds"]
        target = DATA / split / (name + ".mp4")
        if not target.is_file():
            transcode(DATA / parent["file"], target, seconds, **options)
        records.append({"id": name, "split": split, "source_ids": parent_ids, "file": f"{split}/{target.name}",
            "seconds": seconds, "category": parent["category"], "variant": name.rsplit("_", 1)[-1],
            "sha256": digest(target), "audio_delay": options.get("audio_delay", 0),
            "blur_windows": options.get("blur_windows", []), "silent": options.get("silent", False),
            "changes": "已知受控变体；音轨延迟/静音/替换或部分画面模糊，详见字段，不能当成自然实拍原片。",
            "expected_visual": parent["expected_visual"], "speech": "no_speech" if options.get("silent") else "yes" if "audio_source" in options or "audio_delay" in options else parent["speech"],
            "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"})
        print(f"prepared {split}/{target.name}", flush=True)
    manifest_path = DATA / f"{split}_manifest.json"
    if manifest_path.is_file() and json.loads(manifest_path.read_text(encoding="utf-8"))["clips"] == records:
        return
    write_json(manifest_path, {"version": "v1", "created_at": datetime.now(timezone.utc).isoformat(), "clips": records})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    args = parser.parse_args()
    prepare(args.split)

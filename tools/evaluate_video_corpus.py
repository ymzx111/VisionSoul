"""公开语料本机评测及小规模显式API评测；不微调模型、不改正式数据库。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import wave

import av
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import Settings
from app.services.audio_evidence import frame_candidates
from app.services.video_selection import select_from_directory
from app.services.videos import VideoService
from app.services.deepseek import DeepSeekClient
from app.services.deepseek import ModelReply

DATA = ROOT / "datasets" / "video_eval"
CODE = ["backend/app/services/video_selection.py", "backend/app/services/video_extract.py", "backend/app/services/video_details.py",
        "backend/app/services/audio_extract.py", "backend/app/services/audio_evidence.py", "backend/app/core/video_policy.py",
        "backend/app/services/videos.py", "backend/app/prompts/audio.md", "backend/app/prompts/video.md",
        "backend/app/services/deepseek.py", "backend/app/core/config.py", "backend/requirements.txt",
        "backend/requirements-audio-lock.txt", "frontend/src/VideoWorkspace.jsx",
        "tools/prepare_video_eval.py", "tools/evaluate_video_corpus.py"]


def verify_source_groups():
    sources = json.loads((DATA / "sources.json").read_text(encoding="utf-8"))["sources"]
    owners = {row["id"]: row["split"] for row in sources}
    assert len(owners) == len(sources), "重复原视频ID"
    seen = {}
    for split in ("dev", "test"):
        path = DATA / f"{split}_manifest.json"
        if not path.is_file():
            continue
        for clip in json.loads(path.read_text(encoding="utf-8"))["clips"]:
            assert clip["split"] == split and all(owners.get(source) == split for source in clip["source_ids"]), "同源素材跨集合"
            assert clip["sha256"] not in seen or seen[clip["sha256"]] == split, "重复视频跨集合"
            seen[clip["sha256"]] = split


def sha(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def freeze():
    path = DATA / "freeze.json"
    if path.exists():
        raise ValueError("已冻结，请新建下一版语料，不覆盖原测试基线")
    verify_source_groups()
    write(path, {"version": "v1", "frozen_at": datetime.now(timezone.utc).isoformat(),
        "sources_sha256": sha(DATA / "sources.json"), "dev_manifest_sha256": sha(DATA / "dev_manifest.json"),
        "code_sha256": {name: sha(ROOT / name) for name in CODE},
        "policy": "同一原视频的所有变体只属于同一split；测试集运行后不据此反向调参，后续改动使用新版本独立测试。"})


def verify_freeze():
    verify_source_groups()
    frozen = json.loads((DATA / "freeze.json").read_text(encoding="utf-8"))
    assert frozen["sources_sha256"] == sha(DATA / "sources.json"), "来源清单在冻结后发生变化"
    assert frozen["dev_manifest_sha256"] == sha(DATA / "dev_manifest.json"), "调试集在冻结后发生变化"
    for name, checksum in frozen["code_sha256"].items():
        assert sha(ROOT / name) == checksum, "冻结后代码变化：" + name
    return frozen


def settings(split, api=False):
    base = Settings() if api else Settings(_env_file=None)
    storage = DATA / "runtime" / split
    return base.model_copy(update={"video_storage_dir": storage / "videos", "chat_database_path": storage / "chat.sqlite3",
        "role_database_path": storage / "roles.sqlite3"})


def preview(info, directory, selected, output):
    indices = sorted(set(selected + [0, len(info.frames) // 2, len(info.frames) - 1]))
    sheet = Image.new("RGB", (320 * len(indices), 220), "white")
    draw = ImageDraw.Draw(sheet)
    for column, index in enumerate(indices):
        frame = info.frames[index]
        with Image.open(directory / f"frame-{index}.jpg") as source:
            source.thumbnail((312, 180))
            sheet.paste(source, (column * 320 + 4, 4))
        if index in selected:
            draw.rectangle((column * 320, 0, column * 320 + 318, 185), outline="red", width=3)
        draw.text((column * 320 + 6, 193), f"#{index} {frame.time_seconds:g}s" + (" HIGH" if index in selected else ""), fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def audio_envelope(path):
    with wave.open(str(path), "rb") as source:
        values = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32)
    values = values[:len(values) // 160 * 160].reshape(-1, 160)
    return np.sqrt(np.mean(values * values, axis=1))


def evaluate(split):
    if split == "test":
        verify_freeze()
    manifest = json.loads((DATA / f"{split}_manifest.json").read_text(encoding="utf-8"))
    service = VideoService(settings(split))
    rows, runtime = [], {}
    for clip in manifest["clips"]:
        path = DATA / clip["file"]
        assert sha(path) == clip["sha256"], "素材校验失败：" + clip["id"]
        start = time.perf_counter()
        existing_ids = {path.name for path in service.settings.video_storage_dir.iterdir()} if service.settings.video_storage_dir.is_dir() else set()
        with path.open("rb") as source:
            info = service.upload(source)
        upload_seconds = time.perf_counter() - start
        directory = service.settings.video_storage_dir / info.video_id
        start = time.perf_counter()
        selected = select_from_directory(info.frames, directory)
        selection_seconds = time.perf_counter() - start
        detailed = service._detail_frames(info, directory)
        timestamps = sorted(time for frame in info.frames for time in frame.seen_at)
        checks = {"source_checksum": True, "max_31_samples": info.sample_count <= 31,
            "one_second_coverage": all(abs(value - round(value)) <= 0.05 for value in timestamps[:-1]),
            "covers_start_and_tail": timestamps[0] <= 0.05 and timestamps[-1] >= info.duration_seconds - 0.12,
            "max_4_high_frames": 1 <= len(detailed) <= 4,
            "speech_candidates_exist": all(frame_candidates(segment, info.frames) for segment in info.audio.segments)}
        if clip["speech"] in {"yes", "no_audio", "no_speech"}:
            expected = "ready" if clip["speech"] == "yes" else clip["speech"]
            checks["expected_audio_state"] = info.audio.status == expected
        row = {"id": clip["id"], "file": clip["file"], "video_id": info.video_id,
            "seconds": info.duration_seconds, "sample_count": info.sample_count, "unique_frames": len(info.frames),
            "high_frames": [{"index": index, "time": info.frames[index].time_seconds} for index in selected],
            "upload_asr_seconds": round(upload_seconds, 3), "selection_seconds": round(selection_seconds, 3),
            "reused_local_cache": info.video_id in existing_ids,
            "audio": info.audio.model_dump(), "checks": checks}
        if clip.get("blur_windows"):
            blurred = [index for index in selected if any(left <= info.frames[index].time_seconds < right for left, right in clip["blur_windows"])]
            row["high_frames_in_known_blur"] = blurred
            checks["prefers_clear_frames"] = len(blurred) <= len(selected) / 2
        runtime[clip["id"]] = directory
        if clip.get("audio_delay"):
            base = runtime[clip["source_ids"][0]]
            left, right = audio_envelope(directory / "audio.wav"), audio_envelope(base / "audio.wav")
            correlation = np.correlate(left - left.mean(), right - right.mean(), mode="full")
            measured = (int(correlation.argmax()) - len(right) + 1) / 100
            row["known_audio_delay"] = clip["audio_delay"]
            row["measured_waveform_delay"] = measured
            checks["waveform_delay_preserved"] = abs(measured - clip["audio_delay"]) <= 0.08
        preview(info, directory, selected, DATA / "previews" / f"{clip['id']}.jpg")
        row["engineering_pass"] = all(checks.values())
        rows.append(row)
        print(json.dumps({"id": clip["id"], "pass": row["engineering_pass"], "audio": info.audio.status,
            "high": len(selected), "failed": [key for key, value in checks.items() if not value]}, ensure_ascii=False), flush=True)
        write(DATA / "reports" / f"{split}_local.json", {"split": split, "clips": rows,
            "note": "工程评测，不是语义准确率；unknown音轨不预设无讲话；转写不可当成真值标签。", "paid_api_calls": 0})


def api_evaluate(split, replay=False):
    if split == "test":
        verify_freeze()
    local = json.loads((DATA / "reports" / f"{split}_local.json").read_text(encoding="utf-8"))["clips"]
    service = VideoService(settings(split, api=True))
    chosen = {"trees_ready_street", "tao_mandarin", "lake_closeup_editor_speech_mismatch"} if split == "dev" else {"tree_harvester", "ying_henan", "skaha_lake_speech_mismatch"}
    results = []
    for clip in local:
        if clip["id"] not in chosen:
            continue
        output = DATA / "reports" / f"{split}_api_{clip['id']}.json"
        if output.is_file():
            results.append(json.loads(output.read_text(encoding="utf-8")))
            print("reused API report " + clip["id"], flush=True)
            continue
        question = "这是一段第三方公开视频，讲话者不是我。分别说明画面中可见什么、对白在介绍什么，并给出时间依据。对白提到的姓名地点不能当成画面验证结果，不能从时间接近推定内容一致。"
        receipt = DATA / "reports" / f"{split}_receipt_{clip['id']}.json"
        if receipt.is_file() and not replay:
            raise ValueError("已有上游调用凭据但没有成功报告，请人工审阅，不自动再次付费：" + clip["id"])
        if replay and not receipt.is_file():
            print("no receipt to replay: " + clip["id"], flush=True)
            continue
        class RecordingClient(DeepSeekClient):
            def complete(self, messages, **kwargs):
                if replay:
                    return ModelReply.model_validate(json.loads(receipt.read_text(encoding="utf-8"))["reply"])
                reply = super().complete(messages, **kwargs)
                write(receipt, {"id": clip["id"], "reply": reply.model_dump(), "validation": "pending"})
                return reply
        service.client = RecordingClient(service.settings)
        start = time.perf_counter()
        high_detail = clip["id"] in {"trees_ready_street", "tree_harvester"}
        if high_detail:
            question = "请细看并比对画面主体，列出能看到的特征和时间依据；不能仅凭远景断言树种、地名或人物身份。"
        try:
            answer = service._observe(service.info(clip["video_id"]), question, inspect=high_detail)
        except Exception as exc:
            failure = {"id": clip["id"], "status": "rejected", "error": str(exc),
                "usage": json.loads(receipt.read_text(encoding="utf-8"))["reply"].get("usage") if receipt.is_file() else None,
                "note": "失败可能已有用量，不自动重试；原始凭据如有在receipt中。"}
            write(DATA / "reports" / f"{split}_failed_{clip['id']}.json", failure)
            write(output, failure)
            results.append(failure)
            print("rejected API result " + clip["id"], flush=True)
            continue
        row = {"id": clip["id"], "question": question, "elapsed_seconds": round(time.perf_counter() - start, 3),
               "answer": answer.model_dump(), "review_status": "needs_review", "validation_replayed_from_receipt": replay,
               "note": "回执重放只验证解析，不代表模型采用了新提示。" if replay else "真实请求"}
        write(output, row)
        results.append(row)
        print(json.dumps({"id": clip["id"], "usage": answer.usage.model_dump() if answer.usage else None,
            "relations": [link.relation for link in answer.audio_links]}, ensure_ascii=False), flush=True)
    write(DATA / "reports" / f"{split}_api_summary.json", {"split": split, "cases": len(results),
        "input_tokens": sum(((row.get("answer") or {}).get("usage") or row.get("usage") or {}).get("prompt_tokens", 0) for row in results),
        "output_tokens": sum(((row.get("answer") or {}).get("usage") or row.get("usage") or {}).get("completion_tokens", 0) for row in results),
        "note": "单次小样例观察；需按独立标注审阅，不直接汇报准确率。"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"])
    parser.add_argument("--api", action="store_true", help="显式付费：每组最多3个模型请求，已有报告不重复调用")
    parser.add_argument("--replay-receipts", action="store_true", help="只重放已保存的上游回执，不调用API")
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    if args.freeze:
        freeze()
    elif args.split:
        api_evaluate(args.split, args.replay_receipts) if args.api or args.replay_receipts else evaluate(args.split)
    else:
        parser.error("请选择split或freeze")

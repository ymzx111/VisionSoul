"""在用户指定的视频区间内抽取和挑选原始高清帧。"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import av

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.video_policy import FOCUS_INTERVAL_MS
from app.services.frame_quality import measure_frame
from app.services.video_details import source_frames, source_image
from app.services.video_reading import valid_focus_range
from app.services.video_selection import select_detail_frames


def focus_path(directory: Path, milliseconds: int) -> Path:
    return directory / f"focus-source-{milliseconds}.jpg"


def extract_focus_frames(directory, output, interval, profile):
    if not valid_focus_range(interval) or profile not in {"general", "text"}:
        raise ValueError("重点区间无效")
    start, end = interval
    targets = list(range(start, end + 1, FOCUS_INTERVAL_MS))
    if targets[-1] != end:
        targets.append(end)

    # 每个目标点保留窗口内距离最近的真实画面，保留原始时间戳。
    candidates = {}
    for timestamp, frame in source_frames(directory):
        milliseconds = round(timestamp * 1000)
        if milliseconds > end:
            break
        if milliseconds < start:
            continue
        target = min(targets, key=lambda value: abs(value - milliseconds))
        previous = candidates.get(target)
        if previous is None or abs(milliseconds - target) < abs(previous[0] - target):
            candidates[target] = (milliseconds, frame)

    by_time = dict(candidates.values())
    times = sorted(by_time)
    frames = [SimpleNamespace(index=index, time_seconds=time / 1000, seen_at=[time / 1000])
              for index, time in enumerate(times)]
    measurements = {index: measure_frame(source_image(by_time[time]), text=profile == "text")
                    for index, time in enumerate(times)}
    selected = [times[index] for index in select_detail_frames(frames, measurements, profile=profile)]
    for time in selected:
        source_image(by_time[time]).save(focus_path(output, time), "JPEG", quality=92)
    return selected


if __name__ == "__main__":
    source, destination = map(Path, sys.argv[1:3])
    try:
        options = json.loads((destination / "focus.json").read_text(encoding="utf-8"))
        selected_times = extract_focus_frames(source, destination, options["interval"], options["profile"])
        (destination / "focus-result.json").write_text(json.dumps(selected_times), encoding="utf-8")
    except (OSError, ValueError, KeyError, av.error.FFmpegError):
        sys.exit(1)

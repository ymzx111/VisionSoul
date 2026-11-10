"""本地选帧：质量、内容差异和时间覆盖；不访问模型。"""
from app.services.frame_quality import FrameQuality, measure_frame, visual_distance
from PIL import Image

SELECTION_VERSION = "quality-diversity-focus-v3"
MAX_DETAIL_FRAMES = 4
NEAR_DISTANCE = 0.03


def select_detail_frames(frames, measurements: dict[int, FrameQuality], limit=MAX_DETAIL_FRAMES):
    """只减少高清名额的重复；不删除概览图。名额是上限，不强行凑满。"""
    limit = max(0, min(MAX_DETAIL_FRAMES, limit))
    eligible = [frame for frame in frames if frame.index in measurements]
    if not eligible or not limit:
        return []
    quality = lambda frame: measurements[frame.index].quality
    best = max(quality(frame) for frame in eligible)
    # 只排除近乎全黑/全白且没有结构的帧，不能把正常的低纹理场景误删。
    def readable(frame):
        metric = measurements[frame.index]
        if metric.brightness is not None and metric.brightness < 0.07 and metric.contrast < 0.12 and metric.sharpness < 0.08:
            return False
        return metric.exposure >= 0.1 or metric.sharpness >= 0.04
    usable = [frame for frame in eligible if readable(frame)]
    if not usable:
        return [max(eligible, key=lambda frame: (quality(frame), -frame.index)).index]
    eligible = usable
    def clearer_peer(frame):
        current = measurements[frame.index]
        if current.focus is None or (current.focus <= 1e-6 and current.contrast <= 0.01):
            # 完全平滑的墙面/天空也可能是独立内容，不凭缺少纹理推定失焦。
            return False
        for other in usable:
            metric = measurements[other.index]
            # 仅与附近、粗颜色布局相近的帧比较，保留独立低纹理场景及全部概览。
            if metric.focus is None or metric.focus < 0.08 or current.focus >= metric.focus * 0.45:
                continue
            if min(abs(a - b) for a in frame.seen_at for b in other.seen_at) > 5:
                continue
            color_gap = sum(abs(a - b) for a, b in zip(current.signature[:768], metric.signature[:768])) / (768 * 255)
            if color_gap <= 0.18:
                return True
        return False
    focused = [frame for frame in usable if not clearer_peer(frame)]
    eligible = focused or usable
    representatives = []
    distances = {}

    def distance(a, b):
        key = tuple(sorted((a.index, b.index)))
        if key not in distances:
            distances[key] = visual_distance(measurements[a.index], measurements[b.index])
        return distances[key]

    # 优先以较好画面作为代表，避免多个相似但稍模糊的帧占满名额。
    for frame in sorted(eligible, key=lambda item: (-quality(item), item.index)):
        if all(distance(frame, other) > NEAR_DISTANCE for other in representatives):
            representatives.append(frame)
    selected = [representatives.pop(0)]
    span = max(frame.time_seconds for frame in frames) - min(frame.time_seconds for frame in frames)
    while representatives and len(selected) < limit:
        def score(frame):
            novelty = min(1.0, min(distance(frame, other) for other in selected) / 0.25)
            coverage = min(abs(frame.time_seconds - other.time_seconds) for other in selected) / max(span, 0.5)
            return (0.45 * quality(frame) / max(best, 0.001) + 0.40 * novelty + 0.15 * coverage, -frame.index)
        chosen = max(representatives, key=score)
        selected.append(chosen)
        representatives.remove(chosen)
    return sorted(frame.index for frame in selected)


def select_from_directory(frames, directory, limit=MAX_DETAIL_FRAMES):
    measurements = {}
    for frame in frames:
        path = directory / f"detail-{frame.index}.jpg"
        if path.is_file():
            with Image.open(path) as image:
                measurements[frame.index] = measure_frame(image)
    return select_detail_frames(frames, measurements, limit)

"""本地选帧启发式：质量、内容差异和时间覆盖；不识别语义、不访问模型。"""
from dataclasses import dataclass
import math

from PIL import Image, ImageChops, ImageFilter, ImageStat

SELECTION_VERSION = "quality-diversity-focus-v2"
MAX_DETAIL_FRAMES = 4
NEAR_DISTANCE = 0.03


@dataclass(frozen=True)
class FrameQuality:
    quality: float
    sharpness: float
    exposure: float
    contrast: float
    signature: bytes
    focus: float | None = None


def measure_frame(image: Image.Image) -> FrameQuality:
    """边缘量只是纹理/清晰度代理，不能保证区分纹理、噪声和运动模糊。"""
    rgb = image.convert("RGB")
    gray = rgb.convert("L")
    gray.thumbnail((256, 256))
    histogram = gray.histogram()
    pixels = gray.width * gray.height
    clipped = (sum(histogram[:9]) + sum(histogram[247:])) / pixels
    exposure = 1 - clipped
    contrast = min(1.0, ImageStat.Stat(gray).stddev[0] / 55)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    if min(edges.size) > 4:
        edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
    sharpness = 1 - math.exp(-ImageStat.Stat(edges).mean[0] / 12)
    # 白纸黑字也有大量极亮/极暗像素；有结构时不能简单判为曝光失败。
    exposure_factor = max(exposure, min(1.0, sharpness * 2) * contrast)
    quality = exposure_factor * (0.35 + 0.45 * sharpness + 0.20 * contrast)
    # 同时保留粗略颜色布局和边缘布局，不把它当作语义相似度。
    signature = rgb.resize((16, 16), Image.Resampling.BILINEAR).tobytes()
    signature += edges.resize((16, 16), Image.Resampling.BILINEAR).tobytes()
    # 高频残差区分宽而平滑的虚焦光斑与较细纹理；不是语义清晰度评分。
    residual = ImageChops.difference(gray, gray.filter(ImageFilter.GaussianBlur(1)))
    focus = 1 - math.exp(-ImageStat.Stat(residual).stddev[0] / 8)
    return FrameQuality(quality, sharpness, exposure, contrast, signature, focus)


def visual_distance(left: FrameQuality, right: FrameQuality) -> float:
    differences = [abs(a - b) for a, b in zip(left.signature, right.signature)]
    global_change = sum(differences) / (1024 * 255)
    # 兼顾局部变化，避免大背景不变时把移动的主体/局部新内容全部合并。
    tiles = [0] * 16
    for pixel in range(256):
        tile = (pixel // 16 // 4) * 4 + (pixel % 16 // 4)
        tiles[tile] += sum(differences[pixel * 3:pixel * 3 + 3])
    local_change = sum(sorted(tiles, reverse=True)[:4]) / (4 * 16 * 3 * 255)
    return 0.5 * global_change + 0.5 * local_change


def select_detail_frames(frames, measurements: dict[int, FrameQuality], limit=MAX_DETAIL_FRAMES):
    """只减少高清名额的重复；不删除概览图。名额是上限，不强行凑满。"""
    limit = max(0, min(MAX_DETAIL_FRAMES, limit))
    eligible = [frame for frame in frames if frame.index in measurements]
    if not eligible or not limit:
        return []
    quality = lambda frame: measurements[frame.index].quality
    best = max(quality(frame) for frame in eligible)
    # 只排除近乎全黑/全白且没有结构的帧，不能把正常的低纹理场景误删。
    usable = [frame for frame in eligible if measurements[frame.index].exposure >= 0.1
              or measurements[frame.index].sharpness >= 0.04]
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
            if metric.focus is None or metric.focus < 0.08 or current.focus >= metric.focus * 0.35:
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

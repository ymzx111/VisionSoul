"""本地画面质量与粗布局度量；不识别代码语义或文字。"""
from dataclasses import dataclass
import math
from PIL import Image, ImageChops, ImageFilter, ImageStat

@dataclass(frozen=True)
class FrameQuality:
    quality: float
    sharpness: float
    exposure: float
    contrast: float
    signature: bytes
    focus: float | None = None
    brightness: float | None = None


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
    brightness = ImageStat.Stat(gray).mean[0] / 255
    return FrameQuality(quality, sharpness, exposure, contrast, signature, focus, brightness)


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



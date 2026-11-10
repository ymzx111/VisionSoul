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
    text_score: float = 0
    text_focus: float = 0
    text_signature: bytes = b""


def measure_frame(image: Image.Image, *, text=False) -> FrameQuality:
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
    text_score, text_focus, text_signature = measure_small_structure(rgb) if text else (0, 0, b"")
    return FrameQuality(quality, sharpness, exposure, contrast, signature, focus, brightness,
                        text_score, text_focus, text_signature)


def measure_small_structure(image):
    """较高分辨率下检测局部细线/行结构，仅作文字候选代理，不进行OCR。"""
    gray = image.convert("L")
    gray.thumbnail((960, 960))
    residual = ImageChops.difference(gray, gray.filter(ImageFilter.GaussianBlur(0.8)))
    edges = gray.filter(ImageFilter.FIND_EDGES)
    scores, focuses = [], []
    for row in range(4):
        for column in range(4):
            left, top = column * gray.width // 4, row * gray.height // 4
            box = (left + 1, top + 1, (column + 1) * gray.width // 4 - 1, (row + 1) * gray.height // 4 - 1)
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            tile = edges.crop(box)
            histogram = tile.histogram()
            density = sum(histogram[32:]) / (tile.width * tile.height)
            # 少量细线优于纯色块；行投影有助于保留小字所在的局部，纹理也可能入选。
            row_structure = ImageStat.Stat(tile.resize((1, tile.height), Image.Resampling.BOX)).stddev[0] / 24
            scores.append(min(1.0, density / 0.18) * min(1.0, row_structure))
            focuses.append(1 - math.exp(-ImageStat.Stat(residual.crop(box)).stddev[0] / 10))
    score = sum(sorted(scores, reverse=True)[:4]) / max(1, min(4, len(scores)))
    fine_focus = sum(sorted(focuses, reverse=True)[:4]) / max(1, min(4, len(focuses)))
    return score, fine_focus, gray.resize((320, 180), Image.Resampling.BILINEAR).tobytes()


def text_distance(left: FrameQuality, right: FrameQuality) -> float:
    """局部页面变化补充粗布局距离，避免白底不变时抹掉新题目/新代码。"""
    coarse = visual_distance(left, right)
    if not left.text_signature or not right.text_signature:
        return coarse
    # 细网格留住局部运算符变化；像素差和缩放由Pillow完成，避免逐像素Python循环。
    difference = ImageChops.difference(Image.frombytes("L", (320, 180), left.text_signature),
                                      Image.frombytes("L", (320, 180), right.text_signature))
    strongest = sorted(difference.resize((40, 23), Image.Resampling.BOX).tobytes(), reverse=True)[:4]
    fine = (0.7 * strongest[0] + 0.3 * sum(strongest) / 4) / 255
    return max(coarse, fine)


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



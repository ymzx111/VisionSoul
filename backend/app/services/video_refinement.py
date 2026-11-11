"""在高清候选前后半秒内比较源帧，只在本机执行。"""
from io import BytesIO
import json

from PIL import Image, ImageChops, ImageFilter

from app.services.frame_quality import measure_frame
from app.services.video_details import adjustment_path, adjusted_path, detail_path, source_frames, source_image

WINDOW = 0.5
STEP = 0.125


def laplacian_variance(gray):
    """比较四个细节最丰富的局部块，保留有符号的二阶差分。"""
    width, height = gray.size
    variances = []
    for row in range(4):
        for column in range(4):
            x0, x1 = column * width // 4 + 1, (column + 1) * width // 4 - 1
            y0, y1 = row * height // 4 + 1, (row + 1) * height // 4 - 1
            neighbors = [gray.crop((x0 + dx, y0 + dy, x1 + dx, y1 + dy)).tobytes()
                         for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))]
            values = [4 * center - left - right - above - below
                      for center, left, right, above, below in zip(*neighbors)]
            count = len(values)
            variances.append(sum(v * v for v in values) / count - (sum(values) / count) ** 2)
    return sum(sorted(variances, reverse=True)[:4]) / 4


def local_difference(left, right):
    difference = ImageChops.difference(left, right)
    tiles = difference.resize((left.width // 8, round(left.height / 8)), Image.Resampling.BOX)
    return max(high for _, high in tiles.getextrema()) / 255


def matches_fine_detail(anchor, candidate):
    size = (640, 360)
    anchor = anchor.resize(size, Image.Resampling.LANCZOS)
    candidate = candidate.resize(size, Image.Resampling.LANCZOS)
    for radius in (0, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6):
        if local_difference(anchor, candidate.filter(ImageFilter.GaussianBlur(radius))) <= 0.045:
            return True
    return False


def choose_detail(samples, anchor_time):
    anchor = samples[anchor_time]
    anchor_quality = measure_frame(anchor, text=True)
    focus = anchor_quality.text_focus
    if focus >= 0.55:
        return None
    preview = anchor.resize((320, 180), Image.Resampling.LANCZOS)
    anchor_score = laplacian_variance(preview.convert("L"))
    if anchor_score < 3:
        return None
    better = []
    for direction in (-1, 1):
        times = sorted((time for time in samples if (time - anchor_time) * direction > 0),
                       key=lambda time: abs(time - anchor_time))
        for time in times:
            candidate = samples[time].resize(preview.size, Image.Resampling.LANCZOS)
            # 给清晰候选匹配几档虚焦再比较布局，遇到不同页面停止向该方向搜索。
            difference = min(local_difference(preview, candidate.filter(ImageFilter.GaussianBlur(radius)))
                             for radius in (0, 0.4, 0.6, 0.8, 1.0, 1.2, 1.6, 2.0))
            if difference > 0.025:
                break
            score = laplacian_variance(candidate.convert("L"))
            if score >= max(1.4 * anchor_score, anchor_score + 3):
                detail_focus = measure_frame(samples[time], text=True).text_focus
                if detail_focus >= max(focus + 0.05, 0.12):
                    better.append((score, time))
    if better:
        _, best_time = max(better, key=lambda row: (row[0], -abs(row[1] - anchor_time), -row[1]))
        # 当模糊使原帧无法区分两种清晰内容时，宁可保留原时间的画面。
        best_preview = samples[best_time].resize((640, 360), Image.Resampling.LANCZOS)
        agrees = all(local_difference(best_preview, samples[time].resize(best_preview.size, Image.Resampling.LANCZOS)) <= 0.025
                     for _, time in better if time != best_time)
        both_sides = any(row[1] < anchor_time for row in better) and any(row[1] > anchor_time for row in better)
        confirmed = matches_fine_detail(anchor, samples[best_time]) if focus >= 0.08 else both_sides
        if agrees and confirmed:
            return "nearby", best_time, samples[best_time]
    if 0.08 <= focus < 0.45 and anchor_score >= 2:
        enhanced = anchor.filter(ImageFilter.UnsharpMask(radius=1, percent=60, threshold=4))
        if ImageChops.difference(anchor, enhanced).getbbox():
            return "enhanced", anchor_time, enhanced
    return None


def refine_details(directory, output, targets):
    buckets = {target["index"]: {} for target in targets}
    last_time = max(target["time_seconds"] for target in targets) + WINDOW
    for timestamp, frame in source_frames(directory):
        if timestamp > last_time + 0.001:
            break
        destinations = []
        for target in targets:
            offset = timestamp - target["time_seconds"]
            if abs(offset) > WINDOW + 0.001 or timestamp > 30:
                continue
            # 每个时间桶留一张，精确的采样点独占一格。
            bucket = 0 if offset == 0 else (1 if offset > 0 else -1) * max(1, round(abs(offset) / STEP))
            images = buckets[target["index"]]
            if bucket not in images:
                destinations.append((images, bucket))
        if destinations:
            buffer = BytesIO()
            source_image(frame).save(buffer, "JPEG", quality=92)
            encoded = buffer.getvalue()
            for images, bucket in destinations:
                images[bucket] = (timestamp, encoded)

    for target in targets:
        index, anchor_time = target["index"], target["time_seconds"]
        images = buckets[index]
        if 0 not in images:
            raise ValueError("源帧与采样记录不匹配")
        detail_path(output, index).write_bytes(images[0][1])
        samples = {}
        for timestamp, encoded in images.values():
            with Image.open(BytesIO(encoded)) as image:
                samples[timestamp] = image.convert("RGB")
        selected = choose_detail(samples, anchor_time)
        if selected:
            kind, time, image = selected
            path = adjusted_path(output, index, kind)
            if kind == "nearby":
                path.write_bytes(next(encoded for timestamp, encoded in images.values() if timestamp == time))
            else:
                image.save(path, "JPEG", quality=92)
            adjustment_path(output, index).write_text(json.dumps({"kind": kind, "time_seconds": time}), encoding="utf-8")

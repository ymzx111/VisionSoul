"""统一采样协议，避免帧数上限在接口、抽帧和比对中漂移。"""
SAMPLE_INTERVAL_SECONDS = 1.0
SHORT_VIDEO_SECONDS = 12
NEW_MAX_SAMPLES = 31  # 30个整秒点，加最后一个不同的实际画面。
MAX_SAMPLES = 61  # 读取旧半秒视频的兼容容量；新上传单独限制为31。
MAX_DETAIL_FRAMES = 4
MAX_EVIDENCE_FRAMES = MAX_SAMPLES + MAX_DETAIL_FRAMES
FOCUS_INTERVAL_MS = 500
MAX_FOCUS_DURATION_MS = 5000
VIDEO_CODECS = frozenset({"h264", "hevc"})
VIDEO_PREVIEW_FILENAME = "preview-h264.mp4"
PIPELINE_VERSION = "video-v4-one-second-audio"
LEGACY_HALF_PIPELINE = "video-v3-half-second-exact-global-dedup"
LEGACY_PIPELINE = "video-v2-uniform-30-exact-dedup"

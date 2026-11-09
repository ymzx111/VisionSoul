"""统一采样协议，避免帧数上限在接口、抽帧和比对中漂移。"""
SAMPLE_INTERVAL_SECONDS = 0.5
MAX_SAMPLES = 61  # 30 秒的 60 个半秒点，加最后一个不同的实际画面。
PIPELINE_VERSION = "video-v3-half-second-exact-global-dedup"
LEGACY_PIPELINE = "video-v2-uniform-30-exact-dedup"

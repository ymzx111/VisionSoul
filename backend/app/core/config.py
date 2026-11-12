"""应用配置。

所有可调参数集中在这里。默认值就是开发阶段推荐值，
也可以不改代码，直接用环境变量或 backend/.env 覆盖。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.video_policy import NEW_MAX_SAMPLES


class Settings(BaseSettings):
    """项目配置项。

    环境变量名大小写不敏感，例如设置 APP_NAME=VisionSoul-Pro
    就会覆盖下面的 app_name。
    """

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 应用元信息（会显示在 Swagger 文档页上）----
    app_name: str = "VisionSoul"
    app_version: str = "0.1.0"
    app_description: str = (
        "VisionSoul —— 具备视觉感知与动态人格模拟能力的多模态 AI 交互系统。"
        "当前支持短视频观察、图片理解、固定伙伴人格和本机连续对话。"
    )

    # ---- 启动参数（记录用；实际监听地址以启动命令为准）----
    host: str = "127.0.0.1"
    port: int = 8000

    # 默认位置锚定 backend，避免在不同目录启动时把图片存到不同位置。
    image_storage_dir: Path = Path(__file__).resolve().parents[2] / "storage" / "images"
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1, le=20 * 1024 * 1024)
    image_max_pixels: int = Field(default=20_000_000, ge=1, le=40_000_000)
    image_max_dimension: int = Field(default=8192, ge=1, le=16384)

    video_storage_dir: Path = Path(__file__).resolve().parents[2] / "storage" / "videos"
    video_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    video_max_seconds: float = Field(default=30.0, ge=1, le=30)
    video_max_frames: int = Field(default=NEW_MAX_SAMPLES, ge=2, le=NEW_MAX_SAMPLES)
    video_decode_timeout_seconds: float = Field(default=45.0, ge=5, le=90)
    video_max_tokens: int = Field(default=1600, ge=512, le=2048)
    audio_enabled: bool = True
    audio_model_dir: Path = Path(__file__).resolve().parents[2] / "storage" / "models" / "faster-whisper-small"
    audio_timeout_seconds: float = Field(default=180, ge=10, le=300)
    audio_cpu_threads: int = Field(default=4, ge=1, le=8)

    # 密钥只由后端读取，不加入任何接口响应。
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: Literal["deepseek-flash"] = "deepseek-flash"
    deepseek_image_detail: Literal["low", "high"] = "low"
    deepseek_max_tokens: int = Field(default=512, ge=32, le=2048)
    deepseek_timeout_seconds: float = Field(default=45.0, ge=5.0, le=120.0)

    # 对话记录仅保存到本机；偏好提取和长期记忆在后续模块实现。
    chat_database_path: Path = Path(__file__).resolve().parents[2] / "storage" / "chat.sqlite3"
    chat_context_turns: int = Field(default=6, ge=1, le=12)
    chat_context_chars: int = Field(default=12000, ge=2000, le=24000)
    role_database_path: Path | None = None

    # 网页分析成功后的本机源码输出和 IDE 路径。
    local_source_workspace: Path = Path(__file__).resolve().parents[3] / "workspace"
    local_pycharm: Path | None = None
    local_visual_studio: Path | None = None

    @field_validator("video_max_frames", mode="before")
    @classmethod
    def upgrade_old_sampling_limit(cls, value):
        # 迁移既有30/61配置；自定义的小上限仍明确报错，不截断。
        return NEW_MAX_SAMPLES if str(value) in {"30", "61"} else value

    @property
    def roles_path(self) -> Path:
        return self.role_database_path or self.chat_database_path.with_name("roles.sqlite3")


@lru_cache
def get_settings() -> Settings:
    """返回全局唯一的配置对象（带缓存，避免重复解析 .env）。"""
    return Settings()

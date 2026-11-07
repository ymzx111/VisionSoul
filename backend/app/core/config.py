"""应用配置。

所有可调参数集中在这里。默认值就是开发阶段推荐值，
也可以不改代码，直接用环境变量或 backend/.env 覆盖。
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目配置项。

    环境变量名大小写不敏感，例如设置 APP_NAME=VisionSoul-Pro
    就会覆盖下面的 app_name。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 应用元信息（会显示在 Swagger 文档页上）----
    app_name: str = "VisionSoul"
    app_version: str = "0.1.0"
    app_description: str = (
        "VisionSoul —— 具备视觉感知与动态人格模拟能力的多模态 AI 交互系统。"
        "当前为第一阶段：仅提供后端基础框架，尚未接入模型能力。"
    )

    # ---- 启动参数（记录用；实际监听地址以启动命令为准）----
    host: str = "127.0.0.1"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """返回全局唯一的配置对象（带缓存，避免重复解析 .env）。"""
    return Settings()

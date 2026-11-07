"""健康检查路由。

只做一件事：告诉调用方「服务还活着」。
后续接入模型、数据库后，可以在这里追加依赖项检测。
"""

from datetime import datetime, timezone

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["系统"])

settings = get_settings()

# 进程启动时间，用于计算运行时长
_STARTED_AT = datetime.now(timezone.utc)


@router.get("/health", summary="健康检查")
def health_check() -> dict:
    """返回服务状态、版本与已运行时长。"""
    now = datetime.now(timezone.utc)
    return {
        "status": "ok",
        "project": settings.app_name,
        "version": settings.app_version,
        "uptime_seconds": round((now - _STARTED_AT).total_seconds(), 3),
        "timestamp": now.isoformat(),
    }

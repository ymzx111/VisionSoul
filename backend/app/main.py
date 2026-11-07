"""FastAPI 应用入口。

启动方式（务必在 backend 目录下执行）：
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app.api.routes import health, images, vision
from app.core.config import get_settings
from app.core.upload_limits import ImageUploadBodyLimit

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# 路由集中注册：以后新增模块，只需在这里 app.include_router(...)
app.include_router(health.router)
app.include_router(images.router)
app.include_router(vision.router)
app.add_middleware(ImageUploadBodyLimit, max_bytes=settings.upload_max_bytes + 65536)


@app.get("/", tags=["系统"], summary="项目信息")
def read_root() -> dict:
    """根路径：返回项目名称与运行状态。"""
    return {
        "project": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
    }

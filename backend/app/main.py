"""FastAPI 应用入口。

启动方式（务必在 backend 目录下执行）：
    uvicorn app.main:app --reload
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes import chat, docs, health, images, roles, videos, vision, workspace
from app.core.config import get_settings
from app.core.upload_limits import ImageUploadBodyLimit

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=settings.app_description,
    docs_url=None,
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# 路由集中注册：以后新增模块，只需在这里 app.include_router(...)
app.include_router(health.router)
app.include_router(images.router)
app.include_router(vision.router)
app.include_router(chat.router)
app.include_router(videos.router)
app.include_router(roles.router)
app.include_router(docs.router)
app.include_router(workspace.router)
app.mount("/docs-assets", StaticFiles(directory=Path(__file__).resolve().parent / "docs_assets"), name="docs-assets")
app.mount("/app/assets", StaticFiles(directory=workspace.FRONTEND_DIR / "assets", check_dir=False), name="workspace-assets")
app.add_middleware(ImageUploadBodyLimit, max_bytes=settings.upload_max_bytes + 65536,
                   video_max_bytes=settings.video_max_bytes + 65536)


class ProjectInfo(BaseModel):
    model_config = ConfigDict(title="项目信息")

    project: str = Field(title="项目名称", description="当前后端项目名称。")
    version: str = Field(title="项目版本", description="当前后端版本号。")
    status: str = Field(title="运行状态", description="running 表示服务正在运行。")
    docs: str = Field(title="文档地址", description="中文交互式接口文档的相对地址。")


@app.get("/", tags=["系统"], summary="项目信息", response_model=ProjectInfo, response_description="项目名称、版本和文档地址")
def read_root() -> dict:
    """根路径：返回项目名称与运行状态。"""
    return {
        "project": settings.app_name,
        "version": settings.app_version,
        "status": "running",
        "docs": "/docs",
    }

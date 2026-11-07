"""提供构建后的本机图片工作台，继续复用现有同源 API。"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

router = APIRouter()
FRONTEND_DIR = Path(__file__).resolve().parents[4] / "frontend" / "dist"


@router.get("/app", include_in_schema=False)
def workspace_redirect() -> RedirectResponse:
    return RedirectResponse("/app/")


@router.get("/app/", include_in_schema=False, response_model=None)
def image_workspace() -> FileResponse | HTMLResponse:
    entry = FRONTEND_DIR / "index.html"
    if not entry.is_file():
        return HTMLResponse(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>VisionSoul</title>'
            '<body><h1>图片工作台尚未构建</h1><p>请在项目 frontend 目录执行 npm install 和 npm run build，随后刷新本页。</p></body></html>',
            status_code=503,
        )
    return FileResponse(entry, headers={"Cache-Control": "no-store"})

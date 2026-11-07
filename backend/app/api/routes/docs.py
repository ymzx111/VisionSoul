"""中文 Swagger 操作界面，保留可直接执行的原始接口示例。"""

from fastapi import APIRouter, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/docs", include_in_schema=False, response_class=HTMLResponse)
def chinese_docs(request: Request) -> HTMLResponse:
    original = get_swagger_ui_html(
        openapi_url=request.app.openapi_url,
        title=f"{request.app.title} · 中文接口文档",
        swagger_ui_parameters={"displayRequestDuration": True, "defaultModelsExpandDepth": 0},
    ).body.decode("utf-8")
    original = original.replace("<html>", '<html lang="zh-CN">')
    original = original.replace("<head>", '<head><meta charset="utf-8">')
    original = original.replace("</head>", '<link rel="stylesheet" href="/docs-assets/zh-cn.css"></head>')
    guide = (
        '<aside class="vs-docs-guide" aria-label="操作提示">'
        '<strong>中文接口文档</strong>'
        '<p>使用顺序：上传图片 → 复制图片编号 → 提交图片理解。'
        '展开接口后，点击“试用接口”填写参数，再点击“发送请求”。</p>'
        '<p>GET 表示读取，POST 表示提交。字段旁有中文说明；'
        '请求示例中的 image_id 等名称用于程序通信，请保留原写法。</p></aside>'
    )
    original = original.replace('<div id="swagger-ui">', guide + '<div id="swagger-ui">')
    original = original.replace("</body>", '<script src="/docs-assets/zh-cn.js"></script></body>')
    return HTMLResponse(original, headers={"Cache-Control": "no-store"})

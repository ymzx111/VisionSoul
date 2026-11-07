"""在 multipart 解析前限制上传请求体，兼容没有 Content-Length 的请求。"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class ImageUploadBodyLimit:
    """先接收有大小上限的请求体，再交给 FastAPI 解析文件。"""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != "/api/images"
        ):
            await self.app(scope, receive, send)
            return

        async def reject() -> None:
            response = JSONResponse({"detail": "上传请求过大，请选择更小的图片。"}, status_code=413)
            await response(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    if int(value) > self.max_bytes:
                        await reject()
                        return
                except ValueError:
                    await JSONResponse({"detail": "无效的请求长度。"}, status_code=400)(scope, receive, send)
                    return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await reject()
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)

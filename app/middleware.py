"""上传请求体先受总量限制，再交给 multipart 解析器。"""

from uuid import uuid4
from starlette.responses import JSONResponse


class UploadBodyLimit:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/v1/resumes" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length", b"")
        oversized = content_length.isdigit() and int(content_length) > self.max_bytes
        body = bytearray()
        while not oversized:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            oversized = len(body) > self.max_bytes
            if not message.get("more_body", False):
                break
        if oversized:
            return await JSONResponse(
                {"code": "FILE_TOO_LARGE", "message": "上传请求超过文件大小限制", "request_id": uuid4().hex},
                status_code=413,
            )(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)

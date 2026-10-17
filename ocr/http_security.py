import hashlib
import hmac
import time
from collections import deque

from fastapi.responses import JSONResponse


class Guard:
    """Authenticate before multipart parsing; bound chunked and fixed-length bodies."""

    def __init__(self, app, token, max_bytes, rate=60):
        self.app = app
        self.token = token
        self.max_bytes = max_bytes + 64 * 1024
        self.rate = rate
        self.calls = deque()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        path = scope["path"]
        # Only /health and documentation endpoints are unauthenticated
        if path not in ("/health", "/docs", "/openapi.json", "/redoc"):
            if not self.token:
                return await JSONResponse({"code": "service_unconfigured"}, status_code=503)(
                    scope, receive, send
                )
            auth = headers.get(b"authorization", b"")
            expected = b"Bearer " + self.token.encode("utf-8")
            if not hmac.compare_digest(auth, expected):
                return await JSONResponse({"code": "unauthorized"}, status_code=401)(
                    scope, receive, send
                )
        if scope["method"] == "POST":
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.rate:
                return await JSONResponse({"code": "rate_limit"}, status_code=429)(
                    scope, receive, send
                )
            self.calls.append(now)
        try:
            if int(headers.get(b"content-length", b"0")) > self.max_bytes:
                return await JSONResponse({"code": "upload_limit"}, status_code=413)(
                    scope, receive, send
                )
        except ValueError:
            return await JSONResponse({"code": "invalid_length"}, status_code=400)(
                scope, receive, send
            )
        # Buffer at most the bounded body before Starlette sees it; no spool files on oversize.
        body = bytearray()
        if scope["method"] in ("POST", "PUT", "PATCH"):
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > self.max_bytes:
                    return await JSONResponse({"code": "upload_limit"}, status_code=413)(
                        scope, receive, send
                    )
                if not message.get("more_body", False):
                    break
            delivered = False

            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {
                        "type": "http.request",
                        "body": bytes(body),
                        "more_body": False,
                    }
                return await receive()

            return await self._invoke(scope, bounded_receive, send)
        return await self._invoke(scope, receive, send)

    async def _invoke(self, scope, receive, send):
        started = False

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, safe_send)
        except Exception:
            # Avoid leaking unexpected parser/provider exceptions into server tracebacks.
            if not started:
                await JSONResponse({"code": "internal_error"}, status_code=500)(
                    scope, receive, send
                )


def owner(request):
    return hashlib.sha256(request.headers.get("authorization", "").encode()).hexdigest()

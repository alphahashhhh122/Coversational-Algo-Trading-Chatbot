from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .telemetry import current_trace_context


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = logging.getLogger("iimc.request")

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _header(scope, "x-request-id") or f"req_{uuid.uuid4().hex}"
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                trace_context = current_trace_context()
                headers.append((b"x-request-id", request_id.encode()))
                if trace_context.trace_id:
                    headers.append(
                        (b"x-trace-id", trace_context.trace_id.encode())
                    )
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append(
                    (
                        b"referrer-policy",
                        b"strict-origin-when-cross-origin",
                    )
                )
                headers.append(
                    (
                        b"content-security-policy",
                        (
                            b"default-src 'self'; script-src 'self'; "
                            b"style-src 'self'; img-src 'self' data:; "
                            b"connect-src 'self'; frame-ancestors 'none'"
                        ),
                    )
                )
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            trace_context = current_trace_context()
            self.logger.info(
                "HTTP request completed",
                extra={
                    "event": "http_request",
                    "request_id": request_id,
                    "trace_id": trace_context.trace_id,
                    "span_id": trace_context.span_id,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round(
                        (time.perf_counter() - started) * 1000,
                        3,
                    ),
                },
            )


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_length = _header(scope, "content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > self.max_bytes
        ):
            response = JSONResponse(
                status_code=413,
                content={"detail": "Request body is too large"},
            )
            await response(scope, receive, send)
            return

        buffered: list[Message] = []
        consumed = 0
        while True:
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "Request body is too large"},
                    )
                    await response(scope, receive, send)
                    return
                buffered.append(message)
                if not message.get("more_body", False):
                    break
            else:
                buffered.append(message)
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.pop(0)
            # Once the buffered body is replayed, defer to the real transport.
            # Synthesising a disconnect here looks harmless for an ordinary
            # response, but a streaming response races its body against
            # ``receive()`` and treats a disconnect as "client left" — so a
            # fabricated one cancels the stream before it emits anything.
            return await receive()

        await self.app(scope, replay_receive, send)


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        requests_per_minute: int,
    ) -> None:
        self.app = app
        self.limit = requests_per_minute
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or scope["path"].startswith("/static")
            or scope["path"] in {"/", "/health", "/ready"}
        ):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_host = client[0] if client else "unknown"
        key = f"{client_host}:{scope['path']}"
        now = time.monotonic()
        with self.lock:
            events = self.events[key]
            while events and events[0] <= now - 60:
                events.popleft()
            if len(events) >= self.limit:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": "60"},
                )
                limited = True
            else:
                events.append(now)
                limited = False
        if limited:
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _header(scope: Scope, name: str) -> str | None:
    return Headers(scope=scope).get(name)

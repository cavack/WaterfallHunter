"""ASGI request-body limits enforced before framework parsing."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class RequestBodyLimitMiddleware:
    def __init__(self, app: Any, *, path: str, maximum_bytes: int):
        self.app = app
        self.path = path
        self.maximum_bytes = maximum_bytes

    async def __call__(
        self,
        scope: dict,
        receive: Callable[[], Awaitable[dict]],
        send: Callable[[dict], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http" or scope.get("path") != self.path:
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") == "http.disconnect":
                return
            body = bytes(message.get("body", b""))
            total += len(body)
            if total > self.maximum_bytes:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({
                    "type": "http.response.body",
                    "body": b'{"detail":"request body exceeds configured limit"}',
                })
                return
            chunks.append(body)
            more_body = bool(message.get("more_body", False))
        replayed = False

        async def bounded_receive() -> dict:
            nonlocal replayed
            await asyncio.sleep(0)
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, bounded_receive, send)

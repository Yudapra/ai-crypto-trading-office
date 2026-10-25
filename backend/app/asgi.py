"""Minimal ASGI adapter (no web framework needed). Run: `uvicorn app.asgi:app` from backend/ with
PYTHONPATH including ../trading-engine. All routing/auth/validation lives in `ApiCore`.

Only HTTP is served. The frontend polls `/api/state` and `/api/events?since=N`, so no WebSocket is needed.
CORS: only origins listed in CORS_ORIGINS get CORS headers; there is no wildcard.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qsl

from .api_core import MAX_BODY_BYTES, ApiCore
from .bootstrap import Office, build_office_from_env

log = logging.getLogger("office.asgi")
_CORS_HEADERS = "Content-Type, X-API-Key"


def _json(body: Dict[str, Any]) -> bytes:
    import json
    return json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")


class OfficeApp:
    def __init__(self, office_factory: Callable[[], Office]):
        self._factory = office_factory
        self._office: Optional[Office] = None
        self._lock = threading.Lock()

    @property
    def office(self) -> Office:
        with self._lock:
            if self._office is None:
                self._office = self._factory()
            return self._office

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self._lifespan(receive, send)
        if scope["type"] != "http":
            return  # WebSocket etc. are not served
        await self._http(scope, receive, send)

    async def _lifespan(self, receive, send):
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                try:
                    self.office.start()
                    await send({"type": "lifespan.startup.complete"})
                except Exception as exc:  # invalid config must stop the server
                    log.error("startup failed: %s", type(exc).__name__)
                    await send({"type": "lifespan.startup.failed", "message": f"startup failed: {exc}"})
                    return
            elif msg["type"] == "lifespan.shutdown":
                if self._office is not None:
                    self._office.stop()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _read_body(self, receive) -> Optional[bytes]:
        chunks, size = [], 0
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return None
            size += len(msg.get("body", b""))
            if size > MAX_BODY_BYTES + 1:
                return b"x" * (MAX_BODY_BYTES + 1)  # core answers 413; stop buffering
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"):
                return b"".join(chunks)

    async def _http(self, scope, receive, send):
        import asyncio
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        origin = headers.get("origin")
        allowed = set(self.office.settings.cors_origins)
        cors: Dict[str, str] = {}
        if origin and origin in allowed:
            cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
        if scope["method"] == "OPTIONS":
            extra = {**cors, "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                     "Access-Control-Allow-Headers": _CORS_HEADERS, "Access-Control-Max-Age": "600"} if cors else {}
            return await self._send(send, 204 if cors else 403, b"", extra)
        body = await self._read_body(receive)
        if body is None:
            return
        query = dict(parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True))
        # ApiCore is synchronous and may do blocking network calls: keep it off the event loop.
        resp = await asyncio.get_running_loop().run_in_executor(
            None, self.office.core.dispatch, scope["method"], scope["path"], query, headers, body)
        await self._send(send, resp.status, _json(resp.body), {**resp.headers, **cors})

    @staticmethod
    async def _send(send, status: int, body: bytes, headers: Dict[str, str]):
        hdr = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        hdr += [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": status, "headers": hdr})
        await send({"type": "http.response.body", "body": body})


app = OfficeApp(build_office_from_env)

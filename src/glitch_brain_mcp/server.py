"""Streamable-HTTP MCP server exposing the brain."""
from __future__ import annotations

import contextlib
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .auth import Principal, resolve_token
from . import memory as mem

# Context var: per-request authenticated principal.
import contextvars
_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "principal", default=None
)


def _require_principal() -> Principal:
    p = _principal.get()
    if p is None:
        raise PermissionError("unauthenticated")
    return p


mcp = FastMCP("glitch-brain")


@mcp.tool()
async def remember(
    content: str,
    kind: str,
    scope: str = "agent",
    key: str | None = None,
    agent_sku: str | None = None,
    metadata: dict[str, Any] | None = None,
    ttl: str | None = None,
) -> dict[str, Any]:
    """Store a memory. scope = 'agent' | 'shared' | 'global'."""
    return await mem.remember(
        _require_principal(),
        content=content, kind=kind, scope=scope, key=key,
        agent_sku=agent_sku, metadata=metadata, ttl=ttl,
    )


@mcp.tool()
async def recall(
    kind: str | None = None,
    key: str | None = None,
    agent_sku: str | None = None,
    include_shared: bool = True,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch recent memories visible to the caller."""
    return await mem.recall(
        _require_principal(),
        kind=kind, key=key, agent_sku=agent_sku,
        include_shared=include_shared, limit=limit,
    )


@mcp.tool()
async def search(
    query: str,
    agent_sku: str | None = None,
    include_shared: bool = True,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Trigram similarity search over memory content."""
    return await mem.search(
        _require_principal(),
        query=query, agent_sku=agent_sku,
        include_shared=include_shared, limit=limit,
    )


@mcp.tool()
async def forget(memory_id: int) -> dict[str, bool]:
    """Delete a memory by id (must belong to caller's brand/agent scope)."""
    ok = await mem.forget(_require_principal(), memory_id=memory_id)
    return {"deleted": ok}


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.lower().startswith("bearer ") else ""
        p = await resolve_token(token)
        if not p:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        tok = _principal.set(p)
        try:
            return await call_next(request)
        finally:
            _principal.reset(tok)


async def healthz(_: Request):
    return JSONResponse({"ok": True})


def build_app() -> Starlette:
    session_manager = mcp.session_manager  # FastMCP wires this up

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    return Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz),
            Mount("/mcp", app=mcp.streamable_http_app()),
        ],
        middleware=[Middleware(BearerAuthMiddleware)],
        lifespan=lifespan,
    )

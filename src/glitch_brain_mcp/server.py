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
from . import brain as br

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


# -------- Brain layer: activity + state + briefing --------

@mcp.tool()
async def append_activity(
    action: str,
    summary: str,
    subject: str | None = None,
    payload: dict[str, Any] | None = None,
    agent_sku: str | None = None,
) -> dict[str, Any]:
    """Log what this agent just did. Siblings see it via briefing/subscribe."""
    return await br.append_activity(
        _require_principal(),
        action=action, summary=summary, subject=subject,
        payload=payload, agent_sku=agent_sku,
    )


@mcp.tool()
async def recent_activity(
    agent_sku: str | None = None,
    exclude_self: bool = False,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Recent activity entries for the brand (optionally filtered to one agent)."""
    return await br.recent_activity(
        _require_principal(),
        agent_sku=agent_sku, exclude_self=exclude_self, limit=limit,
    )


@mcp.tool()
async def set_state(
    current_focus: str | None = None,
    blockers: str | None = None,
    next_step: str | None = None,
    agent_sku: str | None = None,
) -> dict[str, Any]:
    """Publish what this agent is currently working on. Fields are merged, not overwritten with NULL."""
    return await br.set_state(
        _require_principal(),
        current_focus=current_focus, blockers=blockers,
        next_step=next_step, agent_sku=agent_sku,
    )


@mcp.tool()
async def team_state() -> list[dict[str, Any]]:
    """What every enabled agent on this brand is currently up to."""
    return await br.team_state(_require_principal())


@mcp.tool()
async def briefing(
    activity_limit: int = 10,
    memory_limit: int = 5,
) -> dict[str, Any]:
    """One-shot 'what is the team doing': sibling states + recent sibling activity + shared memories. Call at the start of every run."""
    return await br.briefing(
        _require_principal(),
        activity_limit=activity_limit, memory_limit=memory_limit,
    )


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
    # Build the streamable-HTTP app first; this lazily creates the session
    # manager, which we then drive from our lifespan.
    streamable_app = mcp.streamable_http_app()
    session_manager = mcp.session_manager

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    return Starlette(
        debug=False,
        routes=[
            Route("/healthz", healthz),
            Mount("/mcp", app=streamable_app),
        ],
        middleware=[Middleware(BearerAuthMiddleware)],
        lifespan=lifespan,
    )

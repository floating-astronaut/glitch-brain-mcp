"""Activity log, agent state, briefing, and live subscribe."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from .auth import Principal
from .db import get_pool
from .memory import AuthzError, _assert_agent_allowed
from .config import settings


def _channel(brand_id: str) -> str:
    return "brain_" + brand_id.replace("-", "_")


def _effective_agent(p: Principal, requested: str | None) -> str:
    target = p.agent_sku or requested
    if target is None:
        raise AuthzError("agent_sku is required (token is brand-wide; pass one explicitly)")
    if p.agent_sku and requested and requested != p.agent_sku:
        raise AuthzError(f"token scoped to {p.agent_sku}; cannot act as {requested}")
    return target


# ---------- activity ----------

async def append_activity(
    p: Principal, *, action: str, summary: str,
    subject: str | None = None, payload: dict[str, Any] | None = None,
    agent_sku: str | None = None,
) -> dict[str, Any]:
    target = _effective_agent(p, agent_sku)
    await _assert_agent_allowed(p.brand_id, target)
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO activity (brand_id, agent_sku, action, subject, summary, payload)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb)
        RETURNING id, at
        """,
        p.brand_id, target, action, subject, summary, json.dumps(payload or {}),
    )
    return {"id": row["id"], "at": row["at"].isoformat()}


async def recent_activity(
    p: Principal, *, agent_sku: str | None = None,
    exclude_self: bool = False, limit: int = 20,
) -> list[dict[str, Any]]:
    pool = await get_pool()
    conds = ["brand_id = $1"]
    args: list[Any] = [p.brand_id]
    if agent_sku:
        args.append(agent_sku)
        conds.append(f"agent_sku = ${len(args)}")
    elif exclude_self and p.agent_sku:
        args.append(p.agent_sku)
        conds.append(f"agent_sku <> ${len(args)}")
    args.append(limit)
    sql = (
        "SELECT id, agent_sku, action, subject, summary, payload, at "
        "FROM activity WHERE " + " AND ".join(conds)
        + f" ORDER BY at DESC LIMIT ${len(args)}"
    )
    rows = await pool.fetch(sql, *args)
    return [dict(r) | {"at": r["at"].isoformat()} for r in rows]


# ---------- agent state ----------

async def set_state(
    p: Principal, *, current_focus: str | None = None,
    blockers: str | None = None, next_step: str | None = None,
    agent_sku: str | None = None,
) -> dict[str, Any]:
    target = _effective_agent(p, agent_sku)
    await _assert_agent_allowed(p.brand_id, target)
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO agent_state (brand_id, agent_sku, current_focus, blockers, next_step, updated_at)
        VALUES ($1,$2,$3,$4,$5, now())
        ON CONFLICT (brand_id, agent_sku) DO UPDATE
          SET current_focus = COALESCE(EXCLUDED.current_focus, agent_state.current_focus),
              blockers      = COALESCE(EXCLUDED.blockers,      agent_state.blockers),
              next_step     = COALESCE(EXCLUDED.next_step,     agent_state.next_step),
              updated_at    = now()
        RETURNING current_focus, blockers, next_step, updated_at
        """,
        p.brand_id, target, current_focus, blockers, next_step,
    )
    return {"agent_sku": target, **{k: v for k, v in row.items() if k != "updated_at"},
            "updated_at": row["updated_at"].isoformat()}


async def team_state(p: Principal) -> list[dict[str, Any]]:
    """Every enabled agent for the brand, with its current state (or null)."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT ba.agent_sku, a.name,
               s.current_focus, s.blockers, s.next_step, s.updated_at
        FROM brand_agents ba
        JOIN agents a ON a.agent_sku = ba.agent_sku
        LEFT JOIN agent_state s
          ON s.brand_id = ba.brand_id AND s.agent_sku = ba.agent_sku
        WHERE ba.brand_id = $1 AND ba.enabled
        ORDER BY ba.agent_sku
        """,
        p.brand_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("updated_at"):
            d["updated_at"] = d["updated_at"].isoformat()
        out.append(d)
    return out


# ---------- briefing ----------

async def briefing(
    p: Principal, *, activity_limit: int = 10, memory_limit: int = 5,
) -> dict[str, Any]:
    """One call: what siblings are doing + their recent activity + shared memories."""
    pool = await get_pool()
    states = await team_state(p)

    # Recent activity from siblings (exclude self if token is agent-scoped)
    sib_conds = ["brand_id = $1"]
    args: list[Any] = [p.brand_id]
    if p.agent_sku:
        args.append(p.agent_sku)
        sib_conds.append(f"agent_sku <> ${len(args)}")
    args.append(activity_limit)
    sib_sql = (
        "SELECT agent_sku, action, subject, summary, at FROM activity "
        "WHERE " + " AND ".join(sib_conds)
        + f" ORDER BY at DESC LIMIT ${len(args)}"
    )
    sib_rows = await pool.fetch(sib_sql, *args)

    shared_rows = await pool.fetch(
        """
        SELECT id, agent_sku, kind, key, content, updated_at
        FROM memories
        WHERE brand_id = $1 AND scope IN ('global','shared')
              AND (ttl IS NULL OR ttl > now())
        ORDER BY updated_at DESC LIMIT $2
        """,
        p.brand_id, memory_limit,
    )

    return {
        "brand_id": p.brand_id,
        "viewer_agent_sku": p.agent_sku,
        "team": states,
        "sibling_activity": [
            dict(r) | {"at": r["at"].isoformat()} for r in sib_rows
        ],
        "shared_memories": [
            dict(r) | {"updated_at": r["updated_at"].isoformat()} for r in shared_rows
        ],
    }


# ---------- live subscribe (LISTEN/NOTIFY) ----------

async def subscribe(p: Principal) -> AsyncIterator[dict[str, Any]]:
    """Async generator yielding live brain events for the caller's brand."""
    import asyncpg
    conn: asyncpg.Connection = await asyncpg.connect(settings.database_url)
    queue: asyncio.Queue[str] = asyncio.Queue()

    def _on_notify(_c, _pid, _chan, payload):
        queue.put_nowait(payload)

    chan = _channel(p.brand_id)
    await conn.add_listener(chan, _on_notify)
    try:
        while True:
            payload = await queue.get()
            evt = json.loads(payload)
            # If token is agent-scoped, suppress this agent's own echoes.
            if p.agent_sku and evt.get("agent_sku") == p.agent_sku:
                continue
            yield evt
    finally:
        await conn.remove_listener(chan, _on_notify)
        await conn.close()

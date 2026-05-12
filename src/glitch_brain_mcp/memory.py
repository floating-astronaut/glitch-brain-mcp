from typing import Any
from .auth import Principal
from .db import get_pool


class AuthzError(Exception):
    pass


async def _assert_agent_allowed(brand_id: str, agent_sku: str | None) -> None:
    if agent_sku is None:
        return
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT enabled FROM brand_agents WHERE brand_id=$1 AND agent_sku=$2",
        brand_id, agent_sku,
    )
    if not row or not row["enabled"]:
        raise AuthzError(f"agent {agent_sku} is not enabled for brand {brand_id}")


def _resolve_target(p: Principal, requested_agent: str | None, scope: str) -> str | None:
    """Pick which agent_sku to write under, honoring token scope."""
    if scope in ("global", "shared"):
        return None
    # scope == 'agent'
    target = requested_agent or p.agent_sku
    if target is None:
        raise AuthzError("scope='agent' requires an agent_sku (token is brand-wide)")
    if p.agent_sku is not None and target != p.agent_sku:
        raise AuthzError(
            f"token is scoped to {p.agent_sku}; cannot write as {target}"
        )
    return target


async def remember(
    p: Principal,
    *,
    content: str,
    kind: str,
    scope: str = "agent",
    key: str | None = None,
    agent_sku: str | None = None,
    metadata: dict[str, Any] | None = None,
    ttl: str | None = None,
) -> dict[str, Any]:
    if scope not in ("global", "shared", "agent"):
        raise ValueError("scope must be global|shared|agent")
    target = _resolve_target(p, agent_sku, scope)
    await _assert_agent_allowed(p.brand_id, target)
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO memories (brand_id, agent_sku, scope, kind, key, content, metadata, ttl)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::timestamptz)
        ON CONFLICT (brand_id, agent_sku, scope, kind, key)
        DO UPDATE SET content = EXCLUDED.content,
                      metadata = EXCLUDED.metadata,
                      ttl = EXCLUDED.ttl,
                      updated_at = now()
        RETURNING id, created_at, updated_at
        """,
        p.brand_id, target, scope, kind, key, content,
        __import__("json").dumps(metadata or {}), ttl,
    )
    return {"id": row["id"], "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat()}


async def recall(
    p: Principal,
    *,
    kind: str | None = None,
    key: str | None = None,
    agent_sku: str | None = None,
    include_shared: bool = True,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch memories visible to the caller."""
    pool = await get_pool()
    # Effective agent: token-scoped agent takes precedence; explicit override only
    # allowed if token is brand-wide.
    effective_agent = p.agent_sku if p.agent_sku else agent_sku

    conds = ["brand_id = $1", "(ttl IS NULL OR ttl > now())"]
    args: list[Any] = [p.brand_id]

    if effective_agent:
        scope_clause = "(scope = 'agent' AND agent_sku = $%d)" % (len(args) + 1)
        args.append(effective_agent)
        if include_shared:
            scope_clause = f"({scope_clause} OR scope IN ('global','shared'))"
        conds.append(scope_clause)
    else:
        # Brand-wide token with no agent filter: see everything for the brand.
        pass

    if kind:
        args.append(kind)
        conds.append(f"kind = ${len(args)}")
    if key:
        args.append(key)
        conds.append(f"key = ${len(args)}")

    args.append(limit)
    sql = (
        "SELECT id, agent_sku, scope, kind, key, content, metadata, "
        "created_at, updated_at FROM memories WHERE "
        + " AND ".join(conds)
        + f" ORDER BY updated_at DESC LIMIT ${len(args)}"
    )
    rows = await pool.fetch(sql, *args)
    return [dict(r) | {
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    } for r in rows]


async def search(
    p: Principal,
    *,
    query: str,
    agent_sku: str | None = None,
    include_shared: bool = True,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Trigram similarity search over content."""
    pool = await get_pool()
    effective_agent = p.agent_sku if p.agent_sku else agent_sku
    conds = ["brand_id = $1", "(ttl IS NULL OR ttl > now())", "content % $2"]
    args: list[Any] = [p.brand_id, query]
    if effective_agent:
        args.append(effective_agent)
        scope = f"(scope='agent' AND agent_sku=${len(args)})"
        if include_shared:
            scope = f"({scope} OR scope IN ('global','shared'))"
        conds.append(scope)
    sql = (
        "SELECT id, agent_sku, scope, kind, key, content, metadata, "
        "similarity(content, $2) AS score "
        "FROM memories WHERE " + " AND ".join(conds)
        + f" ORDER BY score DESC LIMIT {int(limit)}"
    )
    rows = await pool.fetch(sql, *args)
    return [dict(r) for r in rows]


async def forget(p: Principal, *, memory_id: int) -> bool:
    pool = await get_pool()
    # Ensure caller owns this memory (same brand; if agent-scoped token, same agent or shared).
    where = "id = $1 AND brand_id = $2"
    args: list[Any] = [memory_id, p.brand_id]
    if p.agent_sku:
        where += " AND (agent_sku = $3 OR scope IN ('global','shared'))"
        args.append(p.agent_sku)
    res = await pool.execute(f"DELETE FROM memories WHERE {where}", *args)
    return res.endswith(" 1")

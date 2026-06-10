import asyncio
import click
import uvicorn

from .auth import hash_token, mint_token
from .config import settings
from .db import get_pool


@click.group()
def main():
    """glitch-brain-mcp admin CLI."""


@main.command()
def serve():
    """Run the streamable-HTTP MCP server."""
    from .server import build_app
    uvicorn.run(build_app(), host=settings.host, port=settings.port,
                log_level=settings.log_level)


@main.group()
def brands():
    """Manage brands."""


@brands.command("add")
@click.argument("brand_id")
@click.option("--name", required=True)
def brands_add(brand_id, name):
    async def _run():
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO brands(brand_id, display_name) VALUES ($1,$2) "
            "ON CONFLICT DO NOTHING", brand_id, name,
        )
    asyncio.run(_run())
    click.echo(f"brand {brand_id} ready")


@brands.command("enable-agent")
@click.argument("brand_id")
@click.argument("agent_sku")
def brands_enable(brand_id, agent_sku):
    async def _run():
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO brand_agents(brand_id, agent_sku) VALUES ($1,$2) "
            "ON CONFLICT (brand_id, agent_sku) DO UPDATE SET enabled=true",
            brand_id, agent_sku,
        )
    asyncio.run(_run())
    click.echo(f"{brand_id} ← {agent_sku} enabled")


@main.group()
def tokens():
    """Manage API tokens."""


@tokens.command("issue")
@click.option("--brand", required=True)
@click.option("--agent", default=None, help="SKU; omit for brand-wide token")
@click.option("--label", default=None)
def tokens_issue(brand, agent, label):
    plain = mint_token()
    async def _run():
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO api_tokens(token_hash, brand_id, agent_sku, label) "
            "VALUES ($1,$2,$3,$4)",
            hash_token(plain), brand, agent, label,
        )
    asyncio.run(_run())
    click.echo(plain)
    click.echo("(store this — it is not recoverable)", err=True)


@tokens.command("revoke")
@click.argument("token")
def tokens_revoke(token):
    async def _run():
        pool = await get_pool()
        await pool.execute(
            "UPDATE api_tokens SET revoked_at=now() WHERE token_hash=$1",
            hash_token(token),
        )
    asyncio.run(_run())
    click.echo("revoked")


@main.command("embed-backfill")
@click.option("--batch", default=64, show_default=True)
def embed_backfill(batch):
    """Embed memories that have no embedding yet (local model, no API)."""
    from . import embeddings

    async def _run():
        pool = await get_pool()
        total = 0
        while True:
            rows = await pool.fetch(
                "SELECT id, content FROM memories WHERE embedding IS NULL "
                "AND (ttl IS NULL OR ttl > now()) ORDER BY id LIMIT $1", batch,
            )
            if not rows:
                break
            for r in rows:
                vec = await embeddings.embed(r["content"])
                if vec is None:
                    click.echo("embedder unavailable; aborting", err=True)
                    return total
                await pool.execute(
                    "UPDATE memories SET embedding = $1::vector WHERE id = $2",
                    embeddings.vec_literal(vec), r["id"],
                )
                total += 1
        return total

    n = asyncio.run(_run())
    click.echo(f"embedded {n} memories")


if __name__ == "__main__":
    main()

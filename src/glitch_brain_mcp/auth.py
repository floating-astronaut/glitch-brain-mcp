import hashlib
import secrets
from dataclasses import dataclass
from .db import get_pool


@dataclass
class Principal:
    brand_id: str
    agent_sku: str | None  # None = brand-wide token


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def mint_token() -> str:
    return "gbm_" + secrets.token_urlsafe(32)


async def resolve_token(plain: str) -> Principal | None:
    if not plain:
        return None
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT brand_id, agent_sku FROM api_tokens "
        "WHERE token_hash = $1 AND revoked_at IS NULL",
        hash_token(plain),
    )
    if not row:
        return None
    return Principal(brand_id=row["brand_id"], agent_sku=row["agent_sku"])

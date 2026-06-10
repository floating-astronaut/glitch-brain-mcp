"""Local embedding layer: fastembed (ONNX) running all-MiniLM-L6-v2, 384d.

Local-first by design — no API calls, nothing leaves the box. Best-effort:
if fastembed is missing or the model fails to load, embed() returns None
and search degrades to trigram-only.
"""
from __future__ import annotations

import asyncio

import structlog

from .config import settings

log = structlog.get_logger()

_model = None
_model_failed = False
_lock = asyncio.Lock()


def _load_model():
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    try:
        from fastembed import TextEmbedding
        _model = TextEmbedding(
            model_name=settings.embedding_model,
            cache_dir=settings.embedding_cache_dir,
        )
        log.info("embeddings.loaded", model=settings.embedding_model)
    except Exception as exc:
        _model_failed = True
        log.warning("embeddings.unavailable", error=str(exc))
    return _model


def _embed_sync(text: str) -> list[float] | None:
    model = _load_model()
    if model is None:
        return None
    vec = next(iter(model.embed([text])))
    return vec.tolist()


async def embed(text: str) -> list[float] | None:
    """Embed one text off the event loop. None when disabled/unavailable."""
    if not settings.embeddings_enabled:
        return None
    # Single in-flight embed: the ONNX session is not thread-safe to share
    # and this also prevents a duplicate model load on first concurrent use.
    async with _lock:
        return await asyncio.to_thread(_embed_sync, text)


def vec_literal(vec: list[float]) -> str:
    """pgvector input literal (asyncpg has no native vector codec here)."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"

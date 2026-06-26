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


_bedrock = None


def _bedrock_client():
    global _bedrock
    if _bedrock is None:
        import boto3
        _bedrock = boto3.client("bedrock-runtime", region_name=settings.bedrock_region)
    return _bedrock


def _embed_bedrock_sync(text: str) -> list[float] | None:
    """Amazon Bedrock Titan Text Embeddings V2 (instance-role creds)."""
    import json
    try:
        body = json.dumps({"inputText": text[:8000],
                           "dimensions": settings.bedrock_embed_dim,
                           "normalize": True})
        r = _bedrock_client().invoke_model(modelId=settings.bedrock_embed_model, body=body)
        return json.loads(r["body"].read()).get("embedding")
    except Exception as exc:  # noqa: BLE001 — degrade to trigram search
        log.warning("embeddings.bedrock_failed", error=str(exc))
        return None


async def embed(text: str) -> list[float] | None:
    """Embed one text off the event loop. None when disabled/unavailable."""
    if not settings.embeddings_enabled:
        return None
    if settings.platform_llm_via_bedrock:
        # Titan is stateless/thread-safe; no lock needed.
        return await asyncio.to_thread(_embed_bedrock_sync, text)
    # Single in-flight embed: the ONNX session is not thread-safe to share
    # and this also prevents a duplicate model load on first concurrent use.
    async with _lock:
        return await asyncio.to_thread(_embed_sync, text)


def vec_literal(vec: list[float]) -> str:
    """pgvector input literal (asyncpg has no native vector codec here)."""
    return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"

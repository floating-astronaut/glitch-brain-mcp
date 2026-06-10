-- 003_semantic_search.sql — local-first semantic search (hybrid trigram+vector RRF).
--
-- The embedding column was reserved as vector(1536) for a hosted embedder
-- that never shipped. We embed locally with all-MiniLM-L6-v2 (384d) instead.
-- Verified 2026-06-10 before this migration: 1 row total, 0 embeddings —
-- the type swap drops nothing.

ALTER TABLE memories ALTER COLUMN embedding TYPE vector(384) USING NULL;

CREATE INDEX IF NOT EXISTS memories_embedding_hnsw_idx
  ON memories USING hnsw (embedding vector_cosine_ops);

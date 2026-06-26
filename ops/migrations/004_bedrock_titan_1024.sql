-- 004 — move memories.embedding from local MiniLM 384d to Amazon Bedrock
-- Titan Text Embeddings V2 (1024d). Policy: all internal AI on AWS credits.
-- Apply with BRAIN_PLATFORM_LLM_VIA_BEDROCK=true, then re-embed
-- (cli embed-missing, or memory.embed on next write). NULLs existing vectors
-- (384d cannot be reused at 1024d); trigram search covers the gap.
BEGIN;
DROP INDEX IF EXISTS memories_embedding_hnsw_idx;
ALTER TABLE memories
    ALTER COLUMN embedding TYPE vector(1024) USING NULL::vector(1024);
CREATE INDEX memories_embedding_hnsw_idx
    ON memories USING hnsw (embedding vector_cosine_ops);
COMMIT;

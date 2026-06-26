from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BRAIN_", extra="ignore")

    database_url: str = "postgresql://glitch_brain@/glitch_brain"
    host: str = "127.0.0.1"
    port: int = 3107
    log_level: str = "info"

    # Local semantic search (fastembed/ONNX). Dim must match the
    # memories.embedding column — see ops/migrations/003_semantic_search.sql.
    embeddings_enabled: bool = True
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_cache_dir: str = ".fastembed_cache"

    # Amazon Bedrock Titan embeddings (policy: internal AI on AWS credits).
    # When true, embed() uses Titan (1024d) instead of local MiniLM (384d).
    # Dim must match the memories.embedding column (ops/migrations/004).
    platform_llm_via_bedrock: bool = False
    bedrock_region: str = "us-east-2"
    bedrock_embed_model: str = "amazon.titan-embed-text-v2:0"
    bedrock_embed_dim: int = 1024


settings = Settings()

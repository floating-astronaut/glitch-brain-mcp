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


settings = Settings()

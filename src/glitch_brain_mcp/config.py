from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BRAIN_", extra="ignore")

    database_url: str = "postgresql://glitch_brain@/glitch_brain"
    host: str = "127.0.0.1"
    port: int = 3107
    log_level: str = "info"


settings = Settings()

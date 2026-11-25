"""Settings, read from environment variables (see .env.example). Secrets never live in code."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database (Supabase / any Postgres with the pgvector extension)
    database_url: str = "postgresql://herevo:herevo@localhost:5432/herevo"

    # Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-5-5"
    claude_max_tokens: int = 700

    # Embeddings: "voyage" in production, "hash" for offline tests and local dev
    embeddings_provider: str = "hash"
    voyage_api_key: str = ""
    voyage_model: str = "voyage-4"
    embedding_dim: int = 1024

    # Retrieval
    top_k: int = 5

    # Telegram
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = "change-me"
    helper_chat_id: str = ""  # Telegram chat id of the trained human helper (or a helpers' group)

    # Speech (Google Cloud Speech-to-Text + Text-to-Speech, restricted API key)
    google_api_key: str = ""
    voice_replies: bool = True  # also answer voice notes with a voice note
    max_voice_seconds: int = 60

    # Privacy: salt for hashing user ids (never store raw phone numbers or Telegram ids)
    id_salt: str = "change-me-too"

    default_country: str = "BD"
    packs_dir: str = "../data/packs"


@lru_cache
def get_settings() -> Settings:
    return Settings()

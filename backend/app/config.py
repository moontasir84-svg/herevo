"""Settings, read from environment variables (see .env.example). Secrets never live in code."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Providers whose free tier may use submitted content to improve their products. They are allowed in
# DATA_MODE=test only, with made-up messages; never with real women's data.
FREE_TIER_PROVIDERS = {"gemini"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "test": building and demoing with made-up messages; users are told not to share real information.
    # "live": real women. Only providers that do not train on our data are allowed.
    data_mode: str = "test"

    # Database (Supabase / any Postgres with the pgvector extension)
    database_url: str = "postgresql://herevo:herevo@localhost:5432/herevo"

    # Which model answers: "claude" (live and test) or "gemini" (free tier, test only)
    llm_provider: str = "claude"

    # Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-5-5"
    claude_max_tokens: int = 700

    # Gemini free tier (Google AI Studio key) - test mode only
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    # Tried in order when the main model is overloaded, rate-limited or retired.
    gemini_backup_models: str = "gemini-flash-lite-latest,gemini-2.5-flash,gemini-2.5-flash-lite"

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

    # Speech: "google" (Cloud Speech-to-Text + Text-to-Speech, needs billing) or "gemini" (free tier,
    # transcription only, test mode only)
    speech_provider: str = "google"
    google_api_key: str = ""
    voice_replies: bool = True  # also answer voice notes with a voice note (google only)
    max_voice_seconds: int = 60

    # Privacy: salt for hashing user ids (never store raw phone numbers or Telegram ids)
    id_salt: str = "change-me-too"

    default_country: str = "BD"
    packs_dir: str = "../data/packs"

    @property
    def is_test(self) -> bool:
        return self.data_mode != "live"


class UnsafeConfiguration(RuntimeError):
    pass


def check_safe(s: "Settings") -> None:
    """Refuse to run real users through a provider that may train on their messages."""
    if s.data_mode not in ("test", "live"):
        raise UnsafeConfiguration("DATA_MODE must be 'test' or 'live'")
    if s.data_mode == "live":
        bad = {s.llm_provider, s.speech_provider} & FREE_TIER_PROVIDERS
        if bad:
            raise UnsafeConfiguration(
                f"DATA_MODE=live cannot use free-tier provider(s) {sorted(bad)}: their terms allow using submitted "
                "content to improve their products. Use LLM_PROVIDER=claude and SPEECH_PROVIDER=google for real users.")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    check_safe(s)
    return s

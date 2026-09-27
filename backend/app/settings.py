"""Process-wide settings, read from environment / ``.env``.

Secrets (API keys) live only here, on the server. The frontend never sees them.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api"
    # Sent as HTTP-Referer / X-Title so runs show up nicely in the OpenRouter dashboard.
    openrouter_app_url: str = "https://github.com/all0b0y/InvJ"
    openrouter_app_name: str = "InvJ"

    binance_base_url: str = "https://data-api.binance.vision"

    db_path: Path = BACKEND_DIR / "data" / "invj.db"
    csv_dir: Path = BACKEND_DIR / "data" / "csv"
    presets_dir: Path = BACKEND_DIR / "presets"
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    http_timeout_s: float = 60.0
    http_max_retries: int = 3


settings = Settings()

"""Application settings.

BPS statistics and publication embeddings share one Postgres database.
Statistical observations use the tables in ``tilik_agent.ingestion.models``.
Publication chunks stay in the LangChain pgvector tables ``langchain_pg_collection``
and ``langchain_pg_embedding``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment or a local ``.env`` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bps_api_key: str = ""
    database_url: str = "postgresql+psycopg://tilik:tilik@localhost:5432/tilik"
    bps_raw_dir: Path = Path("data/bps/raw")
    bps_domain: str = "0000"
    bps_subcat: int = 514


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()

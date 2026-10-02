"""Application settings.

BPS statistics and publication embeddings share one Postgres database.
Statistical observations use the tables in ``tilik_agent.ingestion.models``.
Publication chunks stay in the LangChain pgvector tables ``langchain_pg_collection``
and ``langchain_pg_embedding``.

The question agent reads model and limit settings from ``configs/default.yaml``.
Secrets stay in the environment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from the environment or a local ``.env`` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bps_api_key: str = ""
    postgres_user: str = "tilik"
    postgres_password: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "tilik"
    database_url: str = ""
    bps_raw_dir: Path = Path("data/bps/raw")
    bps_domain: str = "0000"
    bps_subcat: int = 514

    @model_validator(mode="after")
    def default_database_url(self) -> Settings:
        if not self.database_url and self.postgres_password:
            user = quote(self.postgres_user, safe="")
            password = quote(self.postgres_password, safe="")
            self.database_url = (
                f"postgresql+psycopg://{user}:{password}@"
                f"{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self


class LlmConfig(BaseModel):
    """Chat model settings. ``api_key_env`` names the variable in ``.env``."""

    model_config = ConfigDict(extra="forbid")

    model: str
    base_url: str
    temperature: float
    api_key_env: str
    extra_body: dict[str, Any]


class EmbeddingConfig(BaseModel):
    """Sentence-transformer settings for the SP2020 vector store."""

    model_config = ConfigDict(extra="forbid")

    model: str
    device: str
    normalize_embeddings: bool


class RetrievalConfig(BaseModel):
    """SP2020 search settings."""

    model_config = ConfigDict(extra="forbid")

    collection: str
    top_k: int
    max_rewrites: int


class DatasetLookupConfig(BaseModel):
    """Caps for the read-only dataset tools and one lookup step."""

    model_config = ConfigDict(extra="forbid")

    max_search_results: int
    max_measures: int
    max_dimension_members: int
    max_periods: int
    max_observations: int
    max_tool_rounds: int


class GraphConfig(BaseModel):
    """LangGraph run settings."""

    model_config = ConfigDict(extra="forbid")

    recursion_limit: int


class ConversationConfig(BaseModel):
    """Multi-turn chat settings."""

    model_config = ConfigDict(extra="forbid")

    max_history_messages: int


class JudgeMetricConfig(BaseModel):
    """One LLM-as-a-judge metric. ``enabled`` false skips it for that benchmark."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    threshold: float


class StatusMetricConfig(BaseModel):
    """The raw graph status recorded beside the judge metrics."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class EvalsMetricsConfig(BaseModel):
    """Benchmark metrics. Each one can be turned off on its own."""

    model_config = ConfigDict(extra="forbid")

    task_completion: JudgeMetricConfig
    argument_correctness: JudgeMetricConfig
    pipeline_status: StatusMetricConfig


class EvalsConfig(BaseModel):
    """Judge model and metric switches for ``tilik eval``."""

    model_config = ConfigDict(extra="forbid")

    judge: LlmConfig
    metrics: EvalsMetricsConfig


class AgentConfig(BaseModel):
    """Question-agent settings from ``configs/default.yaml``."""

    model_config = ConfigDict(extra="forbid")

    llm: LlmConfig
    embedding: EmbeddingConfig
    retrieval: RetrievalConfig
    dataset_lookup: DatasetLookupConfig
    graph: GraphConfig
    conversation: ConversationConfig
    evals: EvalsConfig


def project_root() -> Path:
    """Return the repository root, whether the process started in it or in ``notebooks``."""
    start = Path.cwd()
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return Path(__file__).resolve().parents[2]


def load_project_env() -> None:
    """Load ``.env`` from the repository root without overriding existing variables."""
    load_dotenv(project_root() / ".env")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()


def vector_connection() -> str:
    """Return the configured pgvector URL, defaulting to the application database."""
    connection = os.getenv("PGVECTOR_CONNECTION", "").strip() or get_settings().database_url
    if not connection:
        raise RuntimeError("Set PGVECTOR_CONNECTION or POSTGRES_PASSWORD in .env")
    return connection


_yaml_mtime_ns: int | None = None
_yaml_config: AgentConfig | None = None


def _yaml_agent_config() -> AgentConfig:
    """Parse ``configs/default.yaml`` again when the file's modification time changes."""
    global _yaml_mtime_ns, _yaml_config
    path = project_root() / "configs" / "default.yaml"
    mtime_ns = path.stat().st_mtime_ns
    if _yaml_config is not None and _yaml_mtime_ns == mtime_ns:
        return _yaml_config
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    _yaml_config = AgentConfig.model_validate(loaded)
    _yaml_mtime_ns = mtime_ns
    return _yaml_config


def get_agent_config() -> AgentConfig:
    """Return agent settings from ``configs/default.yaml``, with env overrides applied."""
    load_project_env()
    config = _yaml_agent_config()
    llm_updates: dict[str, str] = {}
    model = os.getenv("TILIK_MODEL")
    base_url = os.getenv("TILIK_LLM_BASE_URL")
    if model:
        llm_updates["model"] = model
    if base_url:
        llm_updates["base_url"] = base_url
    retrieval_updates: dict[str, str] = {}
    collection = os.getenv("RETRIEVAL_COLLECTION") or os.getenv("SP2020_COLLECTION")
    if collection:
        retrieval_updates["collection"] = collection
    return config.model_copy(
        update={
            "llm": config.llm.model_copy(update=llm_updates),
            "retrieval": config.retrieval.model_copy(update=retrieval_updates),
        }
    )

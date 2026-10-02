"""Database URL settings."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from tilik_agent.config import Settings, get_agent_config


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "postgres_user": "tilik",
        "postgres_password": "",
        "postgres_host": "postgres",
        "postgres_port": 5432,
        "postgres_db": "tilik",
        "database_url": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_database_url_encodes_password() -> None:
    """
    Condition:
    POSTGRES_PASSWORD contains characters that are not safe in a URL, and DATABASE_URL is unset.

    Expected:
    The connection URL is built from the settings and the password is percent-encoded.
    """
    settings = _settings(postgres_password="p@ss word")

    assert settings.database_url == "postgresql+psycopg://tilik:p%40ss%20word@postgres:5432/tilik"
    assert make_url(settings.database_url).password == "p@ss word"


def test_database_url_stays_empty_without_password() -> None:
    """
    Condition:
    POSTGRES_PASSWORD and DATABASE_URL are both empty.

    Expected:
    The URL stays empty. No development password is filled in.
    """
    settings = _settings()

    assert settings.database_url == ""
    assert "tilik:tilik@" not in settings.database_url


@pytest.mark.parametrize("database_url", ["postgresql+psycopg://tilik:secret@postgres:5432/tilik"])
def test_explicit_database_url_is_kept(database_url: str) -> None:
    """
    Condition:
    DATABASE_URL is set, and a password is also present.

    Expected:
    The explicit URL is kept.
    """
    settings = _settings(postgres_password="other", database_url=database_url)

    assert settings.database_url == database_url


def test_retrieval_collection_override_uses_generic_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SP2020_COLLECTION", "old-name")
    monkeypatch.setenv("RETRIEVAL_COLLECTION", "other-publication")

    assert get_agent_config().retrieval.collection == "other-publication"

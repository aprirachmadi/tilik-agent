"""Integration test for the question pipeline in-process: one sourced answer per connector, and a two-turn chat.

Each question is a real model call. Run ``.venv/bin/python -m pytest tests/e2e/test_pipeline_live.py`` against a
migrated Postgres with BNPB facts and the BPS province density table, and narrow it with ``-k`` (for example
``-k bmkg``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

from tilik_agent.agent.runner import run_question

load_dotenv(Path(__file__).resolve().parents[2] / ".env.local", override=True)

QUESTIONS = {
    "bps": "Berapa kepadatan penduduk DKI Jakarta pada 2021 menurut BPS?",
    "bnpb": "Berapa jumlah kejadian banjir di Indonesia sepanjang tahun 2024 menurut data BNPB?",
    "bmkg": "Kapan dan di mana gempa bumi terbaru terjadi menurut BMKG, dan berapa magnitudonya?",
    "bapanas": "Berapa harga cabai rawit merah hari ini di Jawa Barat menurut Bapanas?",
    "sipongi": "Berapa jumlah titik panas yang terdeteksi di Indonesia dalam 24 jam terakhir menurut SiPongi?",
}


@pytest.mark.parametrize("connector", list(QUESTIONS))
def test_connector_gives_a_sourced_answer(connector: str) -> None:
    result = run_question(QUESTIONS[connector], sources=[connector])
    assert not result.plan_error and not result.sql_error, result
    assert result.answer and result.sources, result
    assert connector in result.used_connectors
    if connector == "bps":
        assert any(source["kind"] == "dataset" for source in result.sources)
    else:
        assert any(source.get("provider", "").lower() == connector for source in result.sources)


def test_follow_up_is_answered_from_the_conversation() -> None:
    first = "Berapa perubahan kepadatan penduduk DKI Jakarta dari 2024 ke 2025 menurut tabel kependudukan BPS?"
    answer = run_question(first, conversation_id="multi-turn-smoke").answer or ""
    history = [{"role": "user", "content": first}, {"role": "assistant", "content": answer}]
    follow_up = run_question("Jadi kepadatannya naik, ya?", history=history, conversation_id="multi-turn-smoke")
    assert follow_up.source_route == "direct"

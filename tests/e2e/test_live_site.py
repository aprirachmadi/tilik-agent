"""Integration test for a locally running Tilik site: streaming, sources, visitor privacy, and the pinned example.

Start the dev servers first, then run ``.venv/bin/python -m pytest tests/e2e``. Each question is a real LLM call,
so narrow it with ``-k`` (for example ``-k "not bps and not bnpb"``). ``TILIK_E2E_URL`` overrides the API address.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator

import httpx
import pytest

BASE_URL = os.getenv("TILIK_E2E_URL", "http://localhost:5173/api").rstrip("/")

QUESTIONS = {
    "bps": "Berapa kepadatan penduduk DKI Jakarta pada 2021 menurut BPS?",
    "bnpb": "Berapa jumlah kejadian banjir di Indonesia sepanjang tahun 2024 menurut data BNPB?",
    "bmkg": "Kapan dan di mana gempa bumi terbaru terjadi menurut BMKG, dan berapa magnitudonya?",
}


def ask(client: httpx.Client, cookie: dict[str, str], connector: str) -> tuple[dict, float, float]:
    """Stream one question; return the result payload, the first status time, and the total time."""
    body = {"question": QUESTIONS[connector], "sources": [connector]}
    started = time.monotonic()
    first_status: float | None = None
    event = ""
    with client.stream("POST", "/questions/stream", json=body, cookies=cookie, timeout=300) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                if event == "status" and first_status is None:
                    first_status = time.monotonic() - started
                if event == "result":
                    return json.loads(line.removeprefix("data: ")), first_status or 0.0, time.monotonic() - started
    raise AssertionError("stream ended without a result")


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE_URL, follow_redirects=False) as session:
        yield session


@pytest.fixture(scope="module")
def cookie(client: httpx.Client) -> dict[str, str]:
    """The visitor cookie is Secure, so a plain-http client would not send it back; pass it explicitly."""
    first = client.get("/conversations")
    assert first.cookies, "a new visitor should receive a cookie"
    return dict(first.cookies)


@pytest.fixture(scope="module")
def private_conversation(client: httpx.Client, cookie: dict[str, str]) -> str:
    result, _, _ = ask(client, cookie, "bmkg")
    return result["conversation_id"]


def test_health(client: httpx.Client) -> None:
    assert client.get("/health").status_code == 200


def test_pinned_example_is_readable_but_not_continuable(client: httpx.Client, cookie: dict[str, str]) -> None:
    examples = [row for row in client.get("/conversations", cookies=cookie).json() if row["pinned"]]
    assert examples
    opened = client.get(f"/conversations/{examples[0]['id']}", cookies=cookie)
    assert opened.status_code == 200 and opened.json()["messages"]
    continued = client.post("/questions", json={"question": "tes", "conversation_id": examples[0]["id"]}, cookies=cookie)
    assert continued.status_code == 404


@pytest.mark.parametrize("connector", list(QUESTIONS))
def test_connector_answers_with_sources_and_streams(
    client: httpx.Client, cookie: dict[str, str], connector: str
) -> None:
    result, first_status, total = ask(client, cookie, connector)
    assert result.get("answer")
    assert result.get("sources")
    assert connector in result.get("used_connectors", [])
    assert total - first_status > 1.0, "status events should arrive well before the result, not all at once"


def test_conversation_persists_for_its_visitor(
    client: httpx.Client, cookie: dict[str, str], private_conversation: str
) -> None:
    listed = {row["id"] for row in client.get("/conversations", cookies=cookie).json()}
    assert private_conversation in listed


def test_another_visitor_cannot_read_it(private_conversation: str) -> None:
    with httpx.Client(base_url=BASE_URL, follow_redirects=False) as stranger:
        listed = {row["id"] for row in stranger.get("/conversations").json()}
        assert private_conversation not in listed
        assert stranger.get(f"/conversations/{private_conversation}").status_code == 404

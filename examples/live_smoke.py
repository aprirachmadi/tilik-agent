"""Smoke-test a running Tilik site over HTTP: streaming, sources, visitor privacy, and the pinned example.

Run ``.venv/bin/python examples/live_smoke.py https://tilikagent.frhvn.my.id/api``. The default is the local
dev server. Pass connector names after the URL to ask fewer questions. Each question is a real LLM call.
"""

from __future__ import annotations

import json
import sys
import time

import httpx

CASES = {
    "bps": "Berapa kepadatan penduduk DKI Jakarta pada 2021 menurut BPS?",
    "bnpb": "Berapa kejadian banjir di Indonesia pada 2024 menurut BNPB?",
    "bmkg": "Gempa terbaru yang tercatat BMKG di Indonesia",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"ok: {message}", flush=True)


def ask(client: httpx.Client, connector: str, cookie: dict[str, str]) -> dict:
    """Stream one question and return the result payload, checking events arrive before the end."""
    body = {"question": CASES[connector], "sources": [connector]}
    started = time.monotonic()
    first_status: float | None = None
    event = ""
    with client.stream("POST", "/questions/stream", json=body, cookies=cookie, timeout=300) as response:
        check(response.status_code == 200, f"{connector}: stream accepted")
        for line in response.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
                if event == "status" and first_status is None:
                    first_status = time.monotonic() - started
                if event == "result":
                    total = time.monotonic() - started
                    check(
                        first_status is not None and total - first_status > 1.0,
                        f"{connector}: status events arrived progressively ({first_status:.1f}s of {total:.1f}s)"
                        if first_status is not None
                        else f"{connector}: status events arrived progressively",
                    )
                    return payload
    raise SystemExit(f"FAIL: {connector}: stream ended without a result")


def main() -> None:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173/api").rstrip("/")
    selected = sys.argv[2:] or list(CASES)
    unknown = [name for name in selected if name not in CASES]
    if unknown:
        raise SystemExit(f"Unknown connector: {unknown[0]}")

    with httpx.Client(base_url=base, follow_redirects=False) as client:
        health = client.get("/health")
        check(health.status_code == 200, "health endpoint reports ok")

        # The visitor cookie is Secure, so a plain-http client would not send it back; pass it explicitly.
        first = client.get("/conversations")
        check(bool(first.cookies), "a new visitor receives a cookie")
        cookie = dict(first.cookies)
        examples = [row for row in first.json() if row["pinned"]]
        check(bool(examples), "the pinned example is listed for a new visitor")

        example_id = examples[0]["id"]
        opened = client.get(f"/conversations/{example_id}", cookies=cookie)
        check(opened.status_code == 200 and bool(opened.json()["messages"]), "the example can be opened")
        continued = client.post("/questions", json={"question": "tes", "conversation_id": example_id}, cookies=cookie)
        check(continued.status_code == 404, "the example cannot be continued")

        conversation_ids = []
        for connector in selected:
            result = ask(client, connector, cookie)
            check(bool(result.get("answer")), f"{connector}: answer is not empty")
            check(bool(result.get("sources")), f"{connector}: answer cites sources")
            check(connector in result.get("used_connectors", []), f"{connector}: connector was used")
            conversation_ids.append(result["conversation_id"])

        own = {row["id"] for row in client.get("/conversations", cookies=cookie).json() if not row["pinned"]}
        check(set(conversation_ids) <= own, "the visitor's conversations persist under their cookie")

    with httpx.Client(base_url=base, follow_redirects=False) as stranger:
        listed = {row["id"] for row in stranger.get("/conversations").json()}
        check(not (set(conversation_ids) & listed), "another visitor cannot see those conversations")
        other = stranger.get(f"/conversations/{conversation_ids[0]}")
        check(other.status_code == 404, "another visitor gets 404 for a private conversation")

    print("All checks passed.")


if __name__ == "__main__":
    main()

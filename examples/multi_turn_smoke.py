"""Smoke-test two-turn chat: data lookup then direct clarification."""

from __future__ import annotations

from tilik_agent.agent.runner import run_question
from tilik_agent.evals.scores import pipeline_status_label


def main() -> None:
    q1 = (
        "Berapa perubahan kepadatan penduduk DKI Jakarta dari 2024 ke 2025 "
        "menurut tabel kependudukan BPS?"
    )
    print("=== Turn 1 ===")
    r1 = run_question(q1, conversation_id="multi-turn-smoke")
    status1, comment1 = pipeline_status_label(r1.step_status, r1.plan_error)
    print("source_route:", r1.source_route)
    print("pipeline_status:", status1, comment1)
    print("answer:", (r1.answer or "").encode("ascii", "replace").decode())

    history = [
        {"role": "user", "content": q1},
        {"role": "assistant", "content": r1.answer or ""},
    ]
    q2 = "jadi perubahannya naik ya?"
    print("\n=== Turn 2 ===")
    r2 = run_question(q2, history=history, conversation_id="multi-turn-smoke")
    status2, comment2 = pipeline_status_label(r2.step_status, r2.plan_error)
    print("source_route:", r2.source_route)
    print("pipeline_status:", status2, comment2)
    print("answer:", (r2.answer or "").encode("ascii", "replace").decode())

    if r2.source_route != "direct":
        raise SystemExit(f"Expected turn 2 route direct, got {r2.source_route!r}")


if __name__ == "__main__":
    main()

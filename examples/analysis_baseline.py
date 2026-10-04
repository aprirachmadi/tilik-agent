"""Run the hardest issue-05 analysis question through the current pipeline.

Run ``poetry run python examples/analysis_baseline.py``. It answers benchmark item 14 once, without
judging, and prints the plan, step status, and fetched rows next to the probe result,
so the gap the analysis node must close is visible.
"""

from __future__ import annotations

import json
import sys

from tilik_agent.agent.runner import initial_question_state, merge_question_update, question_result, stream_question
from tilik_agent.evals.scores import pipeline_status_label

QUESTION = "Apakah kesenjangan persentase penduduk miskin antarprovinsi menyempit sejak 2017?"

EXPECTED = """CV across provinces: 0.531 (2017), 0.535, 0.544, 0.522, 0.502, 0.512, 0.514 (2023) for n=34;
then 0.605 (2024), 0.617 (2025), 0.618 (2026) for n=38.
Trap: the 2024 jump comes from the four new Papua provinces. Compare the same 34 provinces every year."""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(f"=== question ===\n{QUESTION}")
    state = initial_question_state(QUESTION)
    for update in stream_question(QUESTION):
        merge_question_update(state, update)
    result = question_result(state)

    rows = state.get("observations") or []
    status, comment = pipeline_status_label(result.step_status, result.plan_error)
    print(f"\n=== plan ===\n{json.dumps(state.get('plan', {}), ensure_ascii=False, indent=2)}")
    print(f"\n=== step status ===\n{json.dumps(result.step_status, ensure_ascii=False, indent=2)}")
    print(f"\npipeline_status: {status} {comment}")
    print(f"observations fetched: {len(rows)} (truncated batch: {any(row.get('truncated_batch') for row in rows)})")
    print(f"datasets: {sorted({row.get('dataset_id') for row in rows})}")
    print(f"periods: {sorted({str(row.get('period')) for row in rows})}")
    for key in ("sql_error", "sql_warnings", "retrieval_error", "plan_error"):
        if state.get(key):
            print(f"{key}: {state[key]}")
    print(f"\n=== answer ===\n{result.answer}")
    print(f"\n=== expected (probe) ===\n{EXPECTED}")


if __name__ == "__main__":
    main()

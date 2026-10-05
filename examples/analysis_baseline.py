"""Run the issue-05 time-scope regression through the current pipeline.

Run ``poetry run python examples/analysis_baseline.py``. It answers benchmark item 14 once, without
judging, and prints the plan, selected series, computed result, and answer.
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
    selections = state.get("series_selections") or []
    analysis = state.get("analysis_results") or []
    status, comment = pipeline_status_label(result.step_status, result.plan_error)
    print(f"\n=== plan ===\n{json.dumps(state.get('plan', {}), ensure_ascii=False, indent=2)}")
    print(f"\n=== step status ===\n{json.dumps(result.step_status, ensure_ascii=False, indent=2)}")
    print(f"\npipeline_status: {status} {comment}")
    print(f"observations fetched: {len(rows)} (truncated batch: {any(row.get('truncated_batch') for row in rows)})")
    print(f"datasets: {sorted({row.get('dataset_id') for row in rows})}")
    print(f"periods: {sorted({str(row.get('period')) for row in rows})}")
    selection_summaries = [
        {key: value for key, value in item.items() if key != "observation_ids"} for item in selections
    ]
    result_summaries = [
        {key: value for key, value in item.items() if key != "input_observation_ids"} for item in analysis
    ]
    print(f"\n=== selections ===\n{json.dumps(selection_summaries, ensure_ascii=False, indent=2)}")
    print(f"\n=== analysis ===\n{json.dumps(result_summaries, ensure_ascii=False, indent=2)}")
    for key in ("sql_error", "sql_warnings", "retrieval_error", "plan_error", "analysis_error"):
        if state.get(key):
            print(f"{key}: {state[key]}")
    print(f"\n=== answer ===\n{result.answer}")
    print(f"\n=== expected (probe) ===\n{EXPECTED}")


if __name__ == "__main__":
    main()

# Add edge-case questions to the single-turn benchmark

## Problem

All 10 items in `src/tilik_agent/evals/datasets/demografi_sosial_questions.json` can be answered fully from the data. None tests what the agent should do when the honest answer is "that is not in the data" or "which one do you mean?". Real users ask these questions, and the cheapest failure is a confident made-up number.

These items are also the cheapest place to start filling `expected_response`. The expected behavior is short and checkable, and the values come from one lookup each.

## Items

Values checked against Postgres on 2026-10-03. Re-check them before writing `expected_response`.

| id | Question | Data | Expected behavior |
|---|---|---|---|
| e1 | Berapa TPT Jawa Barat pada Agustus 2026? | Dataset 805 has only Februari for 2026. Jawa Barat: 6.64 (Feb 2026), 6.77 (Aug 2025) | Says August 2026 is not available yet. Offers February 2026 (6.64) or August 2025 (6.77). Does not estimate. |
| e2 | Bandingkan persentase penduduk miskin Indonesia Maret 2026 dan September 2026. | Dataset 374 has only Semester 1 (Maret) for 2026. Indonesia, Jumlah: 8.07 | Gives March 2026 (8.07) and says September 2026 is not available. Does not compute a change. |
| e3 | Berapa TPT Jawa Barat tahun 2025? | Both rounds exist: 6.74 (Feb), 6.77 (Aug) | Gives both, or states which round it chose and gives the other. Does not silently pick one. |
| e4 | Mengapa TPT Bali paling tinggi di Indonesia pada Agustus 2025? | Bali 1.49. Highest: Papua 6.96, Papua Barat Daya 6.85, Jawa Barat 6.77 | Corrects the false premise with the actual top provinces. Does not explain a ranking that is false. |
| e5 | Berapa tingkat inflasi Indonesia tahun 2025? | No dataset title mentions inflasi. Confirm that no retrieved document covers it before writing the item. | Says the data available to Tilik does not cover inflation. Gives no number from model knowledge. |
| e6 | Tolong buatkan puisi tentang Jakarta. | Outside the agent's scope | Declines briefly and says what it can answer. No SQL lookup. |

## Proposed work

1. Add e1–e6 to the existing question file, and bump `version`.
2. Fill `expected_response` for these six items with the expected behavior and values from the table. Leave the existing items as `null`.
3. If the difficulty field from the golden-set discussion is added by then, tag e1–e6 as edge cases.
4. Before adding e6, check how `ArgumentCorrectnessMetric` scores a run with no tool calls in the installed DeepEval 4.2.3:
   - A 0 would be the same problem as the `ToolUseMetric` zero score noted in 04.
   - If so, skip that metric for items whose expected behavior has no tool call. Do not add e6 until the skip exists.

## Acceptance criteria

- The six items load through the existing dataset schema and publish to the Langfuse dataset with their `expected_response`.
- `tilik benchmark` runs them with no code changes, except the metric skip from step 4 if it is needed.
- The first run's scores are recorded as the baseline for these items in the experiment notes.

## Out of scope

- Jailbreak or prompt-injection items.
- The province-set trap (34 vs 38 provinces). It is covered by a3 in 05.
- Multi-turn ambiguity and corrections. They are covered by c3 in 04.

## Relevant code

- `src/tilik_agent/evals/datasets/demografi_sosial_questions.json`
- `src/tilik_agent/evals/dataset.py`, `src/tilik_agent/evals/runner.py`, `src/tilik_agent/evals/metrics.py`

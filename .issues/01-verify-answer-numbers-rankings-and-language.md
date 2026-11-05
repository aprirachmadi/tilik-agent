# Measure wrong rankings, sums, and language in the final answer

Merges the former issues 01 (ranked claims) and 02 (derived values and answer language).

## Problem

In [the saved conversation](http://localhost:5173/c/60bd1622-e2cc-47ca-9613-689ec52437dc) (message 18, trace `5d0b67f8dfe1a1408eb4a616113fdef9`), the reply to "coba tunjukin grafik per kelompok umur" contains four errors. The SQL step returned correct values from dataset 123, "Jumlah Penduduk Menurut Kelompok Umur dan Jenis Kelamin (ribu jiwa)".

| Claim in the answer | Data | Error |
|---|---|---|
| "Kelompok umur terbesar: **25–29 tahun** (22.514,2 ribu jiwa)" | 2025: 0–4 = **22,752.6**, 25–29 = 22,514.2 | Wrong ranking |
| "puncak di kelompok 25–29 tahun (22.525,6 ribu jiwa)" for 2024 | 2024: 0–4 = **22,638.1**, 25–29 = 22,525.6 | Wrong ranking |
| "65+ … sekitar **21.579,3** ribu jiwa" | 65–69 9,267.4 + 70–74 6,343.6 + 75+ 5,972.3 = **21,583.3** | Wrong sum (off by 4.0) |
| "65+**合计**" | Indonesian answer | Chinese fragment |

The same answer also draws an ASCII bar chart next to the rendered chart. The writer LLM call inside `write_answer` volunteered all four errors as "Sorotan distribusi"; no `calculate` step ran.

## Decision

Treat this as a model failure to measure first, not a reason for an inline guardrail yet.

- One trace is not evidence of how often this happens. Error analysis should show whether it persists before we build infrastructure for it (Hamel Husain & Shreya Shankar, [AI Evals FAQ](https://hamel.dev/blog/posts/evals-faq/): fix unspecified instructions first; build evaluators only for failures that persist).
- An inline guardrail must have a very low false-positive rate. A prototype deterministic number checker deleted correct sentences (any decrease such as "turun 0,13 poin", and plain counts such as "38 provinsi"), so it was not merged.
- The Chinese fragment is known behaviour of the Qwen model (`qwen3.8-flash`) and is cosmetic.
- Issue 05 (statistical analysis) and a model change may reduce the ranking and arithmetic errors; the benchmark item below tells us whether they do.

## Done

1. Prompt rules in `_answer_prompt()` (`src/tilik_agent/agent/answer.py`):
   - Compare a value with every comparable observation for the same period before calling it the largest, smallest, or peak.
   - Do not volunteer totals, sums, ratios, or shares that the question did not ask for and that are not in the intermediate calculations.
   - Never draw charts, bar graphs, or text-art tables.
   - Write only in the user's language, except for cited names and quotations.
2. Regression item `11` in `src/tilik_agent/evals/datasets/demografi_sosial_questions.json`, with ground truth checked against Postgres (dataset 123, measure 183, 2025): 0–4 = 22,752.6, 25–29 = 22,514.2, total = 284,438.8 thousand.

## Follow-up (only if the benchmark shows the failure persists)

- Run item 11 several times on the current model and after issue 05 or a model change, and record how often the ranking is wrong.
- If it stays wrong, add a deterministic offline benchmark metric that checks numbers and superlatives against the observations. Run it as an evaluator, not as an inline guardrail, so false positives only add score noise.
- Only promote it to an inline guardrail if its false-positive rate on real traces is near zero. Known traps from the prototype: negative differences and percent changes are written without a sign, counts like "38 provinsi" are not in the evidence, and a share of a sum cannot be declared from observation IDs alone.

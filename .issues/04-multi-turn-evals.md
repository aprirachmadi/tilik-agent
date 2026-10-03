# Add a multi-turn benchmark with DeepEval's conversational agent metrics

## Problem

The benchmark only scores single, self-contained questions. Every item in `src/tilik_agent/evals/datasets/demografi_sosial_questions.json` runs through `run_question(question)` with no `history`, so the path real chat users take is never measured: `api.py` loads `history` from `conversation_id` and passes it to the same `run_question`, and `history_block()` puts it into the planner and answer prompts.

Failures that only exist across turns are therefore invisible:

- **Lost reference**: after “Berapa TPT Jawa Barat pada Agustus 2025?”, the follow-up “Kalau Februari?” is answered with national TPT instead of Jawa Barat.
- **Self-contradiction**: turn 3 restates the August value differently from turn 1.
- **Ignored correction**: after “Maaf, maksud saya perdesaan, bukan perkotaan.”, the agent still queries urban figures.
- **Redundant work**: values already given in the history are fetched again. This remains visible in traces but is not scored by the metrics proposed below.

The 2026-09-26 experiment also shows why the single-turn set cannot stand in for this: item 4 (top three TPT provinces plus their APS 16–18) scored task_completion **0.40** with argument_correctness **0.93**. The trace shows one combined `sql_lookup` step, `get_observations` failing with “Too many filters” and “two members from one axis”, ~38 per-province calls, and no APS values. Splitting that question across two turns tells us whether the agent fails at the combination or at the APS lookup itself.

## DeepEval guidance (research summary)

Sources: [Multi-Turn Evaluation Metrics guide](https://deepeval.com/guides/guides-multi-turn-evaluation-metrics), [Multi-Turn Evaluation guide](https://deepeval.com/guides/guides-multi-turn-evaluation), [Datasets](https://deepeval.com/docs/evaluation-datasets), [Multi-turn test case](https://deepeval.com/docs/evaluation-multiturn-test-cases), issues [#2223](https://github.com/confident-ai/deepeval/issues/2223) and [#2187](https://github.com/confident-ai/deepeval/issues/2187), and the installed `deepeval` 4.2.3 source.

- **Multi-turn metrics operate on `ConversationalTestCase`** (a list of `Turn`s), not on `LLMTestCase`. The metrics guide's “Choosing the Right Metrics” table maps “Uses tools or function calling” to `GoalAccuracyMetric` and `ToolUseMetric`.
- **One score per metric per conversation, regardless of turn count.** In the installed 4.2.3 source, `GoalAccuracyMetric` averages the per-interaction goal scores and plan scores, then averages those two means. `ToolUseMetric` averages tool-selection scores across interactions, averages argument scores across interactions that called tools, and returns the lower mean. Thus a two-turn and a three-turn conversation each produce one row of metric scores; an individual weak turn can be diluted by stronger turns.
- **These two are DeepEval's multi-turn counterparts of our current metrics.** Issue #2223 asked for multi-turn versions of Task Completion and Tool Correctness, noting that splitting a conversation into single-turn cases loses context. Maintainers replied “Conversational metrics will be out by today” and closed it as released on 2025-10-28; #2187 was closed the same way (“new agent metrics for both single and multi-turn”).
  - `TaskCompletionMetric` → `GoalAccuracyMetric` (goal reached + plan quality/adherence, averaged).
  - `ArgumentCorrectnessMetric` → `ToolUseMetric` (tool selection and argument correctness; score is the minimum of the two).
- **DeepEval does not document feeding history into single-turn metrics.** No doc or issue recommends or forbids it; we will not do it.
- **The proposed judge metrics are referenceless.** In 4.2.3, `GoalAccuracyMetric`, `ToolUseMetric`, `ConversationCompletenessMetric`, and `TurnRelevancyMetric` read the generated `turns` (and `available_tools` for tool use); they do not use `scenario`, `expected_outcome`, or `context` to compare against a ground-truth answer. Keep those fields as human-readable dataset and Langfuse context, not as a scored reference.
- **Assistant turns must be generated at evaluation time.** The dataset docs say `turns` “should 100% be generated at evaluation time”; pre-filled turns are only for resuming from a fixed point. Goldens describe the conversation (`scenario`, `expected_outcome`, optional `context`).
- **Multi-turn context is only judged by some metrics.** From the 4.2.3 source:
  - `ToolUseMetric` and `GoalAccuracyMetric` split the conversation with `get_unit_interactions()` and judge each user→assistant pair with **only that pair's user message**. Turn 2 of a follow-up is judged as “Kalau Februari?” alone.
  - `ConversationCompletenessMetric` extracts user intentions and judges their satisfaction against the full generated conversation. DeepEval's [multi-turn evaluation guide](https://deepeval.com/guides/guides-multi-turn-evaluation) recommends it as a core conversational metric. It does not need reference answers.
  - `TurnRelevancyMetric` judges each assistant turn against a sliding window of prior interactions (`window_size`, default 10). Its window is measured in user→assistant interactions in 4.2.3, not individual messages. It can spot off-topic replies but is not a factual correctness check.
- **Known gaps in the agentic metrics:**
  - Both read only `Turn.tools_called`; they do not see planner output, step status, or SQL sub-agent spans.
  - `ToolUseMetric` scores 0 when no turn calls a tool (fix open in PR [#2968](https://github.com/confident-ai/deepeval/pull/2968)).
  - `include_reason` is ignored by both (PR [#3112](https://github.com/confident-ai/deepeval/pull/3112), open).

## Decision

Add a separate multi-turn benchmark scored with:

| Metric | Required | Why |
|---|---|---|
| `GoalAccuracyMetric` | yes | DeepEval's multi-turn Task Completion: did each user goal get reached, and was the plan sound. Keeps the process focus that led us to pick task completion over answer correctness. |
| `ToolUseMetric` | yes | DeepEval's multi-turn Argument Correctness, plus tool selection against the full SQL tool list. |
| `ConversationCompletenessMetric` | yes | Judges whether the intentions expressed across the full transcript were satisfied, including follow-ups and corrections. Referenceless; complements the pairwise agentic metrics. |
| `TurnRelevancyMetric` | optional, off by default | Judges each reply in recent conversational context. Useful for diagnosing drift, but broader than the core completion check and adds judge cost. |
| `pipeline_status` | yes (non-judge) | Worst graph status across the conversation's turns; no LLM call. |

Rationale:

- Follow DeepEval's intended design rather than bending single-turn metrics with injected history, so scores stay comparable with DeepEval's documented behavior and future fixes.
- Keep the single-turn benchmark and its trace-based TaskCompletion/ArgumentCorrectness unchanged. It sees agent internals (planner, steps, spans) that the conversational metrics do not, so the two benchmarks complement each other.
- Use **scripted user turns**, not `ConversationSimulator`: identical user messages every run make runs comparable, and there is no simulator-model cost. `persona` is not needed.
- These are all referenceless scores. `expected_outcome` describes the intended behavior for human review and Langfuse; it is not fed to a judge as ground truth. The scores may miss a wrong BPS value, a subtle province mix-up, or a contradiction between two assistant-reported numbers. Keep the generated transcript and tool traces reviewable, and add a separate grounded check only when reference data is available.
- The metric scores are aggregated once per conversation, so compare conversations with different turn counts cautiously. A passing aggregate does not prove that every turn was correct.

## Proposed work

0. **Rename `tilik eval` to `tilik benchmark`** in its own commit, before the rest of this issue:
   - The command runs both generation and judging, so "benchmark" is the accurate name.
   - Change `@app.command("eval")` in `src/tilik_agent/cli.py` to `"benchmark"`. Rename `evaluate()` to `benchmark()`.
   - Update the references in `README.md`, the `config.py`, `evals/scores.py` and `evals/dataset.py` docstrings, and `.issues/05-statistical-analysis-step.md`.
   - Do not keep an `eval` alias.
   - Behavior and tests are otherwise unchanged.

1. **Dataset file** `src/tilik_agent/evals/datasets/demografi_sosial_conversations.json`:

   ```json
   {
     "name": "demografi-sosial-conversations",
     "version": "2026-10-02",
     "items": [
       {
         "id": "c1",
         "scenario": "User asks Jawa Barat's TPT for one period, then asks about another period by ellipsis, then asks for a comparison without naming the province again.",
         "expected_outcome": "Agent reports Jawa Barat's TPT for August 2025 and February 2025 from the TPT-by-province table, keeps Jawa Barat across all turns, and states the difference and which month was higher.",
         "user_turns": [
           "Berapa TPT Jawa Barat pada Agustus 2025?",
           "Kalau Februari?",
           "Jadi naik atau turun, dan selisihnya berapa?"
         ]
       },
       {
         "id": "c2",
         "scenario": "User asks for a top-3 list, then refers to those three provinces only as 'provinsi-provinsi itu' in a different table.",
         "expected_outcome": "Agent finds the three provinces with the highest TPT in August 2025, then fetches APS age 16-18 for 2025 for exactly those three without asking the user to repeat them.",
         "user_turns": [
           "Provinsi mana saja yang punya tiga TPT tertinggi pada Agustus 2025?",
           "Berapa Angka Partisipasi Sekolah umur 16-18 tahun di provinsi-provinsi itu pada 2025?"
         ]
       },
       {
         "id": "c3",
         "scenario": "User asks about urban poverty, then corrects the area type mid-conversation.",
         "expected_outcome": "Agent first reports the urban poverty rate change from March 2024 to March 2025, then after the correction reports the rural figures instead and does not reuse the urban numbers.",
         "user_turns": [
           "Bagaimana perubahan persentase penduduk miskin perkotaan Indonesia dari Maret 2024 ke Maret 2025?",
           "Maaf, maksud saya perdesaan, bukan perkotaan."
         ]
       },
       {
         "id": "c4",
         "scenario": "User asks a value for one province, then changes only the year.",
         "expected_outcome": "Agent reports DKI Jakarta's population density for 2025, then for 2024 from the same table, and keeps DKI Jakarta in the second lookup.",
         "user_turns": [
           "Berapa kepadatan penduduk DKI Jakarta tahun 2025?",
           "Kalau 2024?"
         ]
       }
     ]
   }
   ```

   `scenario`, `expected_outcome`, and optional `context` map to `ConversationalGolden` fields. They describe the run for human review; the selected metrics do not score against them. `user_turns` is our own field for scripted replay. With at most 3 user turns, the final turn receives 4 prior messages, within `conversation.max_history_messages: 6`.

2. **Schema** in `src/tilik_agent/evals/dataset.py`: `ConversationItem` (`id`, `scenario`, `expected_outcome`, `context: list[str] | None`, `user_turns: list[str]` with at least one entry) and `ConversationDataset`, following `BenchmarkItem`/`BenchmarkDataset` (`extra="forbid"`), plus a loader.

3. **Generation** (new runner function next to `run_benchmark`): for each conversation, keep a `history: list[ChatTurn]` and, per user turn:
   - call `run_question(user_turn, history=history, conversation_id=<run-scoped id>, callbacks=[CallbackHandler(...)])`. `conversation_id` already becomes the Langfuse `session_id`, so a conversation's turns group as one session;
   - append `Turn(role="user", content=user_turn)` and `Turn(role="assistant", content=answer, tools_called=<handler trace tools_called with descriptions>)`, reusing the tool-call extraction in `_argument_score()`;
   - append the user message and the **generated** answer to `history`. Never use hardcoded assistant text.
   - Then build `ConversationalTestCase(scenario=..., expected_outcome=..., context=..., turns=...)`.

4. **Metrics** in `src/tilik_agent/evals/metrics.py`, built with the existing `AgentJudgeLlm`:
   - `GoalAccuracyMetric(threshold, model=judge, async_mode=False)`
   - `ToolUseMetric(available_tools=[ToolCall(name=t.name, description=t.description) for t in SQL_TOOLS], threshold, model=judge, async_mode=False)`
   - `ConversationCompletenessMetric(threshold, model=judge, async_mode=False)`
   - `TurnRelevancyMetric(threshold, model=judge, window_size=conversation.max_history_messages // 2 + 1, async_mode=False)`, only when enabled. In 4.2.3 the window counts complete user→assistant interactions: three prior exchanges plus the current exchange when the history cap is six messages. This optional score checks contextual relevance, not whether the reported BPS values are correct.

5. **Config**: add an `evals.conversation_metrics` block (`goal_accuracy`, `tool_use`, `conversation_completeness`, `turn_relevancy` as `JudgeMetricConfig`; `pipeline_status` as `StatusMetricConfig`) to `EvalsConfig` and `configs/default.yaml`, with `turn_relevancy.enabled: false`.

6. **Scores and Langfuse**: one score row per conversation, reusing `ItemScore`/`format_report`. Publish as a separate Langfuse dataset `demografi-sosial-conversations` via a variant of `sync_benchmark` (item input = `user_turns`, expected output = `expected_outcome`, metadata = `scenario`, id, version) and `run_experiment`, with the same captured-score replay used by `_run_published()`. The experiment task output is the transcript.

7. **CLI**: expose the multi-turn run as `tilik benchmark --conversations`. Plain `tilik benchmark` keeps the existing single-turn behavior unchanged.

## Acceptance criteria

- Each conversation's turn k receives the generated answers of turns 1..k-1 as `history`; a test with a stubbed `run_question` asserts this.
- `ConversationalTestCase.turns` alternate user/assistant, assistant turns carry that turn's `tools_called`, and no assistant content comes from the dataset file.
- Goal accuracy, tool use, conversation completeness, and pipeline status are each reported once per conversation; turn relevancy appears only when enabled; disabled metrics are omitted, as in the single-turn report. A two-turn and a three-turn conversation each have one score row.
- The judge metrics receive only generated turns and tool descriptions, never a reference answer or `expected_outcome` as grading input. The transcript remains available to inspect failures that aggregated or referenceless scores miss.
- `ToolUseMetric` receives all three SQL tools with descriptions as `available_tools`.
- Turns of one conversation share a Langfuse session; the run appears as its own experiment.
- The single-turn benchmark output and tests are unchanged.
- Tests stub the judge and agent; no test calls a real model.

## Out of scope

- `ConversationSimulator` and personas.
- Injecting history into the single-turn TaskCompletion/ArgumentCorrectness metrics.
- Working around the open DeepEval bugs above; revisit when #2968/#3112 land. The current dataset has tool calls in every conversation, so the ToolUse zero-score case does not apply yet.

## Relevant code

- `src/tilik_agent/evals/runner.py`: `run_benchmark()`, `score_question()`, `_argument_score()`, `_run_published()`
- `src/tilik_agent/evals/metrics.py`, `src/tilik_agent/evals/scores.py`, `src/tilik_agent/evals/dataset.py`, `src/tilik_agent/evals/langfuse_dataset.py`
- `src/tilik_agent/agent/runner.py`: `run_question(history=..., conversation_id=...)`
- `src/tilik_agent/agent/runtime.py`: `history_block()`
- `src/tilik_agent/agent/sql.py`: `SQL_TOOLS`
- `src/tilik_agent/config.py`, `configs/default.yaml`: `EvalsConfig`, `ConversationConfig.max_history_messages`
- `src/tilik_agent/cli.py`: benchmark command
- `tests/test_evals.py`

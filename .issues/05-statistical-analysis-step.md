# Add a data analysis agent for questions that need more than lookups

## Problem

Tilik was meant to be a data analyst agent, but the current pipeline and eval set never do heavy analysis, even though the data is rich enough for it.

What the pipeline does today:

- The question graph is `plan_question` → `run_sql` / `run_retrieval` / `run_both` → `write_answer` → optional `build_chart` (`src/tilik_agent/agent/graph.py`). There is no node that analyses data.
- `plan_question` produces `sql_lookup`, `retrieve_document`, or `calculate` steps. The planner prompt in `src/tilik_agent/agent/workers.py` says not to create `calculate` steps when the SQL agent can rank inside its lookup.
- `Calculation.operation` in `src/tilik_agent/agent/schemas.py` only allows `difference`, `sum`, `mean`, `ratio`, and `percent_change`. `_check_calculation()` in `src/tilik_agent/agent/answer.py` verifies them against observation values.
- `get_observations` returns rows to the SQL agent, capped by `dataset_lookup.max_observations` (currently 500 in the working configuration; 100 in the committed configuration). One province panel such as P0 by province and area type (dataset 374) has 1,932 rows. Repeated calls or a higher cap do not give the next node a reliable, complete series contract, and large panels should not enter model context.
- The model never joins two tables on province, so it has never met the label mismatches listed below.

The linked baseline trace (read from local ClickHouse, trace `c08b2ae63d4942ddf9cf1ac7fb337496`) shows an earlier failure than truncation. The user asked whether the provincial poverty gap narrowed **since 2017**. `plan_question` rewrote this as `201?` and asked for that period and its predecessor. `describe_dataset` exposed periods 2017–2026, but `get_observations` requested only 2018 and 2019, with the `Jumlah` category pinned and the March/September axis unpinned. It returned 140 rows (70 per year, including the national aggregate), with `truncated: false`. The answer compared 2018–2019 maximum-minus-minimum gaps and claimed to answer the question since 2017. This trace therefore demonstrates loss of the user's time scope and an unchecked analysis choice; it does **not** demonstrate a row-cap failure. Later full-panel questions still need a path that avoids sending all rows through the model.

What the eval set asks: items 1–10 in `src/tilik_agent/evals/datasets/demografi_sosial_questions.json` are lookups, rankings, or arithmetic; item 11 asks for a chart and largest age group. None asks about a relationship, a trend, how spread out the provinces are, or an outlier.

What the data supports (Postgres, checked 2026-10-02):

- 806 parsed datasets and 4,338,383 observations.
- 138 parsed datasets with a province dimension (34–39 members) and 8 or more periods. Most cover 2016/2017–2025. Calorie intake (431) and food/non-food spending (380, 381, 474, 476) cover 2007–2025.
- Indicators that can be joined on province: education (RLS 693, APS 627, APK 598, literacy 591), labour (TPT 805, informal work 794, formal work 781, NEET 783), poverty (P0 374, P1 346, P2 349, poverty line 342, Gini 343), health (AHH 194, smoking 278, measles immunisation 258), housing (sanitation 571, drinking water 560), and population (116).

## Example questions

The results below come from probe queries run directly against Postgres. They are candidate ground truth for new items 12–19 in the existing question benchmark (`demografi_sosial_questions.json`, which holds items 1–11 today). Re-check each one with the final analysis code before writing it into `expected_response` (see Proposed work, step 6).

Common filters: provinces only (the `INDONESIA` member is excluded). P0 is `Jumlah` / `Semester 1 (Maret)`. TPT is `Agustus` unless stated otherwise.

| id | Question | Result from probe | Technique | What a correct answer must notice |
|---|---|---|---|---|
| 12 | Apakah provinsi dengan rata-rata lama sekolah lebih tinggi cenderung memiliki TPT lebih rendah pada 2025? Seberapa kuat hubungannya? | n=38, Pearson r = **+0.565**, slope +0.656 pp TPT per year of schooling, R² = 0.319 | Correlation, OLS | The sign is the opposite of what people expect. Say it is correlation, and name urbanisation as a likely confounder. |
| 13 | Seberapa kuat hubungan rata-rata lama sekolah dan persentase penduduk miskin antarprovinsi pada 2025? | n=38, r = **−0.572**, slope −3.150 pp P0 per year of schooling, R² = 0.327 | Correlation, OLS across two datasets | Join on province across datasets 693 and 374. |
| 14 | Apakah kesenjangan persentase penduduk miskin antarprovinsi menyempit sejak 2017? | CV across provinces: 0.531 (2017), 0.535, 0.544, 0.522, 0.502, 0.512, **0.514 (2023)** for n=34; then **0.605 (2024)**, 0.617 (2025), 0.618 (2026) for n=38 | Sigma-convergence (CV per year) | **Trap:** four new Papua provinces enter the panel in 2024. Compute the common-code panel before attributing the jump; Papua/Papua Barat boundaries may also have changed. |
| 15 | Apakah provinsi yang paling miskin pada 2017 mengalami penurunan kemiskinan paling besar hingga 2025? | n=34, r(P0 in 2017, change 2017→2025) = **−0.902**, slope −0.271 | Beta-convergence | Use only provinces that have both years. Say whether the change is in percentage points or relative. |
| 16 | Apakah TPT Agustus biasanya lebih rendah dari TPT Februari di sebagian besar provinsi? | Provinces where August < February: 2017 11/34, 2018 7/34, 2019 6/34, 2020 1/34, 2021 20/34, 2022 19/34, 2023 20/34, 2024 20/38, 2025 14/38 | Paired comparison per year | **The pattern flips after 2020.** One pooled answer would hide this. |
| 17 | Provinsi mana yang TPT-nya paling menyimpang dari yang diperkirakan berdasarkan rata-rata lama sekolahnya pada 2025? | Residuals from TPT ~ RLS: Bali **−3.38**, Jawa Barat **+2.30**, Banten +1.98, DI Yogyakarta −1.74, Papua +1.71, Papua Barat Daya +1.63 | Regression residuals, outliers | Report the residual with the sign and the fitted model, not just the raw TPT ranking. |
| 18 | Seberapa kuat hubungan akses sanitasi layak dan persentase penduduk miskin antarprovinsi pada 2025? | n=38, r = **−0.801** | Correlation | A strong correlation is still not causation. |
| 19 | Bagaimana tren pangsa pengeluaran makanan Indonesia 2007–2025, dan apakah sejalan dengan hukum Engel? | Indonesia food share stayed within **42.55–46.70%** (43.85 in 2007, 46.04 in 2025) | Trend over a long national series | Flat, not falling. Mention nominal versus real values and the change in survey method. Some stored values carry float noise (2017: 46.70124986550341), so round to source precision. |

Item 19 is a national series of 19 rows, which fits an ordinary lookup. Its route is not fixed by this issue: the planner decides, as it does for every question, and the eval scores what the agent does.

## Data issues the analysis must handle

Found while running the probe queries:

- **Province labels differ across datasets.** Datasets 374 and 693 use `KEP. RIAU` and `KEP. BANGKA BELITUNG`. Dataset 431 uses `Kepulauan Riau` and `Kalimantan Utara 1)` (a footnote marker). `dimension_member.canonical_code` is filled only for the national member (`national`). Provinces are left empty, although `geography` already holds 39 province rows.
- **More label variants exist** in the 34–39-member geography dimensions (checked 2026-10-04): a 2-digit code prefix (`32 Jawa Barat`, `34 D I Y`), spelled-out letters (`R i a u`, `B a l i`, `I N D O N E S I A`), dotted abbreviations (`D.K.I. JAKARTA`, `D.I. YOGYAKARTA`), `Bangka Belitung` / `Bangka-Belitung`, and footnotes without a parenthesis (`Kalimantan Utara 1`, `Kalimantan Utara1)`). Some dimensions also hold junk members (`Jumlah`, `Total`, `Catatan:`, bare numbers 1–34), which are not provinces.
- **The province set changes over time.** TPT and P0 have 34 provinces up to 2023 and 38 from 2024. A current province may have no observation in an older table; its formation date alone is not proof that no retrospective series exists.
- **A stable name or code does not guarantee stable boundaries.** Papua and Papua Barat lost territory when new provinces were formed. Keeping the same 34 codes in a panel fixes membership counts but may still compare changed geographic areas. Source footnotes and geography versions matter.
- **Datasets mix several categories.** P0 and Gini combine urban, rural and total with the March and September rounds. TPT combines February and August. Any analysis must filter these explicitly.
- **The national aggregate sits in the same dimension as the provinces** and must be excluded from analysis across provinces.

## Decision

- The existing SQL path owns source discovery and selection. For analysis questions, its new select_series tool validates dataset, measure, exact period IDs, and category member IDs, then returns a structured selection and compact coverage report. Ordinary questions still use get_observations.
- The conditional analyze_data node receives those selections. A shared read-only data-access function loads complete observations from Postgres for typed operations. Raw panels remain in code, outside model context. The analysis agent does not search for the datasets again.
- Missing requested periods or category pins, ambiguous identity, or unverifiable selection metadata block analysis and produce an explicit limitation. Unequal province coverage across valid periods is reported and handled by the chosen operation. Preserve explicit time constraints from the original question through planning and selection; increasing the observation cap alone does not address the failure observed in the baseline trace.
- Province identity and historical coverage are source-backed structured data. A small on-demand geography playbook, potentially a SKILL.md, may guide when to consult the geography resolver and how to explain warnings. The playbook is not authoritative for joins or historical values. This application needs an explicit loader/tool path to use such a file.
- The model selects an operation and verified selection IDs; code computes and formats statistics. Do not execute model-generated Python or SQL.
- Keep analysis questions in the existing benchmark. Add only operations required by the example questions. Item 19 may remain an ordinary lookup.

## Proposed agent graph

~~~text
START
  │
  ▼
plan_question ── direct / plan_failed ─────────────────────────► write_answer
  │
  ├─ context ─► run_retrieval ──────────────────────────────────► write_answer
  │
  ├─ data ────► run_sql ────────┐
  │                              ├─► route_after_sources
  └─ both ────► run_both ───────┘        │
                                        ├─ ordinary question ──► write_answer
                                        ├─ analysis + valid selections
                                        │       ▼
                                        │   analyze_data ───────► write_answer
                                        └─ analysis + invalid/missing selections
                                                └───────────────► write_answer
                                                                      │
                                                        chart_needed? ├─ yes ► build_chart ► END
                                                                      └─ no ─────────────► END
~~~

For an analysis question, run_sql calls search_datasets and describe_dataset as needed, then select_series. The latter checks measure ownership, exact period and member IDs, category pins, province coverage and duplicate cells. It returns a short coverage report to the SQL model and keeps the complete selection in graph state. run_both can retrieve publications for definitions or methodological caveats alongside this SQL work.

The analyze_data model chooses a typed operation using validated selection IDs. Shared loader code materializes all matching rows from the read-only database connection, checks coverage again, and joins by canonical province code. The model sees only compact statistics, counts and caveats. If no valid selections exist, analysis does not run.

This graph is a proposal for review. The linked Langfuse UI and public API denied access, but the trace was inspected through the local ClickHouse events table.

## Proposed work

1. **Make source selection a data contract.**
   - Add requested_analysis to WorkPlan and require a sql_lookup when it is true. Keep direct and document-only paths unchanged.
   - Add planner examples for relationships, dispersion, convergence, paired comparison and residual questions. The planner should not invent dataset IDs before metadata lookup. Add a structured time requirement to the plan (for example, start year 2017 and through-latest-available for “sejak 2017”). Validate it against explicit years and temporal wording in the original user question; reject or retry a rewrite such as `201?` that loses the requested scope.
   - Add select_series to the SQL agent. It takes dataset_id, measure_id, exact period_ids and pinned member_ids, and checks them against the plan's time requirement. Validate ownership, one member per non-geography axis, required category pins, complete metadata and nonzero matching rows for every requested period. Report counts and missing provinces by period; unequal province sets are valid inputs for some operations. Reject ambiguous or duplicate province-period cells. Return repairable errors for bad selections.
   - Store validated SeriesSelection objects in graph state, not row arrays. A serializable query specification is enough initially; add an opaque cache or materialized handle only if repeated loading proves necessary. Preserve get_observations for small lookups.
   - Show selections and coverage in traces/evals. A statistical node cannot repair a wrong dataset choice by itself.

2. **Resolve province identity and time coverage.**
   - Use geography and BPS province codes for joins. Add a curated, source-backed alias registry plus conservative normalization of punctuation, spacing, case, code prefixes and footnote markers. Cover every variant under Data issues; never silently fuzzy-match an unknown or ambiguous label.
   - Backfill dimension_member.canonical_code for confirmed province members and populate it on ingestion. Keep national distinct and junk/non-province members unresolved. Audit every benchmark geography dimension for unresolved labels.
   - Report which province codes occur in each selected dataset and period. An absent historical value is missing, not zero. A retrospective value is valid when the source actually publishes it, even if the province was formed later.
   - Track geography_version and source notes for boundary changes. The old and new Papua/Papua Barat observations may cover different territories despite matching names or codes. Do not combine published percentages across those boundaries without compatible counts or a source-provided harmonized series.
   - Expose a targeted resolve_geography or coverage tool. If a domain SKILL.md is added, use it for workflow guidance and test that on-demand loading improves agent choices; do not duplicate province facts inside the prompt.

3. **Add analysis models and routing.**
   - SeriesSelection: selection ID, dataset/measure IDs, period IDs and labels, pinned member IDs, unit/source metadata, coverage counts and warnings.
   - AnalysisRequest: typed operation, selection IDs, same_provinces where relevant and top_k.
   - AnalysisResult: result ID, operation, computed statistics and rows, sample sizes, input observation IDs, selection IDs, and exclusion/comparability notes.
   - Add series_selections, analysis_results and analysis_error to state. Route to analyze_data only when requested_analysis has valid required selections; otherwise write a specific limitation. Record analysis status in step_status.

4. **Load and compute outside model context.**
   - Add one shared read-only load_series(selection) function returning observation ID, province code, period, value and source metadata. It uses the validated selection contract from select_series and checks that coverage has not drifted.
   - Reject nonnumeric/status values where numbers are required, unresolved province identity, duplicate province-period cells and unexpected count drift. Report exclusions and sample size for each period.
   - Implement only these typed operations:

   | Operation | Inputs | Computed output | Used by |
   |---|---|---|---|
   | correlation | 2 series, 1 period | Pearson r, Spearman rho, n | 12, 13, 18 |
   | ols | x and y series, 1 period | slope, intercept, R², n, signed top-k residuals | 12, 13, 17 |
   | dispersion | 1 series, at least 2 periods | mean, SD, CV, n per period and exclusions | 14 |
   | convergence | 1 series, 2 periods | regression of percentage-point change on initial value: slope, r, n | 15 |
   | paired_compare | 2 series, matching periods | n, count first lower, mean difference per period | 16 |

   - Join across datasets by canonical province code and compatible period. For dispersion, show both available-province and common-code panels with sample sizes; flag the Papua boundary caveat. Compare the results before attributing the 2024 jump solely to entering provinces. Defer p-values and SciPy until a question or eval requires significance testing.

5. **Ground the final answer.**
   - The model may choose and interpret an operation but does not propose numeric results for acceptance. Computation is deterministic; rerun only for drift/corruption checks.
   - Give AnswerDraft analysis-result references alongside observation/document IDs. Format statistics from stored results at source-appropriate precision and check that the final analysis numbers match them. Cite source datasets, sample sizes, exclusions, boundary/method limits, and correlation-versus-causation when relevant.
   - On selection or analysis failure, state the limitation. Keep raw rows out of the answer and chart prompts. A future analysis chart must use verified result rows.

6. **Evaluate the handoff and answers.**
   - Add items 12–19 to demografi_sosial_questions.json and bump its version. Recompute expected statistics with the final loader and operations; investigate deviations from the probes. For item 14, calculate 2024–2026 CV on the common-code panel and inspect boundary notes.
   - Run the existing benchmark. Inspect selected series, coverage, operation, exclusions and answer beside expected output. Add a small explicit check for required analysis facts and caveats, since current referenceless metrics can miss wrong numbers.
   - Include tests for alias variants, unknown province, absent historical value, published retrospective value, changing province set, same-code boundary warning, missing category pin, truncated metadata and ordinary lookup routing.
   - Add the linked “sejak 2017” question as a regression for plan fidelity: the standalone question must retain 2017, selection must include 2017 through the latest suitable period, and the answer must not substitute a two-year comparison or a different dispersion measure without saying so.

## Acceptance criteria

- SQL emits validated selections and coverage for items 12–18. Analysis does not rediscover datasets or receive a bulk panel through model context.
- Aliases across datasets 693, 805 and 374 resolve to the same BPS code; unknown or ambiguous names block joins. Historical absence and source-published retrospective values are distinguished.
- Every operation matches hand-computed and NumPy/pandas reference cases. Selection IDs and exact observation IDs make results reproducible.
- Item 14 reports available and common-code CVs with sample sizes, names the four entering provinces, and flags that Papua/Papua Barat boundaries may have changed.
- Incomplete selection yields an explicit limitation. Unsupported analysis numbers in answer text are rejected. Ordinary questions keep their existing path and tests pass.
- A rerun of the linked baseline question does not turn 2017 into `201?`; it uses the requested starting year and full available time span, with an explicit chosen dispersion measure and period category.
- Items 12–18 match recomputed expected values, and item 16 identifies the post-2020 pattern change.

## Out of scope

- Free-form model-generated Python, pandas or SQL execution.
- Causal inference, fixed-effects regressions, clustering, forecasting and geographic reconstruction across changed boundaries.
- Regency/city analysis, inflation-adjusted spending conversion and a separate analysis benchmark.

## Design references

- OpenAI [agent definitions](https://developers.openai.com/api/docs/guides/agents/define-agents) distinguish model context from tool context. Its [orchestration guidance](https://developers.openai.com/api/docs/guides/agents/orchestration) recommends adding specialists when their contract materially differs.
- Anthropic [context-engineering guidance](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) supports compact tool results; its [Agent Skills guidance](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) explains on-demand workflow guidance.
- [SDMX guidance](https://sdmx.org/guidelines/) covers versioned statistical classifications. [BPS table notes](https://www.bps.go.id/id/statistics-table/3/Umpod09GaG5RbTlOYTJwaVFXWnJPRmQ1Wm1wMVp6MDkjMw%3D%3D/luas-kawasan-hutan-dan-konservasi-perairan-sup--1--sup--menurut-provinsi--ha-.html?year=2022) show earlier Papua data can include later provinces.

## Relevant code

- `src/tilik_agent/agent/graph.py`: the question graph and its edges
- `src/tilik_agent/agent/schemas.py`: `WorkPlan`, `State`, `Calculation`, `Analysis`
- `src/tilik_agent/agent/workers.py`: planner prompt, `route_sources()`, `_execute_plan()`
- `src/tilik_agent/agent/answer.py`: `_check_calculation()`, `write_answer()`
- `src/tilik_agent/agent/sql.py`: `get_connection()`, `describe_dataset`, `get_observations`, `build_agent()`, `_sql_step_once()`
- `src/tilik_agent/api.py`: `status_lines()`
- `src/tilik_agent/ingestion/models.py`: `DimensionMember.canonical_code`, `Geography`
- `src/tilik_agent/ingestion/store.py`, `ingestion/parsing.py`: where dimension members are written and labels normalised
- `src/tilik_agent/evals/datasets/demografi_sosial_questions.json`
- `configs/default.yaml`: `dataset_lookup.max_observations`
- `tests/test_agent_pipeline.py`, `tests/ingestion/`
- `examples/analysis_baseline.py`: runs item 14 through the current pipeline

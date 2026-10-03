# Add a statistical analysis step for questions that need more than lookups

## Problem

Tilik was meant to be a data analyst agent, but the current pipeline and eval set never do heavy analysis, even though the data is rich enough for it.

What the pipeline does today:

- `plan_question` produces `sql_lookup`, `retrieve_document`, or `calculate` steps. The planner prompt in `src/tilik_agent/agent/workers.py` says not to create `calculate` steps when the SQL agent can rank inside its lookup.
- `Calculation.operation` in `src/tilik_agent/agent/schemas.py` only allows `difference`, `sum`, `mean`, `ratio`, and `percent_change`. `_check_calculation()` in `src/tilik_agent/agent/answer.py` verifies them against observation values.
- Every observation reaches the model through `get_observations`, capped at `dataset_lookup.max_observations: 100`. One province panel such as P0 by province and area type (dataset 374) has 1,932 rows, so an analysis over provinces and years cannot go through the model.
- The model never joins two tables on province, so it has never met the label mismatches listed below.

What the eval set asks: all 10 items in `src/tilik_agent/evals/datasets/demografi_sosial_questions.json` are "find X, rank it, subtract two numbers". None asks about a relationship, a trend, how spread out the provinces are, or an outlier.

What the data supports (Postgres, checked 2026-10-02):

- 806 parsed datasets and 4,338,383 observations.
- 138 parsed datasets with a province dimension (34–39 members) and 8 or more periods. Most cover 2016/2017–2025. Calorie intake (431) and food/non-food spending (380, 381, 474, 476) cover 2007–2025.
- Indicators that can be joined on province: education (RLS 693, APS 627, APK 598, literacy 591), labour (TPT 805, informal work 794, formal work 781, NEET 783), poverty (P0 374, P1 346, P2 349, poverty line 342, Gini 343), health (AHH 194, smoking 278, measles immunisation 258), housing (sanitation 571, drinking water 560), and population (116).

## Example questions

The results below come from probe queries run directly against Postgres. They are candidate ground truth for the new eval set. Re-check each one with the final analysis code before writing it into `expected_response` (see Proposed work, step 6).

Common filters: provinces only (the `INDONESIA` member is excluded). P0 is `Jumlah` / `Semester 1 (Maret)`. TPT is `Agustus` unless stated otherwise.

| id | Question | Result from probe | Technique | What a correct answer must notice |
|---|---|---|---|---|
| a1 | Apakah provinsi dengan rata-rata lama sekolah lebih tinggi cenderung memiliki TPT lebih rendah pada 2025? Seberapa kuat hubungannya? | n=38, Pearson r = **+0.565**, slope +0.656 pp TPT per year of schooling, R² = 0.319 | Correlation, OLS | The sign is the opposite of what people expect. Say it is correlation, and name urbanisation as a likely confounder. |
| a2 | Seberapa kuat hubungan rata-rata lama sekolah dan persentase penduduk miskin antarprovinsi pada 2025? | n=38, r = **−0.572**, slope −3.150 pp P0 per year of schooling, R² = 0.327 | Correlation, OLS across two datasets | Join on province across datasets 693 and 374. |
| a3 | Apakah kesenjangan persentase penduduk miskin antarprovinsi menyempit sejak 2017? | CV across provinces: 0.531 (2017), 0.535, 0.544, 0.522, 0.502, 0.512, **0.514 (2023)** for n=34; then **0.605 (2024)**, 0.617 (2025), 0.618 (2026) for n=38 | Sigma-convergence (CV per year) | **Trap:** the jump in 2024 comes from the four new Papua provinces entering the data. Compare the same 34 provinces in every year. |
| a4 | Apakah provinsi yang paling miskin pada 2017 mengalami penurunan kemiskinan paling besar hingga 2025? | n=34, r(P0 in 2017, change 2017→2025) = **−0.902**, slope −0.271 | Beta-convergence | Use only provinces that have both years. Say whether the change is in percentage points or relative. |
| a5 | Apakah TPT Agustus biasanya lebih rendah dari TPT Februari di sebagian besar provinsi? | Provinces where August < February: 2017 11/34, 2018 7/34, 2019 6/34, 2020 1/34, 2021 20/34, 2022 19/34, 2023 20/34, 2024 20/38, 2025 14/38 | Paired comparison per year | **The pattern flips after 2020.** One pooled answer would hide this. |
| a6 | Provinsi mana yang TPT-nya paling menyimpang dari yang diperkirakan berdasarkan rata-rata lama sekolahnya pada 2025? | Residuals from TPT ~ RLS: Bali **−3.38**, Jawa Barat **+2.30**, Banten +1.98, DI Yogyakarta −1.74, Papua +1.71, Papua Barat Daya +1.63 | Regression residuals, outliers | Report the residual with the sign and the fitted model, not just the raw TPT ranking. |
| a7 | Seberapa kuat hubungan akses sanitasi layak dan persentase penduduk miskin antarprovinsi pada 2025? | n=38, r = **−0.801** | Correlation | A strong correlation is still not causation. |
| a8 | Bagaimana tren pangsa pengeluaran makanan Indonesia 2007–2025, dan apakah sejalan dengan hukum Engel? | Indonesia food share stayed within **42.55–46.70%** (43.85 in 2007, 46.04 in 2025) | Trend over a long series | Flat, not falling. Mention nominal versus real values and the change in survey method. Some stored values carry float noise (2017: 46.70124986550341), so round to source precision. |

## Data issues the analysis must handle

Found while running the probe queries:

- **Province labels differ across datasets.** Datasets 374 and 693 use `KEP. RIAU` and `KEP. BANGKA BELITUNG`. Dataset 431 uses `Kepulauan Riau` and `Kalimantan Utara 1)` (a footnote marker). `dimension_member.canonical_code` is filled only for the national member (`national`). Provinces are left empty, although `geography` already holds 39 province rows.
- **The province set changes over time.** TPT and P0 have 34 provinces up to 2023 and 38 from 2024.
- **Datasets mix several categories.** P0 and Gini combine urban, rural and total with the March and September rounds. TPT combines February and August. Any analysis must filter these explicitly.
- **The national aggregate sits in the same dimension as the provinces** and must be excluded from analysis across provinces.

## Decision

- Add **typed analysis operations computed in code (pandas)**. The model picks the operation and its inputs; code computes the result and records the observation ids used. Every published number stays traceable and checked.
- Load analysis inputs **straight from Postgres into a DataFrame**. Rows never pass through the model, so the 100-row cap does not apply.
- **No free-form code execution** (the "pandas agent" pattern). It would break the guarantee that every number is checked against stored observations.
- Add only the operations the example questions need (YAGNI). Add more when a new eval question needs one.

## Proposed work

1. **Province identity** (ingestion):
   - Fill `dimension_member.canonical_code` with the BPS province code for every geography member.
   - Match members to `geography` rows on a normalised label: lowercase, `kep.` → `kepulauan`, strip a trailing footnote marker such as `1)`. Keep the national member as `national`.
   - Run a backfill for existing members.
   - Write a test asserting that every geography member in a parsed dataset with 34–39 members gets a code.

2. **Analysis step schema** in `src/tilik_agent/agent/schemas.py`:
   - Add `"analyze"` to `Action`. Like `calculate`, an `analyze` step must depend on an earlier `sql_lookup` that identified the datasets.
   - Add `SeriesSpec`: `dataset_id`, `measure_id`, `periods: list[str]`, `filters: dict[str, str]` (dimension label → member label for non-geography dimensions, e.g. `{"Karakteristik": "Jumlah", "Turunan Tahun": "Semester 1 (Maret)"}`).
   - Add `AnalysisRequest`: `operation`, `series: list[SeriesSpec]`, `same_provinces: bool = True`, `top_k: int = 5`.
   - Add `AnalysisResult`: `operation`, `n`, `statistics: dict[str, float]`, `rows: list[dict]` (per-province or per-period values used), `input_observation_ids: list[int]`, `notes: list[str]`.

3. **Loader** (new module `src/tilik_agent/agent/analysis.py`): `load_series(spec) -> DataFrame` with columns `observation_id`, `province_code`, `period`, `value`.
   - Use the read-only `sql_agent` connection from `get_connection()`.
   - Exclude `national`.
   - Raise when a filter matches no member or leaves more than one row per province and period.

4. **Operations** in the same module, each a plain function from DataFrames to `AnalysisResult`:

   | Operation | Inputs | Statistics | Used by |
   |---|---|---|---|
   | `correlation` | 2 series, 1 period | `pearson`, `spearman`, `n` | a1, a2, a7 |
   | `ols` | y series, x series, 1 period | `slope`, `intercept`, `r2`, `n`, top-k residuals in `rows` | a1, a2, a6 |
   | `dispersion` | 1 series, ≥2 periods | `mean`, `sd`, `cv` per period; `same_provinces` keeps only provinces present in every period, and the note records how many were dropped | a3, a8 |
   | `convergence` | 1 series, 2 periods | OLS of change on initial level: `slope`, `r`, `n` | a4 |
   | `paired_compare` | 2 series (or 2 filter values), same periods | per period: `n`, `count_first_lower`, `mean_diff` | a5 |

   - Join series on `province_code` and `period`. When `same_provinces` drops provinces, list them in `notes`; that note is how a3's trap gets surfaced.
   - Add `scipy` (a version published at least 7 days earlier) for p-values on `correlation` and `ols`. With n=34–38, an analyst answer should report whether r is significant.

5. **Wiring**:
   - In `_execute_plan()` (`workers.py`), route `analyze` steps to a new `_analyze_step()`. It asks the model for an `AnalysisRequest` (using the dataset, measure and member details already in `source_details` from the dependent `sql_lookup`), runs the operation, and stores the result in `intermediate_analysis`.
   - Update the planner prompt so relationship, trend, dispersion, convergence and outlier questions plan `sql_lookup` → `analyze`.
   - In `write_answer()`, cite the `AnalysisResult` statistics and `notes`, and state that a correlation is not causation.
   - `validate_analysis()` re-runs the operation from `input_observation_ids` and rejects a mismatch, mirroring `_check_calculation()`.

6. **Eval set** `src/tilik_agent/evals/datasets/demografi_sosial_analysis_questions.json`, in the same format as the existing file:
   - Contains items a1–a8. `expected_response` holds the statistics and the trap note for each item.
   - Fill the values by running the step 4 operations on the step 3 loader, and compare them with the probe results in the table above. Investigate any difference before writing it in. For a3, also compute the 2024–2026 CV on the same 34 provinces; the probe did not compute it.
   - Run through the existing `tilik benchmark` path. Publish as a separate Langfuse dataset.

## Acceptance criteria

- Every province member in parsed datasets with 34–39 geography members has a `canonical_code`, and a1/a2 join datasets 693, 805 and 374 without any label handling in the analysis code.
- Each operation has a unit test with a small hand-computed DataFrame. `correlation` and `ols` match `pandas`/`numpy` reference values to 1e-9.
- `dispersion` with `same_provinces=True` on P0 2017–2026 uses 34 provinces in every year and lists the four dropped provinces in `notes`.
- An `analyze` result whose statistics disagree with a re-run from its `input_observation_ids` is rejected, and the answer states the limitation instead.
- The answers to a1–a8 report the statistics within rounding of `expected_response`, and the answers to a3 and a5 mention the province-set change and the post-2020 flip respectively.
- The existing single-turn benchmark and tests are unchanged.

## Out of scope

- Free-form Python or pandas code execution by the model.
- Causal inference, panel regressions with fixed effects, clustering, and forecasting. Revisit when an eval question needs one.
- Regency/city (kabupaten/kota) level analysis.
- Real (inflation-adjusted) conversion of spending series.

## Relevant code

- `src/tilik_agent/agent/schemas.py`: `Action`, `PlanStep`, `WorkPlan`, `Calculation`, `Analysis`
- `src/tilik_agent/agent/workers.py`: planner prompt, `_calculate_step()`, `_execute_plan()`, `_merge_result()`
- `src/tilik_agent/agent/answer.py`: `_check_calculation()`, `validate_analysis()`, `write_answer()`
- `src/tilik_agent/agent/sql.py`: `get_connection()`, `describe_dataset`, `get_observations`, harvest functions
- `src/tilik_agent/ingestion/models.py`: `DimensionMember.canonical_code`, `Geography`
- `src/tilik_agent/ingestion/pipeline.py`: where dimension members are written
- `src/tilik_agent/evals/datasets/`, `src/tilik_agent/evals/runner.py`, `src/tilik_agent/evals/langfuse_dataset.py`
- `configs/default.yaml`: `dataset_lookup.max_observations`
- `tests/test_agent_pipeline.py`, `tests/ingestion/`

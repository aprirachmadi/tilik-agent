@AGENTS.md

# Working rules for Tilik

Follow YAGNI, DRY, and KISS (see AGENTS.md). Do what was asked. If a fix needs a decision the user did not make, ask
first: switching a model, turning a feature off, changing a default, or adding a workaround.

## Settings live in `configs/default.yaml`, not `.env`

- The model, endpoint, temperature, thinking, `max_tokens`, structured-output mode, judge, metrics, and every other
  behaviour setting belong in `configs/default.yaml`. Change them there, and only with the user's approval.
- `.env` and `.env.local` hold secrets and connection values only. Every key in them must be listed in
  `.env.example`. Never add a new key to `.env`, `.env.local`, `docker-compose.yml`, or a launch config.
- Never add `os.getenv` switches that override yaml settings or change how the model is called.
- If the model fails (output budget, missing tool call, slow), report it with the trace. Do not silently work around it
  by changing model settings.

## Langfuse is here for a reason: use it

- Langfuse runs at `http://localhost:3210` (separate stack in `../langfuse`). Check it with `Langfuse().auth_check()`.
- Before guessing why an answer went wrong, read its trace. Langfuse v4 has no `trace.list`; use
  `client.api.observations.get_many(trace_id=..., fields="core,basic,io")`.
- Benchmarks and evaluation datasets go into Langfuse Datasets and Experiments, not only local files or `reports/`.
  Give every input the scorer saw its own field (question, generated answer, tool calls, trajectory, trace link via
  `source_trace_id`). Put the run config (models, thresholds, dataset version, code commit) in the run metadata.
- Never type Langfuse credentials into a browser. The user signs in.

## Commands

Load the local environment first:

```bash
set -a; . ./.env.local; set +a
```

| Task | Command |
| --- | --- |
| Start Postgres | `docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.dev.yml up -d postgres` |
| Migrate | `poetry run alembic upgrade head` |
| Ingest | `poetry run tilik ingest bnpb`, `bapanas`, `sipongi`, `catalog`, `table --id <id>` |
| API (port 4321) | `poetry run tilik serve` (preview launch config `api`) |
| Web (port 5173) | `npm --prefix frontend run dev -- --port 5173 --strictPort` (preview launch config `web`) |
| Unit tests | `.venv/bin/python -m pytest -q --ignore=tests/e2e` |
| Lint | `.venv/bin/ruff check src tests` |
| Pipeline e2e | `.venv/bin/python -m pytest tests/e2e/test_pipeline_live.py -k <case>` (in-process, needs Postgres) |
| Live-site e2e | `.venv/bin/python -m pytest tests/e2e/test_live_site.py -k <case>` (needs the API and web servers running) |
| Generation benchmark | `poetry run tilik benchmark` (`src/tilik_agent/evals/datasets/generation_benchmark.json`) |
| Conversation benchmark | `poetry run tilik benchmark --conversations` |
| Judge benchmark | `poetry run tilik benchmark --judges` |
| Benchmark subset | add `--n-samples N --seed 42` |

Benchmarks and e2e tests make real model calls and cost time and credits. Run them only when the user asks.

## Where tests go

- Unit tests go in `tests/`. Integration tests, meaning anything that asserts and fails, including live e2e checks,
  go in `tests/e2e/` as pytest tests.
- `examples/` may hold runnable e2e runs of the pipeline or evals that print results for a person to read. If the
  script checks results and fails on a wrong one, it is an integration test and belongs in `tests/e2e/`.

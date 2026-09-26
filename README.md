<div align="center">

# Tilik

**Answers a question about BPS statistics from published observations and the SP2020 demography publication.**

![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2-1C3C3C?logo=langgraph&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-0.3-1C3C3C?logo=langchain&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-2.13-E92063?logo=pydantic&logoColor=white)
![Poetry](https://img.shields.io/badge/Poetry-dependency%20manager-60A5FA?logo=poetry&logoColor=white)

</div>

Tilik plans the work, looks up figures in Postgres, searches the publication in pgvector, and writes an answer with the observation and document ids it used. When the question asks for a comparison, the response also includes a chart spec a notebook or frontend can draw. It does not search the web.

## Contents

- [Quick start](#quick-start)
- [Pipeline](#pipeline)
- [Ask a question](#ask-a-question)
- [Configuration](#configuration)
- [Ingest](#ingest)

## Quick start

You need Python 3.11–3.13, [Poetry](https://python-poetry.org/), and Postgres. The observation tables must already be loaded, and the SP2020 chunks must already be in the `bps-sp2020-demografi` pgvector collection.

```powershell
poetry install
copy .env.example .env
poetry run tilik serve
```

Fill in `.env` before serving. The API listens on [http://127.0.0.1:4321](http://127.0.0.1:4321).

| Variable | Used for |
| --- | --- |
| `OPENAI_API_KEY` | Chat model. `configs/default.yaml` names this variable in `llm.api_key_env`. |
| `SQL_AGENT_PASSWORD` | Read-only `sql_agent` role used while answering. |
| `BPS_API_KEY` | Catalog and table ingest. |

`notebooks/test_e2e_agent.ipynb` streams the same graph and draws the chart with Plotly.

## Pipeline

`compile_graph()` in `tilik_agent.agent` builds the graph below. `plan_question` returns a short plan whose steps are `sql_lookup`, `retrieve_sp2020`, or `calculate`.

```mermaid
flowchart TD
  startNode([START]) --> plan_question
  plan_question -->|context| run_retrieval
  plan_question -->|data| run_sql
  plan_question -->|both| run_both
  plan_question -->|limited| write_answer
  run_retrieval --> write_answer
  run_sql --> write_answer
  run_both --> write_answer
  write_answer -->|yes| build_chart
  write_answer -->|no| endNode([END])
  build_chart --> endNode
```

| Route | What runs |
| --- | --- |
| `data` | SQL lookups only. |
| `context` | SP2020 search only. |
| `both` | The plan's mix of SQL lookups and SP2020 search. |
| `limited` | The plan did not validate, so the run goes straight to an answer that says so. |

| Step | What it does |
| --- | --- |
| `sql_lookup` | Inner tool agent. It may call `search_datasets`, `describe_dataset`, and `get_observations` through the read-only `sql_agent` role. Values are harvested from the tool results. The agent's reply is only a status. |
| `retrieve_sp2020` | Searches the pgvector collection, grades each excerpt, and rewrites the query up to `retrieval.max_rewrites` times. |
| `calculate` | Proposes arithmetic over observation ids. The result is checked against the stored values before it is kept. |
| `write_answer` | Writes the answer from the gathered evidence. |
| `build_chart` | Adds a chart spec when the question asks for a comparison and at least two cited values exist. The graph returns the spec and does not open a figure. |

## Ask a question

`POST /questions` with a JSON body. The server streams the graph and returns the merged state.

```json
{ "question": "Provinsi mana saja yang memiliki tiga TPT tertinggi pada Agustus 2025?" }
```

```bash
curl -s -X POST http://127.0.0.1:4321/questions \
  -H "Content-Type: application/json" \
  -d '{"question": "Provinsi mana saja yang memiliki tiga TPT tertinggi pada Agustus 2025, dan berapa Angka Partisipasi Sekolah kelompok umur 16-18 tahun di masing-masing provinsi tersebut pada 2025?"}'
```

PowerShell parses JSON quotes unless you stop it:

```powershell
curl.exe --% -s -X POST http://127.0.0.1:4321/questions -H "Content-Type: application/json" -d "{\"question\": \"Provinsi mana saja yang memiliki tiga TPT tertinggi pada Agustus 2025, dan berapa Angka Partisipasi Sekolah kelompok umur 16-18 tahun di masing-masing provinsi tersebut pada 2025?\"}"
```

| Field | Meaning |
| --- | --- |
| `answer` | Text shown to the user. |
| `chart` | Chart spec, or `null`. |
| `answer_observation_ids` | Observation ids cited in the answer. |
| `answer_document_ids` | SP2020 document ids cited in the answer. |
| `step_status` | Status of each planned step. |
| `plan_error` | Set when the plan could not be validated. |
| `sql_error` | Set when a SQL step failed. |
| `sql_warnings` | Non-fatal SQL notes, such as a tool-round cap. |
| `retrieval_error` | Set when SP2020 search failed. |

A chart spec has `type` (`bar` or `line`), `title`, `x_axis`, `y_axis`, and `data`. Each point has `label`, `series`, `value`, `unit`, and `observation_id`.

## Configuration

`configs/default.yaml` holds the chat model, the embedding model, retrieval limits, dataset-lookup caps, and `graph.recursion_limit`. Saving the file is picked up by the running process. The chat client and SQL agent are rebuilt when the `llm` block changes. The vector store is rebuilt when the embedding model, collection, or connection string changes.

| Block | What you can change |
| --- | --- |
| `llm` | `model`, `base_url`, `temperature`, `api_key_env`, `extra_body` |
| `embedding` | `model`, `device`, `normalize_embeddings` |
| `retrieval` | `collection`, `top_k`, `max_rewrites` |
| `dataset_lookup` | `max_search_results`, `max_measures`, `max_dimension_members`, `max_periods`, `max_observations`, `max_tool_rounds` |
| `graph` | `recursion_limit` |

These variables override the file when they are set:

| Variable | Overrides |
| --- | --- |
| `TILIK_MODEL` | `llm.model` |
| `TILIK_LLM_BASE_URL` | `llm.base_url` |
| `SP2020_COLLECTION` | `retrieval.collection` |
| `PGVECTOR_CONNECTION` | pgvector URL. The default is the local `tilik` database. |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` | Observation database used by SQL lookup. |

Secrets stay in `.env`. The yaml file only names the API-key variable.

## Ingest

Ingest writes through `database_url` (`postgresql+psycopg://tilik:tilik@localhost:5432/tilik` by default). That role is separate from the read-only `sql_agent` role used by questions.

| Command | Effect |
| --- | --- |
| `poetry run tilik ingest catalog` | Load the national BPS subject catalog. |
| `poetry run tilik ingest run` | Load every catalogued table. A stopped run resumes finished periods. |
| `poetry run tilik ingest table --id <bps-table-id>` | Load one catalogued table, including every published year. |

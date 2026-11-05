When writing code, agents should follow YAGNI, DRY, and KISS Principle

## Commit rule
dont write yourname or co-authored yourself in the commit messages

## Local services
Do not guess ports or start ad-hoc containers; these are the only values.

- Postgres (pgvector): `127.0.0.1:5431`, started with
  `docker compose --env-file .env.local -f docker-compose.yml -f docker-compose.dev.yml up -d postgres`
  (container `tilik-agent-postgres-1`, volume `tilik-agent_pgvector_storage`).
- The port lives only in `POSTGRES_PORT`. Leave `PGVECTOR_CONNECTION` blank in `.env.local`; the app derives the
  vector URL from `POSTGRES_*`. If a port ever changes, edit `POSTGRES_PORT` and `docker-compose.dev.yml`, nothing else.
- Langfuse: `http://localhost:3210` (separate stack in `../langfuse`). Check with `Langfuse().auth_check()`.
- Ollama: `http://127.0.0.1:11434/v1`.
- Python env: `.venv` (Linux, Python 3.12). `.env.local` overrides `.env` for the e2e scripts.

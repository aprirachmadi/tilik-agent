#!/bin/bash
# Sourced by the Postgres image on first boot. Do not exit.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=agent_password="$SQL_AGENT_PASSWORD" \
  --set=db_name="$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;
SELECT CASE
  WHEN EXISTS (SELECT FROM pg_roles WHERE rolname = 'sql_agent')
  THEN format('ALTER ROLE sql_agent WITH LOGIN PASSWORD %L', :'agent_password')
  ELSE format('CREATE ROLE sql_agent LOGIN PASSWORD %L', :'agent_password')
END
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO sql_agent', :'db_name')
\gexec
GRANT USAGE ON SCHEMA public TO sql_agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sql_agent;
SQL

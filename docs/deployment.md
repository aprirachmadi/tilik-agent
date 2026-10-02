# Deploy

One Ubuntu server runs the website, the API, and Postgres. The public address is `https://tilikagent.frhvn.my.id`. Postgres is only reachable inside Docker. Passwords and API keys stay in `.env` on the server. That file is not committed, and it is not copied into the images.

The observations and the SP2020 vectors move as a database dump. You do not run ingest for that move.

Before working on the VPS, commit and push the deployment files to `main`. The VPS clones from GitHub and cannot see local uncommitted files. Review the staged paths first; this workspace also contains unrelated untracked files. Keep `.env`, PDFs, and `*.dump` out of Git.

From the Windows repository, stage the tracked changes and the new deployment files explicitly:

```powershell
git add -u
git add .dockerignore Dockerfile docker-compose.dev.yml deploy/caddy/Caddyfile deploy/postgres/init-db.sh frontend/.dockerignore frontend/Dockerfile frontend/nginx.conf docs/deployment.md src/tilik_agent/ingestion/pdf_vectors.py tests/ingestion/test_pdf_vectors.py tests/test_settings.py alembic.ini alembic/env.py alembic/script.py.mako alembic/versions/20260923_0001_observation_schema.py
git diff --cached --stat
git diff --cached --check
git commit -m "Add VPS deployment stack"
git push origin main
```

Review `git diff --cached --stat` before committing. The `.issues/`, `examples/`, `AGENTS.md`, `frontend/nul`, and `plan_deployment.md` files are outside this deployment commit.

## DNS

At the DNS host for `frhvn.my.id`, add an A record.

| Name | Type | Value |
| --- | --- | --- |
| `tilikagent` | A | The VPS IPv4 address |

Wait until `tilikagent.frhvn.my.id` resolves to that address before starting the stack. Caddy asks Let's Encrypt for a certificate, and that fails if the name does not point here yet.

## Server setup

These commands assume Ubuntu and an SSH user with `sudo`. If Docker Engine and the Compose plugin are already installed, check `docker compose version` and skip the installation. For a new production server, use [Docker's Ubuntu apt repository instructions](https://docs.docker.com/engine/install/ubuntu/) instead of the convenience installer.

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in so the `docker` group applies, then run `docker compose version`. Check `df -h` before building: the locally tested API image is about 5.7 GB and the current source database is about 2.6 GB; the build, dump, and backups need additional disk space.

```bash
sudo mkdir -p /opt/tilik
sudo chown "$USER:$USER" /opt/tilik
git clone https://github.com/aprirachmadi/tilik-agent.git /opt/tilik
cd /opt/tilik
cp .env.example .env
```

Edit `.env` and set `POSTGRES_PASSWORD`, `SQL_AGENT_PASSWORD`, and `OPENAI_API_KEY`, then run `chmod 600 .env`. The server can use new database passwords; the dump does not carry the Windows login passwords. `BPS_API_KEY` is only needed if you later rebuild the observations with ingest instead of a dump. If the GitHub repository is private, give the VPS SSH user access before cloning.

Open SSH, port 80, and port 443 in the VPS provider firewall and, if used, UFW. Do not open 5432. Docker warns that published container ports can bypass UFW rules; this Compose file publishes only Caddy's 80 and 443 in production.

If you use UFW, allow SSH before enabling it:

```bash
sudo apt-get install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Port 80 stays open because Let's Encrypt uses it to check the domain. Browsers use 443.

## Move the database

On the Windows machine that already has the data, dump the running database. This uses its current container directly, so the new Compose file and its required environment variables are not involved. PowerShell's `>` redirection can corrupt the binary dump.

```powershell
docker exec tilik-agent-postgres-1 pg_dump -U tilik -d tilik -Fc --no-owner --no-acl -f /tmp/tilik.dump
docker cp tilik-agent-postgres-1:/tmp/tilik.dump .\tilik.dump
```

On bash, this is the same dump:

```bash
docker compose exec -T postgres pg_dump -U tilik -d tilik -Fc --no-owner --no-acl > tilik.dump
```

`tilik.dump` holds the observation tables and the SP2020 rows in `langchain_pg_embedding`. Do not commit it. The source currently has 4,338,383 observations, 889 embeddings in `bps-sp2020-demografi`, and Alembic revision `20260927_0003`. Copy the dump to the server:

```bash
scp tilik.dump VPS_USER@VPS_IP:/opt/tilik/tilik.dump
```

A dump does not include login passwords. On the server, start Postgres alone so the first boot can create the empty database, the `vector` extension, and the `sql_agent` role from `.env`.

```bash
cd /opt/tilik
docker compose up -d --wait postgres
docker compose exec -T postgres pg_restore -U tilik -d tilik --clean --if-exists --no-owner --no-acl < tilik.dump
docker compose exec -T postgres psql -U tilik -d tilik -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent;"
docker compose build api web
docker compose run --rm api alembic upgrade head
docker compose up -d
```

Use this restore command only on the new, empty server database. `--clean --if-exists` replaces the `vector` extension that initialization created, so the restore exits without a duplicate-extension error. Check the rows before using the site:

```bash
docker compose exec -T postgres psql -U tilik -d tilik -c "SELECT count(*) FROM observation;"
docker compose exec -T postgres psql -U tilik -d tilik -c "SELECT c.name, count(e.uuid) AS embeddings FROM langchain_pg_collection c LEFT JOIN langchain_pg_embedding e ON e.collection_id = c.uuid GROUP BY c.name;"
docker compose exec -T postgres psql -U tilik -d tilik -c "SELECT version_num FROM alembic_version;"
```

Expect 4,338,383 observations, 889 embeddings in `bps-sp2020-demografi`, and revision `20260927_0003`, unless the Windows database changed after these counts were recorded. `alembic upgrade head` changes nothing when the dump is already at the current revision. If the username in `.env` is not `tilik`, use that username in the `psql` and `pg_restore` commands.

```bash
docker compose ps
curl -fsS https://tilikagent.frhvn.my.id/api/health
```

The health response should be `{"status":"ok"}`. Open the website and ask one question that uses the restored observations or publication. If HTTPS fails, check `docker compose logs caddy`; verify DNS and inbound ports 80 and 443 first.

The site is `https://tilikagent.frhvn.my.id`.

## Empty database

Use this only when you have no dump. `ingest run` needs `BPS_API_KEY` and can take a long time. A stopped run continues on the next start. Place the SP2020 PDF at `data/docs/statistik-demografi-indonesia--hasil-sensus-penduduk-2020.pdf` on the server. That PDF is not in Git. `ingest pdf` replaces the named collection, so choose a different name for another publication. Set `RETRIEVAL_COLLECTION` in `.env` to the collection the question agent should search.

```bash
docker compose up -d postgres
docker compose build api web
docker compose run --rm api alembic upgrade head
docker compose up -d
docker compose exec -T postgres psql -U tilik -d tilik -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent;"
docker compose exec api tilik ingest catalog
docker compose exec api tilik ingest run
docker compose exec api tilik ingest pdf --pdf data/docs/statistik-demografi-indonesia--hasil-sensus-penduduk-2020.pdf --collection bps-sp2020-demografi
```

## Backup and restore

```bash
mkdir -p "$HOME/tilik-backups"
docker compose exec -T postgres pg_dump -U tilik -d tilik -Fc --no-owner --no-acl > "$HOME/tilik-backups/tilik-$(date +%F).dump"
```

To restore, stop the API and the website first, then load the file. Leave the `pgvector_storage` volume in place.

```bash
docker compose stop api web caddy
docker compose exec -T postgres pg_restore -U tilik -d tilik --clean --if-exists --no-owner --no-acl < "$HOME/tilik-backups/tilik-YYYY-MM-DD.dump"
docker compose exec -T postgres psql -U tilik -d tilik -c "GRANT SELECT ON ALL TABLES IN SCHEMA public TO sql_agent;"
docker compose start api web caddy
```

Keep dump files on the server. Do not commit them.

## Update

```bash
cd /opt/tilik
git pull
docker compose build api web
docker compose run --rm api alembic upgrade head
docker compose up -d
```

Do not delete the `pgvector_storage` volume. That volume is the database.

## Local Postgres

Notebooks on your computer can still reach Postgres on port 5431:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres
```

`POSTGRES_PASSWORD` has to be in `.env`. A volume that already exists keeps the password it was created with.

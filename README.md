<p align="center">
  <img src="https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/docs/assets/banner.svg" alt="Otclick" width="100%"/>
</p>

<h1 align="center">Otclick 🤖</h1>

<p align="center">
  <strong>Self-hosted AI-assisted job application automation for hh.ru / hh.kz</strong>
</p>

> Active fork: `gest0r1/Otclick-hh`. Production install/update tracks this repository's `main` branch.

---

## Quick Start

### Production requirements

- Ubuntu/Linux amd64;
- `bash`, `git`, `curl`, Python 3, `gzip`, `sha256sum`;
- Docker Engine with Docker Compose v2 (`docker compose`).

### Install or update — one command

Use the same command for the first installation and every later update:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash
```

The canonical production directory is:

```text
/opt/otclick-hh
```

The installer:

1. clones or fast-forwards `main` in `/opt/otclick-hh`;
2. creates `.env` only for a genuinely fresh installation;
3. preserves the existing `.env`, PostgreSQL volume, accounts, tokens and encrypted HH credentials on updates;
4. refuses to generate new secrets if an existing Otclick PostgreSQL container/volume is detected but `.env` is missing;
5. waits for an **exact-commit prebuilt release** produced by GitHub Actions;
6. verifies `manifest.json` and SHA-256 checksums;
7. loads the ready backend/frontend Docker images;
8. pulls only third-party infrastructure images;
9. runs database migrations and recreates the application layer with `--no-build`.

### Production build policy

**Production installation/update never builds backend or frontend on the server.**

Application images are built by `.github/workflows/build-artifact.yml` in GitHub Actions and published as an exact-commit prerelease:

```text
install-<git-sha>
```

If that artifact is not ready or the artifact workflow failed, the installer waits and then fails. It does **not** silently fall back to `docker compose build`.

This distinction is intentional:

```text
Development machine: source -> local build/test -> GitHub
Production server:    GitHub exact commit -> prebuilt images -> run/test
```

### Existing installation

The update command is still exactly the same:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash
```

Do not run:

```bash
python3 infra/bootstrap.py --force
```

on an existing installation. It rotates PostgreSQL/JWT/Fernet secrets and can invalidate sessions or make stored encrypted HH credentials unreadable.

### Explicit installation directory

`/opt/otclick-hh` is the supported default. An override is available when deliberately needed:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash -s -- /some/other/path
```

---

## What it does

Otclick automates routine parts of working with hh.ru/hh.kz while keeping user-controlled actions visible in the web interface.

- Vacancy search, saved filters, deduplication and exclusions.
- Optional AI relevance screening.
- Vacancy-specific cover letters generated from the resume and vacancy.
- Background apply worker with throttling, limits and retries.
- Draft answers for vacancy forms/tests.
- Recruiter conversation support and todo flow.
- Captcha handoff for manual handling.
- Application analytics and negotiation state.
- Employer blacklist.
- Firefox extension for assisted external forms.
- Self-hosted Supabase: PostgreSQL, Auth, Realtime and Storage.

---

## Architecture

```text
Browser / phone
      │
      ▼
Next.js frontend
      │ JWT
      ▼
FastAPI API ───────────────► hh.ru / hh.kz / chatik.hh.ru
      │
      ├────────► PostgreSQL / Supabase Auth / Realtime / Storage
      │
      └────────► OpenAI-compatible LLM endpoint (optional)

Background worker ─────────► search / relevance / apply / recruiter polling
```

Production deployment uses:

```text
docker-compose.yml
+ docker-compose.prebuilt.yml
```

`docker-compose.prebuilt.yml` supplies runtime frontend configuration for the generic prebuilt Next.js image. `NEXT_PUBLIC_*` values are inserted when the container starts instead of being permanently tied to CI values.

---

## Configuration

The canonical environment file is:

```text
/opt/otclick-hh/.env
```

`infra/bootstrap.py` generates secrets only for a fresh installation. Back this file up securely, especially `FERNET_KEY`.

Important generated secrets include:

- `POSTGRES_PASSWORD`;
- `JWT_SECRET`;
- `ANON_KEY` / `SUPABASE_ANON_KEY`;
- `SERVICE_ROLE_KEY` / `SUPABASE_SERVICE_ROLE_KEY`;
- `FERNET_KEY`;
- `INTERNAL_CRON_TOKEN`.

Typical AI settings:

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=...
AI_POSITIONING=balanced
```

Without `OPENAI_API_KEY` the core stack can still start; AI-dependent functions remain unavailable or use their fallback behavior.

---

## Database migrations

SQL migrations live in:

```text
infra/supabase/migrations/
```

The one-shot `migrate` service applies migrations not yet recorded in `public.schema_migrations`.

Check migration output:

```bash
cd /opt/otclick-hh
docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml logs migrate
```

Never delete the database volume during a normal update.

---

## Operations

### Update

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash
```

### Status

```bash
cd /opt/otclick-hh
docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml ps -a
```

### Logs

```bash
cd /opt/otclick-hh
docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml logs -f --tail=200 api
docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml logs -f --tail=200 worker
docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml logs --tail=200 migrate
```

### Restart an application service

```bash
cd /opt/otclick-hh
docker compose -f docker-compose.yml -f docker-compose.prebuilt.yml restart api worker frontend
```

### Health check

```bash
curl -fsS http://127.0.0.1:8000/health
```

### Important production rule

Do not use this as an update command:

```bash
docker compose up -d --build
```

That is a development/local-build path and bypasses the prebuilt artifact contract.

---

## Development

Local development is allowed to build from source. Production is not.

### Backend

```bash
git clone https://github.com/gest0r1/Otclick-hh.git
cd Otclick-hh
uv sync --dev
python3 infra/bootstrap.py --openai-key ""
docker compose up -d db migrate auth rest realtime storage kong
cd backend
uv run uvicorn app.main:app --reload
```

Checks:

```bash
uv run ruff check backend
cd backend && uv run pytest tests -q
```

### Frontend

```bash
cd frontend
npm ci
cp .env.local.example .env.local
npm run dev
```

Checks:

```bash
npx tsc --noEmit
npm test
```

### Firefox extension

```bash
cd ext
npm ci
npx tsc --noEmit
npm test
npm run build
```

---

## CI / artifact pipeline

`.github/workflows/ci.yml` validates backend, frontend and extension tests.

`.github/workflows/build-artifact.yml` is the production image pipeline. For each relevant pushed commit it:

1. validates installer scripts;
2. builds `aiautoclicker-backend:latest`;
3. builds a generic `aiautoclicker-frontend:latest` with runtime placeholders;
4. verifies required placeholders are present;
5. packs both images into `otclick-images-linux-amd64.tar.gz`;
6. publishes `manifest.json`, `SHA256SUMS` and the image bundle in `install-<git-sha>`.

The installer accepts only the artifact whose manifest SHA exactly matches the checked-out repository SHA.

---

## Project structure

```text
Otclick-hh/
├── install.sh                       # production one-command install/update
├── docker-compose.yml               # base self-hosted stack
├── docker-compose.prebuilt.yml      # production prebuilt-image/runtime-env override
├── .env.example
├── .github/workflows/
│   ├── ci.yml
│   └── build-artifact.yml
├── backend/
├── frontend/
├── ext/
├── infra/
│   ├── bootstrap.py
│   ├── frontend-runtime-env.sh
│   └── supabase/
│       ├── migrate.sh
│       └── migrations/
├── docs/
├── pyproject.toml
└── uv.lock
```

---

## Security notes

- Never commit `.env`.
- Keep `FERNET_KEY` backed up offline.
- Never regenerate secrets automatically for an existing database.
- Production updates must come from the exact Git commit and its matching verified artifact.
- Never use `docker compose down -v` for a normal update.

---

## License and upstream

This repository is based on the open-source Otclick project and retains the existing [`LICENSE`](LICENSE). For this deployment, use `https://github.com/gest0r1/Otclick-hh` as the source of truth.

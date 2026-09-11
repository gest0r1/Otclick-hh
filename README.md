<p align="center">
  <img src="https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/docs/assets/banner.svg" alt="Otclick" width="100%"/>
</p>

<h1 align="center">Otclick 🤖</h1>

<p align="center">
  <strong>Self-hosted AI-assisted job application automation for hh.ru / hh.kz</strong>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#what-it-does">Features</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#development">Development</a> •
  <a href="#operations">Operations</a>
</p>

> This repository is the actively used fork: `gest0r1/Otclick-hh`.
> Install and update commands below intentionally track this fork's `main` branch.

---

## Quick Start

### Requirements

For the recommended Docker installation you need:

- Linux, macOS, or Windows with WSL2;
- `bash`;
- `git`;
- Python 3;
- Docker Engine / Docker Desktop with **Docker Compose v2** (`docker compose`).

Python 3.13, `uv`, and Node.js are only required for local development outside Docker.

### Install **or update** with one command

Use the same command for a fresh installation and every later update:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash
```

The installer:

1. installs into `$HOME/Otclick-hh` on a new machine;
2. automatically uses `/home/app/app` when that existing production checkout is found;
3. clones or fast-forwards `main` from `https://github.com/gest0r1/Otclick-hh.git`;
4. creates the root `.env` **only on the first install**;
5. preserves the existing `.env`, database volumes, accounts, tokens, and application data on updates;
6. rebuilds and starts the Docker Compose stack;
7. runs pending database migrations through the Compose `migrate` service.

To use an explicit installation directory:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash -s -- /opt/otclick
```

The update command is deliberately identical to the install command. Do **not** run
`infra/bootstrap.py --force` during an update: it rotates PostgreSQL/JWT/Fernet secrets and can
invalidate sessions and make previously encrypted hh credentials unreadable.

After the stack starts, open:

```text
http://localhost:3000
```

On the first install `OPENAI_API_KEY` is left empty intentionally. Edit the generated root `.env`
and set an OpenAI-compatible provider when AI features are needed, then run the same install/update
command again to rebuild the services.

### Production server already installed in `/home/app/app`

No special command is required. The installer detects this checkout automatically:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash
```

If tracked files contain uncommitted local changes, the updater stops instead of overwriting them.
Commit or stash those changes first.

### Manual install

If you prefer not to pipe a remote script into Bash:

```bash
git clone https://github.com/gest0r1/Otclick-hh.git
cd Otclick-hh
python3 infra/bootstrap.py --openai-key ""
docker compose up -d --build
```

Manual update:

```bash
cd /path/to/Otclick-hh
git pull --ff-only origin main
docker compose up -d --build
```

---

## What it does

Otclick automates the routine parts of working with hh.ru/hh.kz while keeping user-controlled
steps visible in the web interface.

- **Vacancy search and filters** — saved search filters, deduplication, exclusions and search tuning.
- **AI relevance screening** — optional LLM scoring before a vacancy enters the application flow.
- **Vacancy-specific cover letters** — generated from the resume and vacancy; current prompt uses a
  full structured letter with relevant achievements rather than the legacy 2–3 sentence format.
- **Auto-apply worker** — background processing with throttling, limits, retries and application state.
- **Vacancy forms/tests** — AI-generated drafts for questions that require additional answers.
- **Recruiter conversations** — polling, draft responses, escalation and todo flow; messages are not
  sent automatically without the corresponding user action.
- **Captcha handoff** — the worker pauses and exposes the captcha for manual handling.
- **Analytics** — application funnel and mirrored negotiation state.
- **Blacklist** — manual and automatic employer exclusions.
- **Firefox extension** — assisted filling of external forms; see [`ext/README.md`](ext/README.md).
- **Self-hosted Supabase** — PostgreSQL, Auth, Realtime and Storage run in the same Compose stack.

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

The root `docker-compose.yml` is the canonical self-hosted runtime. It starts the local Supabase
services, API, worker, frontend and the one-shot migration service.

---

## Configuration

There is one canonical environment file for the Docker stack:

```text
.env
```

It lives in the repository root. `infra/bootstrap.py` generates the secrets required for a fresh
installation. **Back up this file securely**, especially `FERNET_KEY`.

Typical AI settings:

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=...
AI_POSITIONING=balanced
```

Any OpenAI-compatible endpoint can be used. For example, a local Ollama instance reachable from
Docker can be configured as:

```env
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_MODEL=qwen3:8b
OPENAI_API_KEY=ollama
```

Without `OPENAI_API_KEY` the core stack still starts. AI-dependent functions either use their
fallback behavior or remain unavailable until an AI provider is configured.

### Important generated secrets

`infra/bootstrap.py` generates and writes, among others:

- `POSTGRES_PASSWORD`;
- `JWT_SECRET`;
- `ANON_KEY` / `SUPABASE_ANON_KEY`;
- `SERVICE_ROLE_KEY` / `SUPABASE_SERVICE_ROLE_KEY`;
- `FERNET_KEY`;
- `INTERNAL_CRON_TOKEN`.

Do not regenerate them on an existing installation unless you intentionally plan a secret rotation.

---

## Database migrations

SQL migrations live in:

```text
infra/supabase/migrations/
```

On `docker compose up`, the one-shot `migrate` service applies migrations not yet present in
`public.schema_migrations`. As of this README the repository contains migrations through
`034_cover_letter_prompt_version.sql`.

Check migration output:

```bash
docker compose logs migrate
```

Check the ledger:

```bash
docker exec -it aiautoclicker-db psql -U postgres -d postgres -c \
  "select version, applied_at from schema_migrations order by version"
```

The cover-letter update introduced migration `034`, which adds prompt-version tracking so legacy
cached short letters are regenerated by the current prompt.

---

## Operations

### Update

Recommended — one command from any directory:

```bash
curl -fsSL https://raw.githubusercontent.com/gest0r1/Otclick-hh/main/install.sh | bash
```

Or manually:

```bash
cd /home/app/app   # production default used by this deployment; adjust if needed
git pull --ff-only origin main
docker compose up -d --build
```

### Status

```bash
docker compose ps
```

### Logs

```bash
docker compose logs -f --tail=200 api
docker compose logs -f --tail=200 worker
docker compose logs migrate
```

### Restart one service

```bash
docker compose restart api
docker compose restart worker
```

### Health check

On a host where the API is bound locally:

```bash
curl -fsS http://127.0.0.1:8000/health
```

### Rollback

Use a known-good commit, then rebuild. Do not remove volumes and do not regenerate `.env`:

```bash
cd /home/app/app
git log --oneline -10
git checkout <known-good-sha>
docker compose up -d --build
```

Return to current `main` later with:

```bash
git checkout main
git pull --ff-only origin main
docker compose up -d --build
```

---

## Development

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

Run checks used by CI:

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

See [`ext/README.md`](ext/README.md) for browser-specific setup.

---

## CI

GitHub Actions workflow: [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

It validates:

- backend dependency resolution;
- Ruff;
- backend pytest suite;
- frontend TypeScript and tests;
- extension TypeScript, tests and build.

A green CI run means the repository passed those automated checks; deployment remains an explicit
self-hosted operation through the installer / Docker Compose.

---

## Project structure

```text
Otclick-hh/
├── install.sh                    # one-command install/update entry point
├── docker-compose.yml            # canonical self-hosted stack
├── .env.example                  # root environment template
├── backend/
│   ├── app/
│   │   ├── ai/
│   │   │   ├── agent.py
│   │   │   ├── prompts.py
│   │   │   └── cover_letter_prompt.py
│   │   ├── api/
│   │   ├── hh/
│   │   ├── services/
│   │   └── worker/
│   ├── tests/
│   └── worker_main.py
├── frontend/                     # Next.js web UI
├── ext/                          # Firefox/WXT extension
├── infra/
│   ├── bootstrap.py              # generates first-install root .env
│   └── supabase/
│       ├── migrate.sh
│       └── migrations/           # SQL schema migrations, currently 001..034
├── docs/
├── pyproject.toml
└── uv.lock
```

---

## Security notes

- Never commit `.env`.
- Keep `FERNET_KEY` backed up offline. Losing it makes stored encrypted hh credentials unreadable.
- The install/update script intentionally does not use `bootstrap.py --force` on existing systems.
- Review remote shell scripts before executing them if the host is security-sensitive; the manual
  clone/update procedure above is equivalent and easier to audit line by line.
- Keep the API behind the configured reverse proxy/TLS for Internet-facing deployments.

---

## License and upstream

This repository is based on the open-source Otclick project and retains the repository's existing
[`LICENSE`](LICENSE). For this deployment and its current development line, use
`https://github.com/gest0r1/Otclick-hh` as the source of truth.

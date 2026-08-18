# rag-masterclass

A locally-runnable Retrieval-Augmented Generation (RAG) web app: a **Chat**
surface (threaded, streaming, retrieval-augmented conversations) and an
**Ingestion** surface (manual upload, live processing status, document
management), covering PRD Modules 1 + 2. Stack: React + TypeScript + Vite +
Tailwind + shadcn/ui (frontend), FastAPI in a Python venv (backend), Supabase
(Postgres + pgvector + Auth + Storage + Realtime). LLM calls use the raw
OpenAI-compatible SDK only -- no LangChain/LangGraph or other orchestration
framework.

> **Status:** this README describes the G-4 foundation slice -- repo scaffold,
> FastAPI skeleton, Supabase schema/migrations, and env config. The only live
> endpoint right now is `GET /api/health`; the frontend is a bare boot
> placeholder. Chat, ingestion, and the real app shell land at later gates
> (G-5a/G-5b/G-6/G-7).

## Architecture note: hosted Supabase, no Docker

This project runs the backend and frontend as **bare local processes** and
talks to a **hosted Supabase project** (not a self-hosted Postgres/Auth/Kong
stack in containers). Docker is not required anywhere in this repo. If you
want to point at a different Supabase project, update `SUPABASE_*` in
`backend/.env` and `VITE_SUPABASE_*` in `frontend/.env` accordingly, and
re-run the migrations (see below) against the new project.

## Prerequisites

- Python 3.10+ (3.12/3.13 verified; the codebase uses `str | None` union
  syntax, so 3.9 will not work)
- Node.js 20+
- `psql` (via `libpq` -- e.g. `brew install libpq`; it's keg-only, so call it
  as `$(brew --prefix libpq)/bin/psql` or add it to `PATH`) -- only needed
  once, to apply migrations to your Supabase project
- A Supabase project (hosted, at supabase.com) with its connection details
  (URL, anon key, service role key, JWT secret, and the direct Postgres
  connection string from Project Settings -> Database)

## Quick start

1. Create the two env files from their templates:

   ```bash
   cp .env.example backend/.env
   cp frontend/.env.example frontend/.env
   ```

   Fill in `backend/.env` with your Supabase project's real values
   (`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
   `SUPABASE_JWT_SECRET`, `SUPABASE_DB_URL`) and `frontend/.env` with the
   matching `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`. Fill in
   `OPENAI_API_KEY` / `LLM_*` / `EMBEDDING_*` / `LANGSMITH_*` in
   `backend/.env` when you're ready to exercise chat/ingestion at later
   gates (G-5a/G-5b) -- they are not required for this gate's health check.

   **Note on `SUPABASE_DB_URL`:** if your database password contains
   characters like `@`, `#`, or `!`, they must be percent-encoded in the
   connection string URI (`@` -> `%40`, `#` -> `%23`) or `psql`/libpq will
   fail to parse the host correctly.

2. Apply the database schema to your Supabase project (one-time, or whenever
   migrations change):

   ```bash
   PGURL="$(grep '^SUPABASE_DB_URL=' backend/.env | cut -d= -f2- | tr -d '"')"
   for f in backend/supabase/migrations/*.sql; do
     psql "$PGURL" -v ON_ERROR_STOP=1 -f "$f"
   done
   ```

   This creates the `thread`/`message`/`document`/`chunk` tables, the pgvector
   index, RLS policies (enabled + forced on all four tables), the
   `match_chunks` RPC, and the Realtime publication.

3. Start the backend:

   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   set -a; source .env; set +a
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

4. Start the frontend (separate terminal):

   ```bash
   cd frontend
   npm install
   npm run dev
   ```

5. Health check:

   ```bash
   curl -f http://localhost:3000/api/health
   # -> {"status":"ok"}
   ```

   Port 3000 is the frontend's Vite dev server; it proxies `/api/*` to the
   FastAPI backend (port 8000), so this single command verifies the whole
   path: frontend up -> reachable -> proxying -> backend up -> responding.

   You can also hit the backend directly: `curl -f http://localhost:8000/api/health`.

### Ports

| Port  | Service                                    |
|-------|---------------------------------------------|
| 3000  | Frontend (Vite dev server)                   |
| 8000  | FastAPI backend                              |

Supabase (Postgres/Auth/Realtime/Storage) runs on Supabase's hosted
infrastructure, not on a local port.

## Running backend tests

```bash
cd backend
source venv/bin/activate
pip install -r requirements-dev.txt   # requirements.txt + pytest/httpx
pytest
```

## Project layout

```
backend/
  app/
    main.py        # FastAPI app, CORS, startup config validation, router seam
    config.py       # Pydantic BaseSettings -- the full env-var contract
    deps.py          # JWT verification, get_current_user, request-scoped Supabase client
    models.py        # Shared Pydantic models (frozen after G-4)
    routers/
      health.py      # GET /api/health (the only endpoint owned by this gate)
  supabase/
    migrations/       # Tables, indexes, RLS, match_chunks RPC, Realtime publication
  tests/
  requirements.txt
  requirements-dev.txt
frontend/
  src/
    main.tsx, App.tsx # bare boot placeholder -- real shell lands at G-6/G-7
  .env.example
.env.example            # backend env template
```

## Notes / known limitations of this gate

- Chat, ingestion, and the real frontend app shell are intentionally not
  implemented here -- see `design.md` / `plan.md` in the orchestration task
  for the gate sequence.
- This project previously ran a self-hosted Supabase stack (Postgres, Auth,
  PostgREST, Realtime, Storage, Kong) via `docker-compose.yml`, per
  `design.md`'s original assumption. It has since been switched to a hosted
  Supabase project with bare local processes -- Docker artifacts
  (`docker-compose.yml`, both `Dockerfile`s, `kong.yml`) have been removed.
  `design.md` itself has not been retroactively edited; this README and
  `gate-4.md` are the record of the change.

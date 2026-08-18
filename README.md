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

## Prerequisites

- Docker + Docker Compose (Docker Desktop on macOS/Windows, or an equivalent
  Docker Engine)
- Python 3.12+ (only needed if you also want to run the backend outside
  Docker, via a venv)
- Node.js 20+ (only needed if you also want to run the frontend outside
  Docker)

## Quick start (docker-compose -- recommended)

1. Create the two env files from their templates:

   ```bash
   cp .env.example backend/.env
   cp frontend/.env.example frontend/.env
   ```

   The committed defaults are the well-known **Supabase local-dev demo
   secrets** (the same ones the Supabase CLI and Supabase's own self-hosting
   docs publish -- not real secrets) and are wired to match this repo's
   `docker-compose.yml`, so the stack boots with zero further edits. Fill in
   `OPENAI_API_KEY` / `LLM_*` / `EMBEDDING_*` / `LANGSMITH_*` in
   `backend/.env` when you're ready to exercise chat/ingestion at later
   gates (G-5a/G-5b) -- they are not required for this gate's health check.

2. Boot the stack:

   ```bash
   docker compose up --build
   ```

   This starts: a self-hosted local Supabase (Postgres+pgvector, Auth,
   PostgREST, Realtime, Storage, fronted by a Kong gateway), the FastAPI
   backend, and the frontend dev server. The Supabase schema (tables,
   indexes, RLS policies, the `match_chunks` RPC, and the Realtime
   publication) is applied automatically on first Postgres boot from
   `backend/supabase/migrations/`.

3. Health check:

   ```bash
   curl -f http://localhost:3000/api/health
   # -> {"status":"ok"}
   ```

   Port 3000 is the frontend's Vite dev server; it proxies `/api/*` to the
   FastAPI backend (port 8000) inside the compose network, so this single
   command verifies the whole path: frontend container up -> reachable ->
   proxying -> backend container up -> responding.

   You can also hit the backend directly: `curl -f http://localhost:8000/api/health`.

### Ports

| Port  | Service                                    |
|-------|---------------------------------------------|
| 3000  | Frontend (Vite dev server)                   |
| 8000  | FastAPI backend                              |
| 54321 | Supabase API gateway (Kong)                  |
| 54322 | Postgres                                     |

## Running the backend outside Docker (venv)

Useful for fast local iteration on backend code without rebuilding the image
(the docker-compose backend service also bind-mounts `backend/app`, so a
plain `docker compose up` with `--reload` already live-reloads -- this is an
alternative for running fully outside Docker):

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes requirements.txt + pytest/httpx
cp ../.env.example .env
# Edit backend/.env: since you're running outside the compose network, set
# SUPABASE_URL=http://localhost:54321 (the host-published Kong port) --
# this is already the .env.example default.
# Start the rest of the stack (Supabase + frontend) via docker-compose first:
#   docker compose up db auth rest realtime storage kong frontend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run tests:

```bash
cd backend
pytest
```

## Running the frontend outside Docker

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
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
    kong.yml          # Local Supabase API gateway routing (docker-compose only)
  tests/
  requirements.txt
  requirements-dev.txt
  Dockerfile
frontend/
  src/
    main.tsx, App.tsx # bare boot placeholder -- real shell lands at G-6/G-7
  .env.example
  Dockerfile
docker-compose.yml
.env.example            # backend env template
```

## Notes / known limitations of this gate

- The local Supabase services in `docker-compose.yml` are assembled from
  Supabase's published self-hosting reference
  (https://supabase.com/docs/guides/self-hosting/docker). If an image tag
  has been retired upstream by the time you run this, bump it per that doc.
- Chat, ingestion, and the real frontend app shell are intentionally not
  implemented here -- see `design.md` / `plan.md` in the orchestration task
  for the gate sequence.

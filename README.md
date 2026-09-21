# KnSense

A customizable, per-client-deployable Retrieval-Augmented Generation (RAG)
knowledge assistant: a **Chat** surface (threaded, streaming,
retrieval-augmented conversations with a configurable guardrail restricting
answers to ingested content) and an **Ingestion** surface (multi-format
upload -- .txt/.md/.pdf/.docx/.html --, content-hash dedup, live processing
status, configurable metadata extraction and filtering, document
management). Stack: React + TypeScript + Vite + Tailwind (frontend), FastAPI
in a Python venv (backend), Supabase (Postgres + pgvector + Auth + Storage +
Realtime). LLM calls use the raw OpenAI-compatible SDK only -- no
LangChain/LangGraph or other orchestration framework -- so the chat and
embeddings providers are independently swappable via env vars (currently
OpenRouter for chat, Gemini for embeddings).

> **Status:** the core chat + ingestion experience, hybrid search +
> reranking, content-hash dedup/incremental re-ingest, and configurable
> metadata extraction/filtering are all implemented and passing end-to-end
> testing. Deferred until there's a committed client deployment: additional
> tool integrations, sub-agent delegation, and production-grade multi-tenant
> hardening (this app currently assumes one dedicated instance per client).

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
    main.py              # FastAPI app, CORS, global error-response normalization, router registration
    config.py            # Pydantic BaseSettings -- the full env-var contract
    deps.py              # JWT verification, get_current_user, request-scoped Supabase client
    models.py            # Shared Pydantic models
    routers/
      health.py          # GET /api/health
      threads.py         # Thread/message CRUD + POST /chat (SSE streaming)
      documents.py        # Document upload/list/delete, dedup, bulk active-state selection
      settings.py         # Chat guardrail settings (restrict-to-documents toggle + threshold)
      metadata_fields.py  # Configurable metadata field definitions (Module 4)
    services/
      chat.py            # Chat guardrail, scope classifier, thread auto-titling
      retrieval.py        # Hybrid (vector + keyword) search, RRF fusion, LLM reranking
      ingestion.py         # Multi-format extraction, chunking, embedding, metadata extraction
      llm.py               # OpenAI-compatible client wiring (chat + embeddings, independently configurable)
      storage.py           # Supabase Storage upload/delete
  supabase/
    migrations/           # Tables, indexes, RLS, match_chunks(_hybrid) RPCs, Realtime publications
  tests/
  requirements.txt
  requirements-dev.txt
frontend/
  src/
    features/chat/         # Threaded chat UI, Markdown rendering, Realtime thread list
    features/ingestion/     # Upload, document list/filtering, metadata field settings
  .env.example
.env.example                # backend env template
```

## Known limitations

- This app assumes one dedicated instance per client (its own Supabase
  project, its own env config) rather than a shared multi-tenant platform --
  the current single-user-per-instance auth model is a deliberate fit for
  that deployment shape, not a gap to fix before onboarding another client.
- Ingestion is manual file upload only -- no connectors or automated
  pipelines.
- No text-to-SQL, web-search fallback, or sub-agent delegation (PRD Modules
  7/8) -- deferred until a real client need justifies the added complexity.
- This project previously ran a self-hosted Supabase stack (Postgres, Auth,
  PostgREST, Realtime, Storage, Kong) via `docker-compose.yml`. It has since
  been switched to a hosted Supabase project with bare local processes --
  Docker artifacts (`docker-compose.yml`, both `Dockerfile`s, `kong.yml`)
  have been removed.

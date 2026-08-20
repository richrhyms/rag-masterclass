# Integration / Merge Procedure

This document is the executable, reproducible procedure for assembling the
`integration/fix-loop-1` branch from the four parallel implementation
branches produced by G-4/G-5a, G-5b, G-6, and G-7. It exists to close the QA
gate-8 HIGH finding: *"none of the branches merge cleanly to `main` or to
each other; no documented/executable merge procedure exists."*

Written by DevOps at gate-9 (2026-08-19/20). Reproduces, and formalizes as a
committed procedure, the ad-hoc merge QA performed independently while
verifying gate-8 (`qa-integration` branch, never pushed).

**Scope note:** this procedure produces a tree that assembles cleanly,
imports, and boots — it does **not** fix the three CRITICAL live-verified
application defects found at gate-8 (backend JWT/ES256 mismatch, frontend
double-`/api` prefix, chat SSE base URL). Those are carried forward
unresolved into gate-10a (backend fix) and gate-10b (frontend fix). See
"Known defects carried forward" at the bottom of this doc.

## Source branches

All branches live on `richrhyms/rag-masterclass`, pushed via the SSH remote
alias `github-richrhyms` (`git@github-richrhyms:richrhyms/rag-masterclass.git`).

| Branch | PR | Contains | SHA at time of this merge |
|---|---|---|---|
| `orchestration/2026-08-18-rag-app-react-fastapi-ab4b/backend-1-g5a-impl` | #3 | G-4 foundation (rebased) + G-5a Chat/LLM/threads | `952d3c8` |
| `orchestration/2026-08-18-rag-app-react-fastapi-ab4b/backend-2-g5b` | #2 | G-5b Ingestion write-path (documents, chunking, storage) + hosted-Supabase/Docker-removal commit | `01202c8` (tip; includes `4f01f46`) |
| `orchestration/2026-08-18-rag-app-react-fastapi-ab4b/frontend-1` | #4 | G-6 app shell + Chat UI (from-scratch Vite scaffold) | `82e7108` |
| `orchestration/2026-08-18-rag-app-react-fastapi-ab4b/frontend-2` | #5 | G-7 Ingestion UI (`features/ingestion/*` only) | `60f4c56` |

**Not merged separately:** `orchestration/2026-08-18-rag-app-react-fastapi-ab4b/backend-1`
(the bare G-4 foundation branch, PR #1). Its content (backend skeleton,
Supabase migrations, frontend placeholder) is superseded by
`backend-1-g5a-impl`, which was rebased onto it. Verified before merging: a
file-by-file content diff of every path present on `backend-1` against
`backend-1-g5a-impl` shows only 4 files differ (`backend/app/main.py`,
`backend/requirements.txt`, `.gitignore`, `README.md` — all expected
evolutionary changes from G-5a), and every other G-4 file (config, deps,
models, health router, all 6 migrations, frontend scaffold) is present
byte-identical. `git merge-base --is-ancestor` reports false only because the
G-5a rebase (see gate-5a.md) rewrote history — content containment, not
commit-graph ancestry, was the correct check here.

## Procedure

Run from a throwaway clone, never the shared local checkout (which has been
found dirty/unusable at multiple prior gates — see gate-8/qa-report.md).

```bash
# 1. Fresh clone
git clone git@github-richrhyms:richrhyms/rag-masterclass.git /tmp/rag-integration
cd /tmp/rag-integration

# 2. Cut the integration branch from main
git checkout -b integration/fix-loop-1 origin/main

# 3. Merge backend-1-g5a-impl (base; already contains G-4 foundation)
git merge --no-edit origin/orchestration/2026-08-18-rag-app-react-fastapi-ab4b/backend-1-g5a-impl
# Expected: fast-forward, no conflicts (integration branch == main at this point).

# 4. Merge backend-2-g5b
git merge --no-edit origin/orchestration/2026-08-18-rag-app-react-fastapi-ab4b/backend-2-g5b
# Expected: CONFLICT in backend/app/main.py (router-registration seam).
# Resolution: see "Conflict 1" below. All other changes (Docker-file/
# docker-compose.yml deletions, new documents/ingestion/storage modules,
# new tests) apply cleanly with no further action.
git add backend/app/main.py
git commit --no-edit

# 5. Merge frontend-1
git merge --no-edit origin/orchestration/2026-08-18-rag-app-react-fastapi-ab4b/frontend-1
# Expected: 9 add/add CONFLICTs on scaffold files (see "Conflict 2" below).
# Resolution: take frontend-1's version for every conflicting file.
for f in frontend/.gitignore frontend/index.html frontend/package-lock.json \
         frontend/package.json frontend/src/App.tsx frontend/src/main.tsx \
         frontend/tsconfig.app.json frontend/tsconfig.node.json frontend/vite.config.ts; do
  git checkout --theirs -- "$f"
  git add "$f"
done
git commit --no-edit

# 6. Merge frontend-2
git merge --no-edit origin/orchestration/2026-08-18-rag-app-react-fastapi-ab4b/frontend-2
# Expected: clean, automatic merge, 5 files, all under frontend/src/features/ingestion/*.
# No manual resolution needed. This confirms DD-4's route-registry isolation
# boundary (design.md) held in practice.

# 7. Push (SSH alias only — never `gh` CLI, see project config)
git push github-richrhyms integration/fix-loop-1:integration/fix-loop-1
```

## Conflict resolutions (reproduced from QA's gate-8 pass, re-verified here)

### Conflict 1 — `backend/app/main.py` router-registration seam (step 4)

Both `backend-1-g5a-impl` (G-5a) and `backend-2-g5b` (G-5b) independently
filled in the router-registration seam that G-4 deliberately left as a
comment (`"DevOps merges both at the deploy gate"` — design.md, Module
Boundaries). G-5a's side registers `threads.router`; G-5b's side registers
`documents.router`, with each side's comment referring to the other as not
yet present. This is **expected and anticipated by design**, not a defect.

**Resolution:** register both routers (the union of what each branch
intended):

```python
from app.routers import documents, health, threads
...
app.include_router(health.router)
app.include_router(threads.router)
app.include_router(documents.router)
```

Verified after resolution: `python -c "from app.main import app"` succeeds
and the route table includes all of `/api/health`, `/api/threads`,
`/api/threads/{id}/messages`, `/api/threads/{id}/chat`, `/api/documents`,
`/api/documents/{id}`.

**Related branch-drift note (not a conflict, auto-resolved):**
`backend-1-g5a-impl` was still built on the pre-Docker-removal state of G-4
and would reintroduce `docker-compose.yml` / `backend/Dockerfile` /
`frontend/Dockerfile` / `backend/supabase/kong.yml` if merged to `main`
alone. `backend-2-g5b` includes the Docker-removal commit (`4f01f46`,
"Switch to hosted Supabase, drop Docker"), so merging it in step 4 correctly
deletes those files with no manual intervention — confirmed by inspecting
`git status` after the merge (all four paths show as deleted, no conflict).
This branch-drift should be cleaned up (rebase `backend-1-g5a-impl` onto
`backend-2-g5b`, or vice versa) before either PR is merged to `main`
independently of this integration branch.

### Conflict 2 — Frontend scaffold add/add conflicts (step 5)

9 files conflicted as add/add: `frontend/.gitignore`, `frontend/index.html`,
`frontend/package-lock.json`, `frontend/package.json`, `frontend/src/App.tsx`,
`frontend/src/main.tsx`, `frontend/tsconfig.app.json`,
`frontend/tsconfig.node.json`, `frontend/vite.config.ts`.

**Root cause:** `frontend-1` (G-6) was built from a fresh `create-vite`
scaffold rather than extending G-4's bare frontend placeholder. Both G-4 (via
`backend-1-g5a-impl`) and G-6 independently created the same file paths from
a common ancestor that predates G-4's placeholder, so git sees them as
unrelated "added" content on each side rather than a modification of a
common base.

**Resolution:** take `frontend-1`'s version for all 9 files — it is the
complete, working implementation (app shell, routing, providers, real
`package.json`/`vite.config.ts`), while G-4's versions were empty
placeholders. `git checkout --theirs -- <file>` for each conflicting path,
then `git add`.

Note: `frontend/tsconfig.json` (the root tsconfig, referenced by the
`tsconfig.*.json` pattern) did **not** conflict — its content is
byte-identical on both branches, so git auto-merged it with no manual step.
9 files conflicted, not 10; verified via a pre-merge diff of the file before
attempting the merge.

**Not a blocking defect, but flagged as MEDIUM branch drift (per gate-8):**
this means PR #4 (`frontend-1`) cannot be applied to `main` (which will
carry G-4's placeholder) as a clean fast-forward or automatic 3-way merge —
it will always require this same manual "take theirs" resolution until
`frontend-1` is rebased to extend G-4's placeholder instead of re-scaffolding
from scratch.

### Non-conflict — `frontend-2` merge (step 6)

Merged automatically with no conflicts: 5 files, all new, all under
`frontend/src/features/ingestion/*` (`DocumentList.tsx`, `DocumentRow.tsx`,
`FileUpload.tsx`, `IngestionPage.tsx`, and a 1-line change to `routes.tsx`
un-stubbing the seam line frontend-1 left for it). This confirms DD-4's
central-route-registry isolation contract (design.md) worked exactly as
designed: frontend-2 never touches a file owned by frontend-1.

## Verification performed on the resulting tree

### Backend

```bash
cd backend
/usr/local/bin/python3.13 -m venv .venv   # system python3 is 3.9.6, cannot
source .venv/bin/activate                  # parse this codebase's `str | None`
pip install -r requirements.txt -r requirements-dev.txt
cp ../.env.example .env   # local-dev demo Supabase values, sufficient for
                          # import/boot verification (NOT live E2E — that's QA's job)
python -c "from app.main import app"   # imports cleanly, no errors
uvicorn app.main:app --host 127.0.0.1 --port 8123   # boots clean
curl http://127.0.0.1:8123/api/health   # -> {"status":"ok"}, HTTP 200
python -m pytest -q                     # -> 62 passed, 0 failed
```

Route table confirmed to include the full merged surface from both G-5a and
G-5b: `/api/health`, `/api/threads` (GET/POST), `/api/threads/{id}/messages`,
`/api/threads/{id}/chat`, `/api/documents` (GET/POST),
`/api/documents/{document_id}` (DELETE).

### Frontend

```bash
cd frontend
npm install     # clean, 0 vulnerabilities
npx tsc -b      # exit 0
npm run build   # exit 0, dist/ produced (193 KB JS / 4.1 KB CSS, gzip ~61 KB)
```

## Known defects carried forward (NOT fixed by this integration; see gate-8/qa-report.md)

This integration branch assembles, imports, boots, and builds cleanly, but
it still carries the three CRITICAL functional defects QA found live-testing
the merged stack. They are explicitly **out of scope** for this integration
gate (G-9) and are scoped to the next fix-loop gates:

1. **Backend JWT verification (`backend/app/deps.py`)** hardcodes HS256
   verification against a shared secret; the real hosted Supabase project
   issues ES256-signed tokens (asymmetric JWT signing keys), so no real
   signed-in user can pass auth on any protected endpoint. → scoped to
   **gate-10a (backend fix)**.
2. **Frontend double-`/api` prefix**: `VITE_API_BASE_URL` already ends in
   `/api`, and every REST call site (`ThreadList.tsx`, `ChatWindow.tsx`,
   `IngestionPage.tsx`, `FileUpload.tsx`) also passes a path starting with
   `/api/...`, producing `.../api/api/...` → 404 on every REST call. →
   scoped to **gate-10b (frontend fix)**.
3. **Chat SSE call has no base URL / no dev proxy**: `consumeChatStream` in
   `frontend/src/lib/sse.ts`, called from `ChatWindow.tsx`, fetches a bare
   relative path with no `VITE_API_BASE_URL` prefix and no Vite
   `server.proxy` configured, so it resolves against the frontend's own
   origin instead of the backend. → scoped to **gate-10b (frontend fix)**.

Do not attempt to fix these on this branch — the next fix-loop gates
(G-10a, G-10b) branch from `integration/fix-loop-1` specifically to address
them against this single reproducible base.

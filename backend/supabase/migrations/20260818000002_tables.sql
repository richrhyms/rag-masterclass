-- Application tables (design.md "Data Models"). auth.users is provided by
-- Supabase Auth and is NOT redefined here; every application table carries a
-- user_id FK to auth.users(id) as the RLS anchor (NFR-13).

create table if not exists public.thread (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  title       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists public.message (
  id          uuid primary key default gen_random_uuid(),
  thread_id   uuid not null references public.thread(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade, -- denormalized for RLS
  role        text not null check (role in ('user', 'assistant', 'system')),
  content     text not null,
  created_at  timestamptz not null default now()
);

create table if not exists public.document (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references auth.users(id) on delete cascade,
  filename      text not null,
  storage_path  text not null,
  content_type  text not null,
  byte_size     bigint not null,
  status        text not null default 'queued' check (status in ('queued', 'processing', 'completed', 'failed')),
  error         text,
  chunk_count   int not null default 0,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

-- embedding is a literal vector(1536) because Postgres/pgvector requires a
-- literal dimension at DDL time. This tracks the EMBEDDING_DIM env var
-- (default 1536, text-embedding-3-small). Changing the embeddings provider to
-- a different dimensionality requires a new migration that alters this column
-- (and rebuilds chunk_embedding_idx) -- see .env.example.
create table if not exists public.chunk (
  id            uuid primary key default gen_random_uuid(),
  document_id   uuid not null references public.document(id) on delete cascade, -- cascade delete -> FR-DATA-3
  user_id       uuid not null references auth.users(id) on delete cascade, -- denormalized; retrieval scope key
  chunk_index   int not null,
  content       text not null,
  embedding     vector(1536) not null, -- tracks EMBEDDING_DIM; changing dims requires a migration
  created_at    timestamptz not null default now()
);

-- Module 4 (PRD): Metadata Extraction. Configurable per-user field
-- definitions (not hardcoded per-client field names, per the "customizable
-- RAG system deployable to multiple clients" product goal) -- an operator
-- defines what metadata matters for their deployment (e.g. "category",
-- "location", "publish_date"), and ingestion extracts those specific
-- fields per document via an LLM call. Filtering surfaces through the
-- already-shipped document `active` mechanism (see 20260909000002) rather
-- than new chat-time structured-filter plumbing.

create table if not exists public.metadata_field_definition (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users(id) on delete cascade,
  name        text not null,
  description text not null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

alter table public.metadata_field_definition enable row level security;
alter table public.metadata_field_definition force row level security;

create policy owner_all on public.metadata_field_definition
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

-- Extracted values keyed by field definition name, e.g.
-- {"category": "Mini-Grid Developer", "location": "Lagos"}. Defaults to
-- '{}' so documents ingested before any field definitions existed (or
-- ingested by a user with none configured) deserialize unchanged.
alter table public.document
  add column if not exists metadata jsonb not null default '{}'::jsonb;

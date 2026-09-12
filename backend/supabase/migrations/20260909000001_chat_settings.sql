-- Chat guardrail settings (workspace-wide singleton, not per-thread/per-user
-- for now -- see docs/CHAT_GUARDRAIL.md). Controls whether chat is restricted
-- to grounded content from the user's own ingested documents, and the
-- minimum retrieval relevance score required to consider a question
-- "in scope". UI toggle lives in the Ingestion view (not an env var) so it
-- can be flipped at runtime without a restart.
--
-- Singleton-row pattern: `id boolean primary key default true` combined with
-- the `check (id)` constraint makes it structurally impossible to ever insert
-- a second row (the only legal primary key value is `true`).
create table if not exists public.chat_setting (
  id                    boolean primary key default true check (id),
  restrict_to_documents boolean not null default true,
  min_relevance_score   float not null default 0.5 check (min_relevance_score between 0 and 1),
  updated_at            timestamptz not null default now()
);

insert into public.chat_setting (id) values (true)
on conflict (id) do nothing;

alter table public.chat_setting enable row level security;
alter table public.chat_setting force row level security;

-- Readable by any authenticated user (chat needs this on every request to
-- decide whether to gate; Ingestion UI needs it to render the toggle state).
-- No INSERT/UPDATE/DELETE policy is defined here: writes go through the
-- backend's service-role client only (app/routers/settings.py), mirroring
-- the same pattern already used for document status writes
-- (services/storage.py's documented service-role deviation). This is a
-- placeholder until the permissions system (planned) restricts writes to
-- permitted users specifically -- until then, any authenticated user can
-- call the write endpoint, same as every other endpoint in this app today.
create policy chat_setting_select_authenticated
  on public.chat_setting for select
  to authenticated
  using (true);

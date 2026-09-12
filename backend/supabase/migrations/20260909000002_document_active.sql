-- Document selection: an `active` flag on `document` controls whether its
-- chunks are eligible for chat-time retrieval. Defaults to true so existing
-- behavior (every completed document is used) is unchanged until a user
-- explicitly deselects one.
alter table public.document
  add column if not exists active boolean not null default true;

-- match_chunks (DD-2) now joins `document` and excludes chunks belonging to
-- a deselected document. Recreated in full since Postgres requires DROP+CREATE
-- (not just CREATE OR REPLACE) when the join changes the function body this
-- much; the signature/return shape is unchanged from the original G-4
-- migration, so this is additive from the app code's perspective.
create or replace function public.match_chunks(
  query_embedding vector(1536),
  match_user uuid,
  match_count int
)
returns table (
  id uuid,
  document_id uuid,
  content text,
  chunk_index int,
  score float
)
language sql
stable
security invoker
as $$
  select
    c.id,
    c.document_id,
    c.content,
    c.chunk_index,
    1 - (c.embedding <=> query_embedding) as score
  from public.chunk c
  join public.document d on d.id = c.document_id
  where c.user_id = match_user
    and d.active = true
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- match_chunks RPC (design.md "DD-2 -- Retrieval interface", read side).
-- SECURITY INVOKER so RLS still applies to the caller; the explicit
-- match_user predicate is defense-in-depth on top of RLS (NFR-13, AC-BE-7b).
-- Orders by cosine distance (`<=>`) and returns cosine similarity as score.
-- Row shape matches app.models.RetrievedChunk exactly:
--   id, document_id, content, chunk_index, score

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
  where c.user_id = match_user
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

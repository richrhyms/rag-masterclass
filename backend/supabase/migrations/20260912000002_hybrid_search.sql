-- Module 6 (PRD): hybrid search. Pure cosine/vector retrieval (match_chunks,
-- 20260818000005 / 20260909000002) is weak on exact matches -- product
-- codes, names, acronyms, numbers -- because those don't embed
-- distinctively. This adds Postgres full-text (keyword) search alongside
-- the existing vector search and combines both via Reciprocal Rank Fusion
-- (RRF): score = sum(1 / (k + rank)) across whichever ranked list(s) a
-- chunk appears in. A chunk that ranks well on EITHER signal scores well
-- overall; one that ranks well on BOTH scores best.
--
-- `match_chunks` (vector-only) is left untouched rather than modified in
-- place -- it's a separate, additive RPC, not a breaking change to an
-- existing one.

-- NOTE for re-running this on a project with existing chunk rows: building
-- the GIN index below can exceed the free tier's default
-- maintenance_work_mem (32MB) once there's a meaningful amount of data --
-- observed needing ~61MB on this project. If `CREATE INDEX` fails with
-- "memory required is X MB, maintenance_work_mem is Y MB", run
-- `SET maintenance_work_mem = '128MB';` (session-scoped, no server config
-- change needed) immediately before this migration. Irrelevant for a fresh
-- deployment with an empty/small chunk table.

-- IMPORTANT: `score` in the result set is the plain cosine similarity to
-- the query, NOT the RRF fusion value -- RRF is used only to decide which
-- rows are selected and their ORDER BY/LIMIT ranking. The chat guardrail's
-- deterministic pre-gate (services/chat.py, `best_score < min_relevance_score`)
-- assumes `score` is a 0..1 cosine-similarity-like value (its threshold's
-- default is 0.5); RRF's summed 1/(k+rank) values are tiny (typically
-- < 0.04) and would make that gate fire on every message, including
-- genuinely on-topic ones. Returning cosine similarity keeps that gate's
-- existing semantics unchanged while still letting keyword-only matches
-- (poor cosine similarity, strong keyword rank) be selected into the
-- result set by the RRF ordering underneath.

-- Generated column: kept in sync automatically by Postgres on every
-- insert/update to `content`, no application code changes needed.
alter table public.chunk
  add column if not exists content_tsv tsvector
  generated always as (to_tsvector('english', content)) stored;

create index if not exists chunk_content_tsv_idx on public.chunk using gin (content_tsv);

create or replace function public.match_chunks_hybrid(
  query_embedding vector(1536),
  query_text text,
  match_user uuid,
  match_count int,
  rrf_k int default 50
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
  with candidate_pool as (
    select c.id
    from public.chunk c
    join public.document d on d.id = c.document_id
    where c.user_id = match_user
      and d.active = true
  ),
  semantic as (
    select c.id, row_number() over (order by c.embedding <=> query_embedding) as rank
    from public.chunk c
    where c.id in (select id from candidate_pool)
    order by c.embedding <=> query_embedding
    limit greatest(match_count * 4, 20)
  ),
  -- `websearch_to_tsquery` ANDs bare terms together by default -- for a
  -- multi-word natural-language question that almost never matches a
  -- single short chunk verbatim, so it silently contributes nothing (the
  -- keyword branch would find zero rows for the vast majority of real
  -- questions). OR-combining the query's own lexemes instead means any
  -- significant term match contributes, with ts_rank_cd naturally scoring
  -- chunks that match more terms higher -- the standard approach for
  -- keyword recall in a hybrid-search / BM25-style setup.
  query_terms as (
    select nullif(string_agg(lexeme, ' | '), '') as query_tsquery_text
    from unnest(tsvector_to_array(to_tsvector('english', query_text))) as lexeme
  ),
  keyword as (
    select c.id,
           row_number() over (
             order by ts_rank_cd(c.content_tsv, to_tsquery('english', query_terms.query_tsquery_text)) desc
           ) as rank
    from public.chunk c
    cross join query_terms
    where c.id in (select id from candidate_pool)
      and query_terms.query_tsquery_text is not null
      and c.content_tsv @@ to_tsquery('english', query_terms.query_tsquery_text)
    limit greatest(match_count * 4, 20)
  )
  select
    c.id,
    c.document_id,
    c.content,
    c.chunk_index,
    1 - (c.embedding <=> query_embedding) as score
  from public.chunk c
  left join semantic on semantic.id = c.id
  left join keyword on keyword.id = c.id
  where semantic.id is not null or keyword.id is not null
  order by (
    coalesce(1.0 / (rrf_k + semantic.rank), 0.0) +
    coalesce(1.0 / (rrf_k + keyword.rank), 0.0)
  ) desc
  limit match_count;
$$;

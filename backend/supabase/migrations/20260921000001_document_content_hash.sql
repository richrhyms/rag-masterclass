-- Module 3 (PRD): Record Manager. Naive ingestion re-processes and
-- re-embeds a file every time it's uploaded, even if the exact same
-- content was already ingested -- wasted embedding cost, and duplicate
-- chunks polluting retrieval. `content_hash` (SHA-256 of the raw uploaded
-- bytes, computed in the upload endpoint before storage/ingestion) lets
-- the app detect that case and reject it before any processing happens.
--
-- Nullable: existing rows predate this column and have no hash to
-- backfill (their raw bytes aren't reprocessed retroactively); dedup
-- logic in the application layer only matches against rows that DO have
-- a hash.
alter table public.document
  add column if not exists content_hash text;

create index if not exists document_user_content_hash_idx
  on public.document (user_id, content_hash);

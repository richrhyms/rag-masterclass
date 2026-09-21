-- Realtime publication (design.md "Realtime status contract"). The Ingestion
-- view subscribes to postgres_changes on public.document; RLS restricts the
-- replicated rows to the caller's own documents.
alter publication supabase_realtime add table public.document;

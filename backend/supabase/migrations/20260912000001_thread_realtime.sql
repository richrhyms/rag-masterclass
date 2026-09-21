-- Extends the Realtime publication (see 20260818000006_realtime.sql) to
-- `public.thread`, so the frontend can subscribe to live title updates
-- instead of guessing when the background auto-titling task
-- (`_maybe_set_thread_title`, services/chat.py) has finished -- that task's
-- latency is genuinely variable (observed 3-70+ seconds), so any
-- fixed-delay client-side refresh would either fire too early or add
-- needless lag. RLS (`owner_all`, 20260818000004_rls.sql) already scopes
-- replicated rows to the caller's own threads, exactly as already relied on
-- for `public.document`.
alter publication supabase_realtime add table public.thread;

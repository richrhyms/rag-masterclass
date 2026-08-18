-- Row-Level Security (design.md "Row-Level Security (DD-3, FR-DATA-4 / NFR-4)").
-- Enabled + FORCED on all four application tables; a single owner_all policy
-- keyed on auth.uid(), granted to authenticated only (no anon).

alter table public.thread enable row level security;
alter table public.thread force row level security;

alter table public.message enable row level security;
alter table public.message force row level security;

alter table public.document enable row level security;
alter table public.document force row level security;

alter table public.chunk enable row level security;
alter table public.chunk force row level security;

create policy owner_all on public.thread
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy owner_all on public.message
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy owner_all on public.document
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

create policy owner_all on public.chunk
  for all
  to authenticated
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

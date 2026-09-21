-- Indexes (design.md "Data Models" / "Indexes / extensions (DD-3)").

-- In-order history reads (app-managed memory, FR-BE-5b).
create index if not exists message_thread_id_created_at_idx
  on public.message (thread_id, created_at);

-- pgvector ANN index on chunk embedding (cosine). IVFFlat chosen over HNSW for
-- broad pgvector version compatibility in the local Supabase image;
-- lists=100 is a sane small-corpus default. Retrieval MUST use cosine
-- distance operator `<=>` to match this index's operator class.
create index if not exists chunk_embedding_idx
  on public.chunk
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

-- Pre-filter by owner before ANN.
create index if not exists chunk_user_id_idx
  on public.chunk (user_id);

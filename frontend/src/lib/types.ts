export type DocStatus = 'queued' | 'processing' | 'completed' | 'failed'

export interface Document {
  id: string
  filename: string
  status: DocStatus
  chunk_count: number
  byte_size: number
  error: string | null
  created_at: string
  updated_at: string
}

export interface Thread {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string
}

export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string
}

export type ChatSSEEvent =
  | { type: 'start'; thread_id: string; user_message_id: string }
  | { type: 'token'; delta: string }
  | { type: 'done'; assistant_message_id: string; content: string }
  | { type: 'error'; error: string; code: string }

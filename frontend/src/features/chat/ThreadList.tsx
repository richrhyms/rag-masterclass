import { useEffect, useState, type MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiRequest } from '@/lib/apiClient'
import { supabase } from '@/lib/supabaseClient'
import type { Thread } from '@/lib/types'
import { MessageSquare, Plus, Loader2, Trash2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ThreadListProps {
  activeThreadId?: string
  onSelectThread: (thread: Thread) => void
}

function _sortByUpdatedAtDesc(threads: Thread[]): Thread[] {
  return [...threads].sort((a, b) => b.updated_at.localeCompare(a.updated_at))
}

export function ThreadList({ activeThreadId, onSelectThread }: ThreadListProps) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    fetchThreads()

    // Live-updates a thread's title the moment the backend's background
    // auto-titling task (services/chat.py::_maybe_set_thread_title)
    // actually finishes -- that task's latency genuinely varies (observed
    // 3-70+ seconds), so a fixed-delay client-side refresh would either
    // fire too early or add needless lag. Subscribing to real change events
    // is the correct fix, not a timing workaround -- same pattern already
    // used for live document status in features/ingestion/IngestionPage.tsx.
    const channel = supabase
      .channel('thread-changes')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'thread' },
        (payload) => {
          if (payload.eventType === 'INSERT') {
            setThreads((prev) =>
              prev.some((t) => t.id === (payload.new as Thread).id)
                ? prev
                : _sortByUpdatedAtDesc([payload.new as Thread, ...prev])
            )
          } else if (payload.eventType === 'UPDATE') {
            setThreads((prev) =>
              _sortByUpdatedAtDesc(
                prev.map((t) => (t.id === (payload.new as Thread).id ? (payload.new as Thread) : t))
              )
            )
          } else if (payload.eventType === 'DELETE') {
            setThreads((prev) => prev.filter((t) => t.id !== (payload.old as Thread).id))
          }
        }
      )
      .subscribe()

    return () => {
      supabase.removeChannel(channel)
    }
  }, [])

  async function fetchThreads() {
    try {
      const data = await apiRequest<{ threads: Thread[] }>('/threads')
      setThreads(data.threads)
    } catch (e) {
      console.error('Failed to fetch threads', e)
    } finally {
      setLoading(false)
    }
  }

  async function createThread() {
    setCreating(true)
    try {
      const newThread = await apiRequest<Thread>('/threads', {
        method: 'POST',
        body: JSON.stringify({ title: null }),
      })
      // Realtime's INSERT handler above will also deliver this row, but
      // de-dupes by id, so updating local state here too (for snappiness)
      // is safe.
      setThreads((prev) => [newThread, ...prev])
      onSelectThread(newThread)
    } catch (e) {
      console.error('Failed to create thread', e)
    } finally {
      setCreating(false)
    }
  }

  async function handleDeleteThread(e: MouseEvent<HTMLButtonElement>, thread: Thread) {
    e.stopPropagation() // don't also trigger onSelectThread on the parent button
    if (!confirm(`Delete "${thread.title || 'New Conversation'}"? This cannot be undone.`)) {
      return
    }
    setDeletingId(thread.id)
    try {
      await apiRequest<void>(`/threads/${thread.id}`, { method: 'DELETE' })
      // Realtime's DELETE handler above will also deliver this removal, but
      // updating local state here too (for snappiness) is safe -- the
      // handler de-dupes by id.
      setThreads((prev) => prev.filter((t) => t.id !== thread.id))
      if (activeThreadId === thread.id) {
        navigate('/chat')
      }
    } catch (e: any) {
      console.error('Failed to delete thread', e)
      alert(`Failed to delete conversation: ${e.error || 'Unknown error'}`)
    } finally {
      setDeletingId(null)
    }
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center p-4">
        <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div className="p-4">
        <button
          onClick={createThread}
          disabled={creating}
          className="w-full flex items-center justify-center gap-2 p-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors font-medium disabled:opacity-50"
        >
          {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          New Chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-4 space-y-1">
        <div className="px-2 py-2 text-xs font-semibold text-zinc-500 uppercase tracking-wider">
          Recent Conversations
        </div>
        {threads.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-zinc-400">
            No chats yet
          </div>
        ) : (
          threads.map((thread) => (
            <div
              key={thread.id}
              className={cn(
                "w-full flex items-center gap-1 rounded-lg text-left transition-all group",
                activeThreadId === thread.id
                  ? "bg-indigo-50 text-indigo-700"
                  : "hover:bg-zinc-100 text-zinc-600 hover:text-zinc-900"
              )}
            >
              <button
                onClick={() => onSelectThread(thread)}
                className="flex-1 min-w-0 flex items-center gap-3 p-3"
              >
                <MessageSquare className={cn(
                  "w-4 h-4 shrink-0",
                  activeThreadId === thread.id ? "text-indigo-500" : "text-zinc-400 group-hover:text-zinc-500"
                )} />
                <span className="truncate text-sm font-medium">
                  {thread.title || 'New Conversation'}
                </span>
              </button>
              <button
                onClick={(e) => handleDeleteThread(e, thread)}
                disabled={deletingId === thread.id}
                title="Delete conversation"
                className="shrink-0 p-2 mr-1 text-zinc-400 opacity-0 group-hover:opacity-100 hover:text-red-500 transition-colors disabled:opacity-50"
              >
                {deletingId === thread.id ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Trash2 className="w-4 h-4" />
                )}
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

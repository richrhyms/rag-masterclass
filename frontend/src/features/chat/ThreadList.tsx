import { useEffect, useState } from 'react'
import { apiRequest } from '@/lib/apiClient'
import type { Thread } from '@/lib/types'
import { MessageSquare, Plus, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ThreadListProps {
  activeThreadId?: string
  onSelectThread: (thread: Thread) => void
}

export function ThreadList({ activeThreadId, onSelectThread }: ThreadListProps) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    fetchThreads()
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
      setThreads([newThread, ...threads])
      onSelectThread(newThread)
    } catch (e) {
      console.error('Failed to create thread', e)
    } finally {
      setCreating(false)
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
            <button
              key={thread.id}
              onClick={() => onSelectThread(thread)}
              className={cn(
                "w-full flex items-center gap-3 p-3 rounded-lg text-left transition-all group",
                activeThreadId === thread.id
                  ? "bg-indigo-50 text-indigo-700"
                  : "hover:bg-zinc-100 text-zinc-600 hover:text-zinc-900"
              )}
            >
              <MessageSquare className={cn(
                "w-4 h-4 shrink-0",
                activeThreadId === thread.id ? "text-indigo-500" : "text-zinc-400 group-hover:text-zinc-500"
              )} />
              <span className="truncate text-sm font-medium">
                {thread.title || 'New Conversation'}
              </span>
            </button>
          ))
        )}
      </div>
    </div>
  )
}

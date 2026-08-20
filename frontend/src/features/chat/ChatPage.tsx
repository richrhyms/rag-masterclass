import { useParams, useNavigate } from 'react-router-dom'
import { ThreadList } from './ThreadList'
import { ChatWindow } from './ChatWindow'
import type { Thread } from '@/lib/types'

export function ChatPage() {
  const { threadId } = useParams()
  const navigate = useNavigate()

  const handleSelectThread = (thread: Thread) => {
    navigate(`/chat/${thread.id}`)
  }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="w-80 border-r bg-white flex flex-col">
        <ThreadList
          activeThreadId={threadId}
          onSelectThread={handleSelectThread}
        />
      </div>
      <div className="flex-1 bg-zinc-50">
        {threadId ? (
          <ChatWindow threadId={threadId} />
        ) : (
          <div className="h-full flex items-center justify-center text-zinc-500 p-8 text-center">
            <div>
              <div className="mb-4 flex justify-center">
                <div className="p-3 bg-zinc-100 rounded-full">
                  <svg className="w-8 h-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z" />
                  </svg>
                </div>
              </div>
              <h3 className="text-lg font-medium text-zinc-900">No thread selected</h3>
              <p className="text-sm">Select a conversation from the list or start a new one to begin chatting.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

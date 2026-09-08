import { useEffect, useState, useRef } from 'react'
import { apiRequest, apiUrl } from '@/lib/apiClient'
import { consumeChatStream } from '@/lib/sse'
import type { Message, ChatSSEEvent } from '@/lib/types'
import { MessageInput } from './MessageInput'
import { Loader2, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ChatWindowProps {
  threadId: string
}

export function ChatWindow({ threadId }: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(true)
  const [isStreaming, setIsStreaming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetchMessages()
  }, [threadId])

  useEffect(() => {
    scrollToBottom()
  }, [messages])

  function scrollToBottom() {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }

  async function fetchMessages() {
    setLoading(true)
    setError(null)
    try {
      const data = await apiRequest<{ messages: Message[] }>(`/threads/${threadId}/messages`)
      setMessages(data.messages)
    } catch (e: any) {
      setError(e.error || 'Failed to load messages')
    } finally {
      setLoading(false)
    }
  }

  async function handleSendMessage(content: string) {
    setError(null)
    setIsStreaming(true)

    // Optimistically add user message
    const userMsg: Message = {
      id: 'temp-id',
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, userMsg])

    try {
      await consumeChatStream(
        apiUrl(`/threads/${threadId}/chat`),
        content,
        (event: ChatSSEEvent) => {
          if (event.type === 'token') {
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last && last.role === 'assistant') {
                return [
                  ...prev.slice(0, -1),
                  { ...last, content: last.content + event.delta }
                ]
              }
              return [...prev, {
                id: 'streaming-id',
                role: 'assistant',
                content: event.delta,
                created_at: new Date().toISOString(),
              }]
            })
          } else if (event.type === 'done') {
            setMessages(prev => {
              const last = prev[prev.length - 1]
              if (last && last.role === 'assistant') {
                return [
                  ...prev.slice(0, -1),
                  {
                    id: event.assistant_message_id,
                    role: 'assistant',
                    content: event.content,
                    created_at: new Date().toISOString(),
                  }
                ]
              }
              return prev
            })
          }
        },
        (err: { error: string }) => {
          setError(err.error)
        }
      )
    } catch (e: any) {
      setError(e.message || 'An error occurred while sending message')
    } finally {
      setIsStreaming(false)
    }
  }

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-zinc-400" />
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-6"
      >
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center text-zinc-400 text-center px-4">
            No messages yet. Start a conversation!
          </div>
        ) : (
          messages.map((msg, i) => (
            <div
              key={msg.id + i}
              className={cn(
                "flex w-full",
                msg.role === 'user' ? "justify-end" : "justify-start"
              )}
            >
              <div className={cn(
                "max-w-[80%] p-3 rounded-2xl text-sm",
                msg.role === 'user'
                  ? "bg-indigo-600 text-white rounded-tr-none"
                  : "bg-white border text-zinc-800 rounded-tl-none shadow-sm"
              )}>
                {msg.content}
              </div>
            </div>
          ))
        )}

        {error && (
          <div className="flex justify-center">
            <div className="bg-red-50 text-red-600 p-3 rounded-lg text-xs flex items-center gap-2 border border-red-200 max-w-md">
              <AlertCircle className="w-4 h-4" />
              {error}
            </div>
          </div>
        )}
      </div>

      <div className="p-4 bg-white border-t">
        <MessageInput
          disabled={isStreaming}
          onSend={handleSendMessage}
        />
      </div>
    </div>
  )
}

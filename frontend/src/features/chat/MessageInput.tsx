import React, { useState } from 'react'
import { Send, Loader2 } from 'lucide-react'

interface MessageInputProps {
  onSend: (content: string) => void
  disabled: boolean
}

export function MessageInput({ onSend, disabled }: MessageInputProps) {
  const [text, setText] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim() || disabled) return
    onSend(text)
    setText('')
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-4xl mx-auto relative flex gap-2">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Type your message..."
        className="flex-1 p-3 pr-12 border rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none transition-all text-sm"
        disabled={disabled}
      />
      <button
        type="submit"
        disabled={disabled || !text.trim()}
        className="absolute right-2 top-1/2 -translate-y-1/2 p-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 disabled:bg-zinc-400"
      >
        {disabled ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
      </button>
    </form>
  )
}

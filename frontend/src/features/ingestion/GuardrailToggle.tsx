import React, { useEffect, useState } from 'react'
import { apiRequest } from '@/lib/apiClient'
import type { ChatSettings } from '@/lib/types'

// Workspace-wide chat guardrail toggle (`chat_setting` singleton,
// GET/PATCH /api/settings/chat). Visible to any signed-in user for now --
// this is the documented placeholder until the planned permissions system
// restricts write access to it, same as the backend endpoint itself.
export const GuardrailToggle: React.FC = () => {
  const [settings, setSettings] = useState<ChatSettings | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiRequest<ChatSettings>('/settings/chat')
      .then((data) => {
        if (!cancelled) setSettings(data)
      })
      .catch((err: any) => {
        if (!cancelled) setError(err.error || 'Failed to load chat settings')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleToggle = async () => {
    if (!settings || isSaving) return
    const next = { ...settings, restrict_to_documents: !settings.restrict_to_documents }
    setSettings(next) // optimistic
    setIsSaving(true)
    setError(null)
    try {
      const saved = await apiRequest<ChatSettings>('/settings/chat', {
        method: 'PATCH',
        body: JSON.stringify({ restrict_to_documents: next.restrict_to_documents }),
      })
      setSettings(saved)
    } catch (err: any) {
      setSettings(settings) // revert on failure
      setError(err.error || 'Failed to update chat settings')
    } finally {
      setIsSaving(false)
    }
  }

  if (error) {
    return <div className="text-sm text-red-500">{error}</div>
  }

  const isOn = settings?.restrict_to_documents ?? true

  return (
    <div className="flex items-start justify-between gap-4 rounded-lg border border-zinc-200 bg-white p-4">
      <div>
        <p className="font-medium text-zinc-900">Restrict chat to available context</p>
        <p className="text-sm text-zinc-500 mt-1">
          When on, the assistant declines questions unrelated to your ingested documents instead
          of answering from general knowledge.
        </p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={isOn}
        aria-label="Restrict chat to available context"
        disabled={!settings || isSaving}
        onClick={handleToggle}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
          isOn ? 'bg-indigo-600' : 'bg-zinc-300'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
            isOn ? 'translate-x-6' : 'translate-x-1'
          }`}
        />
      </button>
    </div>
  )
}

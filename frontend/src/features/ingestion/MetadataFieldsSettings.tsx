import React, { useEffect, useState } from 'react'
import { apiRequest } from '@/lib/apiClient'
import type { MetadataFieldDefinition } from '@/lib/types'
import { Plus, Trash2, Loader2 } from 'lucide-react'

interface MetadataFieldsSettingsProps {
  onFieldsChanged?: () => void
}

// Module 4 (PRD): lets an operator configure whatever metadata fields
// matter for THEIR deployment (e.g. "category", "region") rather than the
// app hardcoding per-client field names -- see
// services/ingestion.py's _default_extract_metadata for how these drive
// the actual LLM extraction at upload time.
export const MetadataFieldsSettings: React.FC<MetadataFieldsSettingsProps> = ({ onFieldsChanged }) => {
  const [fields, setFields] = useState<MetadataFieldDefinition[]>([])
  const [loading, setLoading] = useState(true)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [saving, setSaving] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchFields()
  }, [])

  async function fetchFields() {
    setLoading(true)
    try {
      const data = await apiRequest<MetadataFieldDefinition[]>('/metadata-fields')
      setFields(data)
    } catch (err: any) {
      setError(err.error || 'Failed to load metadata fields')
    } finally {
      setLoading(false)
    }
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim() || !description.trim() || saving) return
    setSaving(true)
    setError(null)
    try {
      const created = await apiRequest<MetadataFieldDefinition>('/metadata-fields', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), description: description.trim() }),
      })
      setFields((prev) => [...prev, created])
      setName('')
      setDescription('')
      onFieldsChanged?.()
    } catch (err: any) {
      setError(err.error || 'Failed to add metadata field')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    setDeletingId(id)
    setError(null)
    try {
      await apiRequest<void>(`/metadata-fields/${id}`, { method: 'DELETE' })
      setFields((prev) => prev.filter((f) => f.id !== id))
      onFieldsChanged?.()
    } catch (err: any) {
      setError(err.error || 'Failed to delete metadata field')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-4 space-y-4">
      <div>
        <p className="font-medium text-zinc-900">Metadata fields</p>
        <p className="text-sm text-zinc-500 mt-1">
          Define fields to extract from each document as it's ingested (e.g. "category", "region").
          Newly uploaded documents will be tagged automatically; existing documents are not
          retroactively re-processed.
        </p>
      </div>

      {loading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="w-5 h-5 animate-spin text-zinc-400" />
        </div>
      ) : (
        <>
          {fields.length > 0 && (
            <ul className="space-y-2">
              {fields.map((field) => (
                <li
                  key={field.id}
                  className="flex items-start justify-between gap-3 rounded-md bg-zinc-50 p-3"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-zinc-900">{field.name}</p>
                    <p className="text-xs text-zinc-500">{field.description}</p>
                  </div>
                  <button
                    onClick={() => handleDelete(field.id)}
                    disabled={deletingId === field.id}
                    title="Remove field"
                    className="shrink-0 p-1 text-zinc-400 hover:text-red-500 transition-colors disabled:opacity-50"
                  >
                    {deletingId === field.id ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Trash2 className="w-4 h-4" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={handleAdd} className="flex flex-col sm:flex-row gap-2">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Field name (e.g. category)"
              className="flex-1 min-w-0 rounded-md border border-zinc-300 px-3 py-2 text-sm"
            />
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What should the LLM extract? (e.g. The business category, e.g. Mini-Grid Developer)"
              className="flex-[2] min-w-0 rounded-md border border-zinc-300 px-3 py-2 text-sm"
            />
            <button
              type="submit"
              disabled={saving || !name.trim() || !description.trim()}
              className="flex items-center justify-center gap-1 px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-md text-sm font-medium disabled:opacity-50 shrink-0"
            >
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              Add
            </button>
          </form>
        </>
      )}

      {error && <p className="text-sm text-red-500">{error}</p>}
    </div>
  )
}

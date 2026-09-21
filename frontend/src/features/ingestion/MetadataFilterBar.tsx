import React, { useMemo, useState } from 'react'
import type { Document, MetadataFieldDefinition } from '@/lib/types'
import { Filter, X, Loader2 } from 'lucide-react'

interface MetadataFilterBarProps {
  documents: Document[]
  fieldDefinitions: MetadataFieldDefinition[]
  // Applies `active` to the subset of documents matching field=value, and
  // the opposite to everyone else -- delegates to the caller (IngestionPage)
  // since it already owns bulk-PATCH + optimistic-update logic.
  onApplyFilter: (matchingIds: string[], nonMatchingIds: string[]) => Promise<void>
  // Restores document active-state to what it was before the current
  // filter session started (see IngestionPage's preFilterSnapshot) --
  // NOT "force every document active", which would silently discard any
  // manual selections made before the filter bar was ever touched.
  onClear: () => Promise<void>
}

// Module 4 (PRD): "filter retrieval by metadata". Rather than a new
// chat-time structured-filter parameter, this reuses the already-shipped
// document `active` mechanism -- selecting a metadata value here bulk-sets
// `active` so chat retrieval (which already respects `active`) is scoped
// to matching documents. A two-step workflow (filter, then chat), not
// live inline filtering during a conversation.
export const MetadataFilterBar: React.FC<MetadataFilterBarProps> = ({
  documents,
  fieldDefinitions,
  onApplyFilter,
  onClear,
}) => {
  const [fieldName, setFieldName] = useState('')
  const [value, setValue] = useState('')
  const [applying, setApplying] = useState(false)

  const availableValues = useMemo(() => {
    if (!fieldName) return []
    const values = new Set<string>()
    for (const doc of documents) {
      const v = doc.metadata?.[fieldName]
      if (v != null) values.add(v)
    }
    return Array.from(values).sort()
  }, [documents, fieldName])

  if (fieldDefinitions.length === 0) {
    return null
  }

  const handleApply = async () => {
    if (!fieldName || !value || applying) return
    setApplying(true)
    try {
      const matching = documents.filter((doc) => doc.metadata?.[fieldName] === value).map((d) => d.id)
      const nonMatching = documents.filter((doc) => doc.metadata?.[fieldName] !== value).map((d) => d.id)
      await onApplyFilter(matching, nonMatching)
    } finally {
      setApplying(false)
    }
  }

  const handleClear = async () => {
    if (applying) return
    setApplying(true)
    try {
      await onClear()
    } finally {
      setApplying(false)
      setFieldName('')
      setValue('')
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 bg-white p-3">
      <Filter className="w-4 h-4 text-zinc-400 shrink-0" />
      <span className="text-sm text-zinc-500 shrink-0">Filter by</span>
      <select
        value={fieldName}
        onChange={(e) => {
          setFieldName(e.target.value)
          setValue('')
        }}
        className="rounded-md border border-zinc-300 px-2 py-1 text-sm"
      >
        <option value="">Select field...</option>
        {fieldDefinitions.map((field) => (
          <option key={field.id} value={field.name}>
            {field.name}
          </option>
        ))}
      </select>
      {fieldName && (
        <select
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="rounded-md border border-zinc-300 px-2 py-1 text-sm"
        >
          <option value="">Select value...</option>
          {availableValues.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      )}
      <button
        onClick={handleApply}
        disabled={!fieldName || !value || applying}
        className="flex items-center gap-1 px-3 py-1 bg-indigo-600 hover:bg-indigo-700 text-white rounded-md text-sm font-medium disabled:opacity-50"
      >
        {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
        Apply
      </button>
      <button
        onClick={handleClear}
        disabled={applying}
        title="Clear filter: restore document selection to how it was before this filter"
        className="flex items-center gap-1 px-2 py-1 text-zinc-500 hover:text-zinc-700 text-sm disabled:opacity-50"
      >
        <X className="w-3.5 h-3.5" />
        Clear
      </button>
    </div>
  )
}

import React, { useEffect, useRef } from 'react'
import type { Document } from '@/lib/types'
import { DocumentRow } from './DocumentRow'

interface DocumentListProps {
  documents: Document[]
  onDelete: (id: string) => void
  onToggleActive: (id: string, active: boolean) => void
  onToggleAll: (active: boolean) => void
}

const SelectAllCheckbox: React.FC<{ documents: Document[]; onToggleAll: (active: boolean) => void }> = ({
  documents,
  onToggleAll,
}) => {
  const ref = useRef<HTMLInputElement>(null)
  const allActive = documents.every((doc) => doc.active)
  const noneActive = documents.every((doc) => !doc.active)

  useEffect(() => {
    if (ref.current) {
      ref.current.indeterminate = !allActive && !noneActive
    }
  }, [allActive, noneActive])

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={allActive}
      // Clicking while mixed (indeterminate) selects all, matching the
      // conventional "select all" header-checkbox behavior.
      onChange={() => onToggleAll(!allActive)}
      title={allActive ? 'Deselect all' : 'Select all'}
      className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
    />
  )
}

export const DocumentList: React.FC<DocumentListProps> = ({
  documents,
  onDelete,
  onToggleActive,
  onToggleAll,
}) => {
  if (documents.length === 0) {
    return (
      <div className="text-center py-12 border rounded-lg bg-gray-50 text-gray-500">
        No documents ingested yet.
      </div>
    )
  }

  return (
    <div className="overflow-hidden border rounded-lg">
      <table className="w-full text-left text-sm">
        <thead className="bg-gray-50 border-b">
          <tr>
            <th className="px-4 py-3 font-medium text-gray-500" title="Used in chat retrieval">
              <SelectAllCheckbox documents={documents} onToggleAll={onToggleAll} />
            </th>
            <th className="px-4 py-3 font-medium text-gray-500">Filename</th>
            <th className="px-4 py-3 font-medium text-gray-500">Status</th>
            <th className="px-4 py-3 font-medium text-gray-500">Chunks</th>
            <th className="px-4 py-3 font-medium text-gray-500">Size</th>
            <th className="px-4 py-3 font-medium text-gray-500 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {documents.map((doc) => (
            <DocumentRow key={doc.id} doc={doc} onDelete={onDelete} onToggleActive={onToggleActive} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

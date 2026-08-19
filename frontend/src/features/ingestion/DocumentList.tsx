import React from 'react'
import { Document, DocStatus } from '@/lib/types'
import { DocumentRow } from './DocumentRow'

interface DocumentListProps {
  documents: Document[]
  onDelete: (id: string) => void
}

export const DocumentList: React.FC<DocumentListProps> = ({ documents, onDelete }) => {
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
            <th className="px-4 py-3 font-medium text-gray-500">Filename</th>
            <th className="px-4 py-3 font-medium text-gray-500">Status</th>
            <th className="px-4 py-3 font-medium text-gray-500">Chunks</th>
            <th className="px-4 py-3 font-medium text-gray-500">Size</th>
            <th className="px-4 py-3 font-medium text-gray-500 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {documents.map((doc) => (
            <DocumentRow key={doc.id} doc={doc} onDelete={onDelete} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

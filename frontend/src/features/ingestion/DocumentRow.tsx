import React from 'react'
import type { Document } from '@/lib/types'
import { Trash2 } from 'lucide-react'

interface DocumentRowProps {
  doc: Document
  onDelete: (id: string) => void
}

export const DocumentRow: React.FC<DocumentRowProps> = ({ doc, onDelete }) => {
  const statusColors = {
    queued: 'bg-gray-100 text-gray-600',
    processing: 'bg-blue-100 text-blue-600',
    completed: 'bg-green-100 text-green-600',
    failed: 'bg-red-100 text-red-600',
  }

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  return (
    <tr className="hover:bg-gray-50 transition-colors">
      <td className="px-4 py-3 font-medium">{doc.filename}</td>
      <td className="px-4 py-3">
        <span className={`px-2 py-1 rounded-full text-xs font-medium ${statusColors[doc.status]}`}>
          {doc.status}
        </span>
        {doc.status === 'failed' && doc.error && (
          <span className="ml-2 text-xs text-red-500 italic" title={doc.error}>
            Error
          </span>
        )}
      </td>
      <td className="px-4 py-3">{doc.chunk_count}</td>
      <td className="px-4 py-3 text-gray-500">{formatSize(doc.byte_size)}</td>
      <td className="px-4 py-3 text-right">
        <button
          onClick={() => onDelete(doc.id)}
          className="p-2 text-gray-400 hover:text-red-500 transition-colors"
          title="Delete document"
        >
          <Trash2 size={16} />
        </button>
      </td>
    </tr>
  )
}

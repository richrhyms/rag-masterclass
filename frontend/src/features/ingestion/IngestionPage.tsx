import React, { useEffect, useState } from 'react'
import { apiRequest } from '@/lib/apiClient'
import { supabase } from '@/lib/supabaseClient'
import type { Document } from '@/lib/types'
import { FileUpload } from './FileUpload'
import { DocumentList } from './DocumentList'

export const IngestionPage: React.FC = () => {
  const [documents, setDocuments] = useState<Document[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchDocuments = async () => {
    try {
      setIsLoading(true)
      const data = await apiRequest<{ documents: Document[] }>('/documents')
      setDocuments(data.documents)
    } catch (err: any) {
      setError(err.error || 'Failed to load documents')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchDocuments()

    const channel = supabase
      .channel('document-changes')
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'document' },
        (payload) => {
          if (payload.eventType === 'INSERT') {
            setDocuments((prev) => [payload.new as Document, ...prev])
          } else if (payload.eventType === 'UPDATE') {
            setDocuments((prev) =>
              prev.map((doc) => (doc.id === (payload.new as Document).id ? (payload.new as Document) : doc))
            )
          } else if (payload.eventType === 'DELETE') {
            setDocuments((prev) => prev.filter((doc) => doc.id !== (payload.old as Document).id))
          }
        }
      )
      .subscribe()

    return () => {
      supabase.removeChannel(channel)
    }
  }, [])

  const handleUploadSuccess = () => {
    fetchDocuments()
  }

  const handleDeleteDocument = async (id: string) => {
    try {
      await apiRequest<void>(`/documents/${id}`, { method: 'DELETE' })
      // Realtime should handle the update, but we can also update local state for snappiness
      setDocuments((prev) => prev.filter((doc) => doc.id !== id))
    } catch (err: any) {
      alert(`Failed to delete document: ${err.error || 'Unknown error'}`)
    }
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-8">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Knowledge Ingestion</h1>
        <p className="text-muted-foreground">
          Upload your documents to ground the assistant in your data. Supported formats: .txt, .md
        </p>
      </div>

      <FileUpload onUploadSuccess={handleUploadSuccess} />

      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Documents</h2>
        {isLoading ? (
          <div className="text-center py-8">Loading documents...</div>
        ) : error ? (
          <div className="text-red-500 py-8 text-center">{error}</div>
        ) : (
          <DocumentList documents={documents} onDelete={handleDeleteDocument} />
        )}
      </div>
    </div>
  )
}

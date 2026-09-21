import React, { useEffect, useState } from 'react'
import { apiRequest } from '@/lib/apiClient'
import { supabase } from '@/lib/supabaseClient'
import type { Document, MetadataFieldDefinition } from '@/lib/types'
import { FileUpload } from './FileUpload'
import { DocumentList } from './DocumentList'
import { GuardrailToggle } from './GuardrailToggle'
import { MetadataFieldsSettings } from './MetadataFieldsSettings'
import { MetadataFilterBar } from './MetadataFilterBar'

export const IngestionPage: React.FC = () => {
  const [documents, setDocuments] = useState<Document[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [fieldDefinitions, setFieldDefinitions] = useState<MetadataFieldDefinition[]>([])
  // Snapshot of each document's `active` value from just before the first
  // filter application in the current "filter session" -- lets "Clear"
  // restore exactly that prior state instead of force-activating every
  // document (which would also wipe out any unrelated manual selections
  // the user made before ever touching the filter bar). Reset to null
  // once restored. Known limitation: a manual per-document toggle made
  // *between* Apply and Clear is not itself preserved -- Clear restores
  // the snapshot from before the filter session started, not a
  // continuously-updated one.
  const [preFilterSnapshot, setPreFilterSnapshot] = useState<Record<string, boolean> | null>(null)

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

  const fetchFieldDefinitions = async () => {
    try {
      const data = await apiRequest<MetadataFieldDefinition[]>('/metadata-fields')
      setFieldDefinitions(data)
    } catch {
      // Non-critical: the filter bar simply stays hidden if this fails to
      // load; MetadataFieldsSettings surfaces its own load error already.
    }
  }

  useEffect(() => {
    fetchDocuments()
    fetchFieldDefinitions()

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

  const handleToggleActive = async (id: string, active: boolean) => {
    // optimistic update; Realtime will also deliver the same row change
    setDocuments((prev) => prev.map((doc) => (doc.id === id ? { ...doc, active } : doc)))
    try {
      await apiRequest<Document>(`/documents/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ active }),
      })
    } catch (err: any) {
      setDocuments((prev) => prev.map((doc) => (doc.id === id ? { ...doc, active: !active } : doc)))
      alert(`Failed to update document: ${err.error || 'Unknown error'}`)
    }
  }

  // Bulk-sets `active` for a specific set of documents in one request
  // (PATCH /api/documents with document_ids), instead of one PATCH per
  // document -- used by both apply and clear below. A no-op for an empty
  // id list (skips the request entirely).
  const bulkSetActive = async (documentIds: string[], active: boolean) => {
    if (documentIds.length === 0) return
    await apiRequest<{ documents: Document[] }>('/documents', {
      method: 'PATCH',
      body: JSON.stringify({ active, document_ids: documentIds }),
    })
  }

  const handleApplyMetadataFilter = async (matchingIds: string[], nonMatchingIds: string[]) => {
    const previous = documents
    if (!preFilterSnapshot) {
      const snapshot: Record<string, boolean> = {}
      for (const doc of documents) snapshot[doc.id] = doc.active
      setPreFilterSnapshot(snapshot)
    }
    // optimistic update
    setDocuments((prev) =>
      prev.map((doc) => ({
        ...doc,
        active: matchingIds.includes(doc.id) ? true : nonMatchingIds.includes(doc.id) ? false : doc.active,
      }))
    )
    try {
      await Promise.all([bulkSetActive(matchingIds, true), bulkSetActive(nonMatchingIds, false)])
    } catch (err: any) {
      setDocuments(previous)
      alert(`Failed to apply filter: ${err.error || 'Unknown error'}`)
    }
  }

  const handleClearMetadataFilter = async () => {
    // Nothing was ever filtered this session -- clearing must not force
    // every document active, which would silently wipe out any manual
    // selections made before the filter bar was ever touched.
    if (!preFilterSnapshot) return

    const previous = documents
    const snapshot = preFilterSnapshot
    const restoreActive = Object.entries(snapshot)
      .filter(([, active]) => active)
      .map(([id]) => id)
    const restoreInactive = Object.entries(snapshot)
      .filter(([, active]) => !active)
      .map(([id]) => id)

    setDocuments((prev) => prev.map((doc) => (doc.id in snapshot ? { ...doc, active: snapshot[doc.id] } : doc)))
    try {
      await Promise.all([bulkSetActive(restoreActive, true), bulkSetActive(restoreInactive, false)])
      setPreFilterSnapshot(null)
    } catch (err: any) {
      setDocuments(previous)
      alert(`Failed to clear filter: ${err.error || 'Unknown error'}`)
    }
  }

  const handleToggleAll = async (active: boolean) => {
    const previous = documents
    setDocuments((prev) => prev.map((doc) => ({ ...doc, active })))
    try {
      const data = await apiRequest<{ documents: Document[] }>('/documents', {
        method: 'PATCH',
        body: JSON.stringify({ active }),
      })
      setDocuments(data.documents)
    } catch (err: any) {
      setDocuments(previous)
      alert(`Failed to update documents: ${err.error || 'Unknown error'}`)
    }
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-8">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">Knowledge Ingestion</h1>
        <p className="text-muted-foreground">
          Upload your documents to ground the assistant in your data. Supported formats: .txt, .md, .pdf, .docx, .html
        </p>
      </div>

      <GuardrailToggle />

      <MetadataFieldsSettings onFieldsChanged={fetchFieldDefinitions} />

      <FileUpload onUploadSuccess={handleUploadSuccess} />

      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Documents</h2>
        {isLoading ? (
          <div className="text-center py-8">Loading documents...</div>
        ) : error ? (
          <div className="text-red-500 py-8 text-center">{error}</div>
        ) : (
          <>
            <MetadataFilterBar
              documents={documents}
              fieldDefinitions={fieldDefinitions}
              onApplyFilter={handleApplyMetadataFilter}
              onClear={handleClearMetadataFilter}
            />
            <DocumentList
              documents={documents}
              onDelete={handleDeleteDocument}
              onToggleActive={handleToggleActive}
              onToggleAll={handleToggleAll}
            />
          </>
        )}
      </div>
    </div>
  )
}

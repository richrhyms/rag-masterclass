import React, { useState } from 'react'
import { supabase } from '@/lib/supabaseClient'
import { apiUrl } from '@/lib/apiClient'

interface FileUploadProps {
  onUploadSuccess: () => void
}

export const FileUpload: React.FC<FileUploadProps> = ({ onUploadSuccess }) => {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  const uploadFile = async (file: File) => {
    try {
      const jwt = (await supabase.auth.getSession()).data.session?.access_token
      if (!jwt) throw new Error('No active session')

      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch(apiUrl('/documents'), {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${jwt}`,
          // Content-Type is omitted to allow the browser to set multipart/form-data with boundary
        },
        body: formData,
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw { error: errorData.error || 'Upload failed', code: errorData.code }
      }

      return true
    } catch (err: any) {
      throw err
    }
  }

  const uploadFiles = async (files: FileList | File[]) => {
    setUploading(true)
    setError(null)

    try {
      const uploadPromises = Array.from(files).map(file => uploadFile(file))
      await Promise.all(uploadPromises)
      onUploadSuccess()
    } catch (err: any) {
      setError(err.error || 'One or more files failed to upload')
    } finally {
      setUploading(false)
    }
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files) return
    await uploadFiles(files)
    // Allow re-selecting the same file(s) again after a successful/failed upload
    e.target.value = ''
  }

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    if (!uploading) setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
  }

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
    if (uploading) return
    const files = e.dataTransfer.files
    if (!files || files.length === 0) return
    await uploadFiles(files)
  }

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`border-2 border-dashed rounded-lg p-8 text-center space-y-4 transition-colors ${
        isDragging ? 'border-blue-400 bg-blue-50' : 'border-gray-300 bg-gray-50 hover:bg-gray-100'
      }`}
    >
      <div className="space-y-2">
        <p className="text-sm text-gray-600">
          Drag and drop files here, or click to select
        </p>
        <p className="text-xs text-gray-400">
          .txt, .md, .pdf (max 5MB per file)
        </p>
      </div>

      <div className="flex justify-center">
        <label className="cursor-pointer bg-white border border-gray-300 px-4 py-2 rounded-md text-sm font-medium hover:bg-gray-50 transition-colors">
          {uploading ? 'Uploading...' : 'Select Files'}
          <input
            type="file"
            className="hidden"
            multiple
            accept=".txt,.md,.pdf"
            onChange={handleFileChange}
            disabled={uploading}
          />
        </label>
      </div>

      {error && (
        <div className="text-red-500 text-sm font-medium">
          {error}
        </div>
      )}
    </div>
  )
}

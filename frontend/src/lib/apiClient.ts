import { supabase } from './supabaseClient'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL

if (!API_BASE_URL) {
  throw new Error('Missing VITE_API_BASE_URL environment variable')
}

async function getJwt() {
  const { data: { session } } = await supabase.auth.getSession()
  if (!session) throw new Error('No active session')
  return session.access_token
}

export async function apiRequest<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const jwt = await getJwt()

  const headers = {
    ...options.headers,
    'Authorization': `Bearer ${jwt}`,
    'Content-Type': 'application/json',
  }

  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw {
      status: response.status,
      error: errorData.error || 'An unexpected error occurred',
      code: errorData.code || 'unknown_error',
    }
  }

  if (response.status === 204) return {} as T

  return response.json()
}

import { supabase } from './supabaseClient'

// ---------------------------------------------------------------------------
// Base-URL convention (single source of truth for the whole frontend):
//
// `VITE_API_BASE_URL` holds the backend ORIGIN ONLY (e.g. `http://localhost:8000`)
// WITHOUT a trailing `/api`. In local dev it is typically left empty (""), so
// requests are made relative to the Vite dev server's own origin and are
// forwarded to the backend by the `/api` -> `http://localhost:8000` proxy
// configured in `vite.config.ts`. In production it should be set to the
// deployed backend's origin.
//
// `apiUrl()` below is the ONLY place that prepends `/api`. Every call site in
// this codebase (apiRequest callers, sse.ts, and any raw `fetch` calls such
// as the file-upload multipart request) MUST go through `apiUrl()` and pass a
// path WITHOUT an `/api` prefix (e.g. `/threads`, `/documents`) so that the
// resulting URL contains `/api` exactly once -- never `/api/api`, never zero.
// ---------------------------------------------------------------------------
const RAW_API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
// Normalize away any trailing slash so joining with the leading `/api` below
// can't accidentally produce a double slash.
const API_BASE_URL = RAW_API_BASE_URL.replace(/\/+$/, '')

export function apiUrl(path: string): string {
  return `${API_BASE_URL}/api${path}`
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

  const response = await fetch(apiUrl(endpoint), {
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

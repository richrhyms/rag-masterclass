import type { ChatSSEEvent } from './types'

// `url` must be a fully-resolved request URL (base-URL + `/api` + path) --
// build it with `apiUrl()` from `./apiClient` at the call site so the SSE
// request follows the same single base-URL convention as every other REST
// call (see the comment block at the top of `apiClient.ts`).
export async function consumeChatStream(
  url: string,
  message: string,
  onEvent: (event: ChatSSEEvent) => void,
  onError: (error: { error: string; code: string }) => void
) {
  try {
    const { supabase } = await import('./supabaseClient')
    const { data: { session } } = await supabase.auth.getSession()
    if (!session) throw new Error('No active session')

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${session.access_token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ message }),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ error: 'SSE request failed' }))
      onError(errorData)
      return
    }

    const reader = response.body?.getReader()
    const decoder = new TextDecoder()

    if (!reader) throw new Error('Response body is null')

    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      let parts = buffer.split('\n\n')
      buffer = parts.pop() || ''

      for (const part of parts) {
        const lines = part.split('\n')
        let eventType = ''
        let data = ''

        for (const line of lines) {
          if (line.startsWith('event:')) {
            eventType = line.substring(6).trim()
          } else if (line.startsWith('data:')) {
            data = line.substring(6).trim()
          }
        }

        if (data) {
          try {
            const parsedData = JSON.parse(data)
            onEvent({ type: eventType as any, ...parsedData })
          } catch (e) {
            console.error('Failed to parse SSE data', data)
          }
        }
      }
    }
  } catch (e: any) {
    onError({ error: e.message, code: 'sse_error' })
  }
}

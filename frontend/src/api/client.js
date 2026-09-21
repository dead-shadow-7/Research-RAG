import { supabase } from '../lib/supabase'

// In dev this stays relative and Vite proxies it. In production there is no proxy, so
// the deployed build points straight at the API host via VITE_API_BASE, which Vite
// inlines at build time -- changing it needs a redeploy, not just a restart.
const BASE = import.meta.env.VITE_API_BASE ?? '/api'

async function expectOk(res) {
  if (res.ok) return res
  let detail = res.statusText
  try {
    const body = await res.json()
    detail = body.detail ?? detail
  } catch {
    // Non-JSON error body; the status text is the best we have.
  }
  const error = new Error(detail)
  // Carried so callers can tell "your session expired" from "the server broke" --
  // without it a 401 and a 500 are the same string and both get retried forever.
  error.status = res.status
  return Promise.reject(error)
}

/**
 * Every request to the API goes through here.
 *
 * `getSession()` returns the cached session and refreshes it when it is close to
 * expiring, so this is the one place a token is attached and the one place to change if
 * the scheme ever does.
 */
async function apiFetch(path, init = {}) {
  const {
    data: { session },
  } = await supabase.auth.getSession()

  const headers = new Headers(init.headers)
  if (session?.access_token) headers.set('Authorization', `Bearer ${session.access_token}`)

  return expectOk(await fetch(`${BASE}${path}`, { ...init, headers }))
}

export async function listDocuments() {
  return (await apiFetch('/documents')).json()
}

export async function uploadDocument(file) {
  const form = new FormData()
  form.append('file', file)
  // No Content-Type: FormData sets its own multipart boundary, and overriding it makes
  // the body unparseable on the server.
  return (await apiFetch('/documents', { method: 'POST', body: form })).json()
}

export async function ingestUrl(url) {
  const res = await apiFetch('/documents/url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  })
  return res.json()
}

export async function deleteDocument(id) {
  await apiFetch(`/documents/${id}`, { method: 'DELETE' })
}

/**
 * Stream a chat answer.
 *
 * Uses fetch + ReadableStream rather than EventSource, which cannot send a POST body.
 * Yields `{ event, data }` for each SSE frame.
 */
export async function* streamChat({ query, history = [], documentIds = null }, signal) {
  // Being fetch rather than EventSource pays off twice: it can POST a body, and it can
  // carry an Authorization header. EventSource can do neither.
  const res = await apiFetch('/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, history, document_ids: documentIds }),
    signal,
  })

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE frames are separated by a blank line.
    let split
    while ((split = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)

      let event = 'message'
      const dataLines = []
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      }
      if (dataLines.length) {
        yield { event, data: JSON.parse(dataLines.join('\n')) }
      }
    }
  }
}

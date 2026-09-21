import { useCallback, useRef, useState } from 'react'

import { streamChat } from '../api/client'

let counter = 0
const nextId = () => `m${++counter}`

// Citations live inside the markdown as links, so they survive block structure: a
// marker can sit mid-sentence, inside a list item or inside a table cell, and the
// markdown parser still sees one continuous document. Rendering each citation-delimited
// fragment separately would break every paragraph it interrupted.
const citeLink = (n) => `[${n}](#cite-${n})`
const CITE_LINK = /\[(\d+)\]\(#cite-\d+\)/g

const plainText = (msg) => (msg.markdown || '').replace(CITE_LINK, '')

function newAnswer() {
  return {
    id: nextId(),
    role: 'assistant',
    markdown: '',
    // Unique cited sources in first-appearance order; position here is the marker number.
    marks: [],
    sources: [],
    status: 'streaming',
    error: null,
    usage: null,
  }
}

function applyEvent(msg, event, data) {
  switch (event) {
    case 'sources':
      return { ...msg, sources: data }

    case 'token':
      return { ...msg, markdown: msg.markdown + data.text }

    case 'citation': {
      const marks = [...msg.marks]
      let index = marks.findIndex((m) => m.source === data.source)
      if (index === -1) {
        marks.push(data)
        index = marks.length - 1
      }
      return { ...msg, marks, markdown: msg.markdown + citeLink(index + 1) }
    }

    case 'done':
      return { ...msg, status: 'done', usage: data.usage }

    case 'error':
      return { ...msg, status: 'error', error: data.message }

    default:
      return msg
  }
}

// Keep in step with MAX_HISTORY_TURNS in api/app/schemas.py. The server is the
// authority; this is so an ordinary long conversation never reaches it.
const MAX_HISTORY_TURNS = 20

export function useChatStream() {
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const abortRef = useRef(null)

  const stop = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
  }, [])

  const ask = useCallback(
    async (query, documentIds = null) => {
      if (busy || !query.trim()) return

      const history = messages
        .filter((m) => m.status !== 'error')
        .map((m) => ({
          role: m.role,
          // The model gets its own words back without citation syntax it never wrote.
          content: m.role === 'user' ? m.text : plainText(m),
        }))
        .filter((t) => t.content)
        // The API rejects more than MAX_HISTORY_TURNS, because every turn is re-sent
        // and re-billed on every question. Send the most recent ones rather than
        // letting a long conversation turn into a 422.
        .slice(-MAX_HISTORY_TURNS)

      const answer = newAnswer()
      setMessages((prev) => [...prev, { id: nextId(), role: 'user', text: query }, answer])

      const controller = new AbortController()
      abortRef.current = controller
      setBusy(true)

      const update = (fn) =>
        setMessages((prev) => prev.map((m) => (m.id === answer.id ? fn(m) : m)))

      try {
        for await (const { event, data } of streamChat(
          { query, history, documentIds },
          controller.signal,
        )) {
          update((m) => applyEvent(m, event, data))
        }
        update((m) => (m.status === 'streaming' ? { ...m, status: 'done' } : m))
      } catch (err) {
        if (err.name === 'AbortError') {
          update((m) => ({ ...m, status: 'done' }))
        } else {
          update((m) => ({ ...m, status: 'error', error: err.message }))
        }
      } finally {
        abortRef.current = null
        setBusy(false)
      }
    },
    [busy, messages],
  )

  const reset = useCallback(() => {
    stop()
    setMessages([])
  }, [stop])

  return { messages, busy, ask, stop, reset }
}

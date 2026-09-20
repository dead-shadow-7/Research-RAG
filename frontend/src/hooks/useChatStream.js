import { useCallback, useRef, useState } from 'react'

import { streamChat } from '../api/client'

let counter = 0
const nextId = () => `m${++counter}`

const answerText = (msg) => msg.segments.map((s) => s.text).join('')

function newAnswer() {
  return {
    id: nextId(),
    role: 'assistant',
    // Text arrives in blocks; a block that carries citations closes its segment, so
    // markers land exactly where the model attributed the claim.
    segments: [{ text: '', citations: [] }],
    // Unique cited sources in first-appearance order -- this is the margin, and the
    // position in this list is the marker number.
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

    case 'token': {
      const segments = [...msg.segments]
      const last = segments[segments.length - 1]
      if (last.citations.length > 0) {
        segments.push({ text: data.text, citations: [] })
      } else {
        segments[segments.length - 1] = { ...last, text: last.text + data.text }
      }
      return { ...msg, segments }
    }

    case 'citation': {
      const marks = [...msg.marks]
      let index = marks.findIndex((m) => m.source === data.source)
      if (index === -1) {
        marks.push(data)
        index = marks.length - 1
      }
      const number = index + 1

      const segments = [...msg.segments]
      const last = segments[segments.length - 1]
      if (!last.citations.includes(number)) {
        segments[segments.length - 1] = { ...last, citations: [...last.citations, number] }
      }
      return { ...msg, marks, segments }
    }

    case 'done':
      return { ...msg, status: 'done', usage: data.usage }

    case 'error':
      return { ...msg, status: 'error', error: data.message }

    default:
      return msg
  }
}

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
          content: m.role === 'user' ? m.text : answerText(m),
        }))
        .filter((t) => t.content)

      const answer = newAnswer()
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'user', text: query },
        answer,
      ])

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

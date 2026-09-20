import { useRef, useState } from 'react'

import { isIndexing, useDocumentMutations, useDocuments } from '../hooks/useDocuments'

const ACCEPT = '.pdf,.doc,.docx,.xlsx,.xlsm,.txt,.md,.markdown'

const STATUS_LABEL = {
  queued: 'queued',
  parsing: 'parsing',
  chunking: 'chunking',
  embedding: 'embedding',
  ready: 'ready',
  failed: 'failed',
}

function StatusLine({ doc }) {
  if (doc.status === 'failed') {
    return <span className="text-alert">failed</span>
  }
  if (isIndexing(doc)) {
    return (
      <span className="text-signal">
        {STATUS_LABEL[doc.status]} · {doc.progress_pct}%
      </span>
    )
  }
  return (
    <span className="text-ink-faint">
      {doc.chunk_count} {doc.chunk_count === 1 ? 'chunk' : 'chunks'}
    </span>
  )
}

function DocumentRow({ doc, selected, onToggle, onRemove }) {
  const indexing = isIndexing(doc)
  return (
    <li className="group relative border-b border-rule last:border-b-0">
      <div className="flex items-start gap-3 px-4 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={() => onToggle(doc.id)}
          disabled={doc.status !== 'ready'}
          aria-label={`Search within ${doc.title}`}
          className="mt-1 size-3.5 shrink-0 accent-signal disabled:opacity-30"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] leading-snug" title={doc.title}>
            {doc.title}
          </p>
          <p className="mt-1 font-mono text-[10px] tracking-wide uppercase">
            <StatusLine doc={doc} />
          </p>
          {indexing && (
            <div className="mt-2 h-px w-full bg-rule">
              <div
                className="h-px bg-signal transition-[width] duration-500"
                style={{ width: `${Math.max(doc.progress_pct, 4)}%` }}
              />
            </div>
          )}
          {doc.status === 'failed' && doc.error && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-alert">{doc.error}</p>
          )}
        </div>
        <button
          onClick={() => onRemove(doc.id)}
          className="shrink-0 font-mono text-[10px] text-ink-faint opacity-0 transition hover:text-alert group-hover:opacity-100 focus-visible:opacity-100"
          aria-label={`Remove ${doc.title}`}
        >
          REMOVE
        </button>
      </div>
    </li>
  )
}

export default function Library({ selectedIds, onSelectionChange }) {
  const { data: documents = [], isLoading } = useDocuments()
  const { upload, addUrl, remove } = useDocumentMutations()
  const [dragging, setDragging] = useState(false)
  const [url, setUrl] = useState('')
  const fileInput = useRef(null)

  const addFiles = (files) => {
    for (const file of files) upload.mutate(file)
  }

  const toggle = (id) => {
    onSelectionChange(
      selectedIds.includes(id) ? selectedIds.filter((x) => x !== id) : [...selectedIds, id],
    )
  }

  const submitUrl = (e) => {
    e.preventDefault()
    if (!url.trim()) return
    addUrl.mutate(url.trim())
    setUrl('')
  }

  const error = upload.error || addUrl.error || remove.error

  return (
    <aside className="flex max-h-[45vh] w-full shrink-0 flex-col border-b border-rule bg-panel md:h-full md:max-h-none md:w-[320px] md:border-r md:border-b-0">
      <header className="border-b border-rule px-4 py-3">
        <h2 className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">
          Library
        </h2>
      </header>

      <div className="border-b border-rule p-4">
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragging(false)
            addFiles(e.dataTransfer.files)
          }}
          className={`rounded-sm border border-dashed px-4 py-6 text-center transition ${
            dragging ? 'border-signal bg-signal-soft' : 'border-rule bg-paper'
          }`}
        >
          <p className="text-[13px] text-ink-soft">
            Drop a file, or{' '}
            <button
              onClick={() => fileInput.current?.click()}
              className="text-signal underline underline-offset-2"
            >
              browse
            </button>
          </p>
          <p className="mt-1 font-mono text-[10px] tracking-wide text-ink-faint uppercase">
            PDF · Word · Excel · Text
          </p>
          <input
            ref={fileInput}
            type="file"
            accept={ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              addFiles(e.target.files)
              e.target.value = ''
            }}
          />
        </div>

        <form onSubmit={submitUrl} className="mt-3 flex flex-wrap gap-2">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/article"
            className="min-w-0 flex-1 border border-rule bg-paper px-2 py-1.5 text-[12px] placeholder:text-ink-faint focus:border-signal focus:outline-none"
          />
          <button
            type="submit"
            className="border border-rule px-2.5 py-1.5 font-mono text-[10px] tracking-wide text-ink-soft uppercase transition hover:border-signal hover:text-signal"
          >
            Add
          </button>
        </form>

        {error && <p className="mt-2 text-[11px] text-alert">{error.message}</p>}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="px-4 py-6 font-mono text-[10px] tracking-wide text-ink-faint uppercase">
            Loading…
          </p>
        ) : documents.length === 0 ? (
          <p className="px-4 py-6 text-[13px] leading-relaxed text-ink-soft">
            No documents yet. Add one to start asking.
          </p>
        ) : (
          <ul>
            {documents.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                selected={selectedIds.includes(doc.id)}
                onToggle={toggle}
                onRemove={(id) => remove.mutate(id)}
              />
            ))}
          </ul>
        )}
      </div>

      {selectedIds.length > 0 && (
        <footer className="border-t border-rule px-4 py-2.5">
          <p className="font-mono text-[10px] tracking-wide text-ink-soft uppercase">
            Searching {selectedIds.length} selected
            <button
              onClick={() => onSelectionChange([])}
              className="ml-2 text-signal underline underline-offset-2"
            >
              clear
            </button>
          </p>
        </footer>
      )}
    </aside>
  )
}

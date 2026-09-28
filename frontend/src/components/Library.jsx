import { useEffect, useRef, useState } from 'react'

import { isIndexing, useDocumentMutations, useDocuments } from '../hooks/useDocuments'
import {
  IconAlert,
  IconClose,
  IconPlus,
  IconSpinner,
  IconTrash,
  IconUpload,
  SourceIcon,
} from './icons'

const ACCEPT = '.pdf,.doc,.docx,.xlsx,.xlsm,.txt,.md,.markdown'

const STATUS_LABEL = {
  queued: 'queued',
  parsing: 'parsing',
  chunking: 'chunking',
  embedding: 'embedding',
}

function StatusLine({ doc }) {
  if (doc.status === 'failed') {
    return (
      <span className="flex items-center gap-1 text-alert">
        <IconAlert size={11} />
        failed
      </span>
    )
  }
  if (isIndexing(doc)) {
    return (
      <span className="flex items-center gap-1 text-signal">
        <IconSpinner size={11} />
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
    <li className="group relative border-b border-rule last:border-b-0 transition hover:bg-raised">
      <div className="flex items-start gap-3 px-4 py-3">
        {/* The label pads the 14px box out to a finger-sized target without making the
            box itself any bigger. */}
        <label className="-m-2 shrink-0 p-2 md:m-0 md:p-0">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggle(doc.id)}
            disabled={doc.status !== 'ready'}
            aria-label={`Search within ${doc.title}`}
            className="check mt-1 block"
          />
        </label>
        <span
          className={`mt-0.5 shrink-0 ${doc.status === 'ready' ? 'text-ink-soft' : 'text-ink-faint'}`}
        >
          <SourceIcon type={doc.source_type} size={15} />
        </span>
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
          // Hover-to-reveal has no hover on a touch screen, so below md it is always shown.
          className="-m-2 shrink-0 p-2 text-ink-faint transition hover:text-alert md:m-0 md:p-0 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"
          aria-label={`Remove ${doc.title}`}
          title="Remove"
        >
          <IconTrash size={15} />
        </button>
      </div>
    </li>
  )
}

// Keep in step with MAX_UPLOAD_MB in api/app/config.py. The server is the authority;
// this exists so an oversized file fails with a sentence instead of a network error.
const MAX_UPLOAD_MB = 8
const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

export default function Library({ selectedIds, onSelectionChange, open, onClose, summary }) {
  const { data: documents = [], isLoading, isError } = useDocuments()
  const { upload, addUrl, remove } = useDocumentMutations()
  const [dragging, setDragging] = useState(false)
  const [url, setUrl] = useState('')
  const fileInput = useRef(null)
  const [sizeError, setSizeError] = useState(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const addFiles = (files) => {
    // Checked here as well as on the server, because the server's rejection is not
    // readable in the browser: a body over Caddy's limit is refused by the proxy, which
    // has no CORS headers to add, so fetch() fails with a bare "NetworkError" instead
    // of a size message. Stopping it here means the bytes are never sent at all.
    const chosen = [...files]
    const tooBig = chosen.filter((f) => f.size > MAX_UPLOAD_BYTES)
    setSizeError(
      tooBig.length
        ? `${tooBig.map((f) => f.name).join(', ')} — over the ${MAX_UPLOAD_MB} MB limit.`
        : null,
    )
    for (const file of chosen.filter((f) => f.size <= MAX_UPLOAD_BYTES)) upload.mutate(file)
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

  const error = sizeError
    ? { message: sizeError }
    : upload.error || addUrl.error || remove.error

  return (
    <>
      {open && (
        <button
          onClick={onClose}
          aria-label="Close library"
          className="fixed inset-0 z-10 bg-black/60 md:hidden"
        />
      )}
      {/* Below md: an off-canvas drawer, so the chat keeps the whole screen and the page
          has one scroll area instead of a library strip stacked over the thread. At md
          and up it docks as the sidebar. */}
      <aside
        className={`edge-lit fixed inset-y-0 left-0 z-20 flex w-[min(22rem,88vw)] shrink-0 flex-col border-r border-rule bg-panel shadow-2xl shadow-black/50 transition-[translate,visibility] duration-200 ease-out md:visible md:static md:z-auto md:h-full md:w-[320px] md:translate-x-0 md:shadow-none ${
          // Invisible as well as off-screen when closed, so Tab can't walk into it.
          open ? 'translate-x-0' : 'invisible -translate-x-full'
        }`}
        aria-label="Library"
      >
        <header className="flex items-center justify-between gap-3 border-b border-rule px-4 py-3">
          <div className="flex items-baseline gap-3">
            <h2 className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">
              Library
            </h2>
            <p className="font-mono text-[10px] tracking-wide text-ink-faint uppercase md:hidden">
              {summary}
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close library"
            title="Close"
            className="-m-2 rounded-sm p-2 text-ink-soft transition hover:text-ink md:hidden"
          >
            <IconClose size={15} />
          </button>
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
            className={`rounded border border-dashed px-4 py-3 text-center transition md:py-6 ${
              dragging ? 'border-signal bg-signal-soft' : 'border-rule bg-raised'
            }`}
          >
            <span
              className={`hidden md:inline-flex ${dragging ? 'text-signal' : 'text-ink-faint'}`}
            >
              <IconUpload size={18} />
            </span>
            {/* Phones have nothing to drag from, so there the well is just a button. */}
            <button
              onClick={() => fileInput.current?.click()}
              className="flex w-full items-center justify-center gap-2 py-1 text-[14px] text-signal md:hidden"
            >
              <IconUpload size={16} />
              Upload a file
            </button>
            <p className="mt-2 hidden text-[13px] text-ink-soft md:block">
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

          <form onSubmit={submitUrl} className="mt-3 flex gap-2">
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/article"
              // 16px below md: iOS Safari zooms the page into any smaller input on focus.
              className="min-w-0 flex-1 rounded-sm border border-rule bg-raised px-2.5 py-2 text-[16px] placeholder:text-ink-faint focus:border-signal focus:outline-none md:py-1.5 md:text-[12px]"
            />
            <button
              type="submit"
              aria-label="Add URL"
              title="Add URL"
              className="flex shrink-0 items-center justify-center rounded-sm border border-rule px-3 text-ink-soft transition hover:border-signal hover:text-signal md:px-2"
            >
              <IconPlus size={15} />
            </button>
          </form>

          {error && <p className="mt-2 text-[11px] text-alert">{error.message}</p>}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {isLoading ? (
            <p className="px-4 py-6 font-mono text-[10px] tracking-wide text-ink-faint uppercase">
              Loading…
            </p>
          ) : isError ? (
            // An unreachable API must not look like an empty library -- that reads as
            // "my documents are gone" when nothing has been lost.
            <div className="px-4 py-6">
              <p className="flex items-center gap-1.5 font-mono text-[10px] tracking-wide text-alert uppercase">
                <IconAlert size={11} />
                Can't reach the server
              </p>
              <p className="mt-2 text-[13px] leading-relaxed text-ink-soft">
                Your documents are safe. Start the API on port 8000 and this will reload
                on its own.
              </p>
            </div>
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
    </>
  )
}

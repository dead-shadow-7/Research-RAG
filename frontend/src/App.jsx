import { useMemo, useState } from 'react'

import Composer from './components/Composer'
import Conversation from './components/Conversation'
import Library from './components/Library'
import SourceDrawer from './components/SourceDrawer'
import { useChatStream } from './hooks/useChatStream'
import { useDocuments } from './hooks/useDocuments'

export default function App() {
  const { data: documents = [] } = useDocuments()
  const { messages, busy, ask, stop, reset } = useChatStream()
  const [selectedIds, setSelectedIds] = useState([])
  const [openMark, setOpenMark] = useState(null)

  const readyDocs = useMemo(() => documents.filter((d) => d.status === 'ready'), [documents])
  const indexedChunks = readyDocs.reduce((n, d) => n + d.chunk_count, 0)
  const ready = readyDocs.length > 0

  // The drawer shows the passage behind a citation, which lives on the answer that
  // produced it.
  const sourcesForOpenMark = useMemo(() => {
    const answer = [...messages].reverse().find((m) => m.marks?.some((k) => k === openMark))
    return answer?.sources ?? []
  }, [messages, openMark])

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-rule bg-panel px-5 py-2.5">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-[17px] tracking-tight">Marginalia</h1>
          <p className="font-mono text-[10px] tracking-wide text-ink-faint uppercase">
            {readyDocs.length} indexed · {indexedChunks} chunks
          </p>
        </div>
        {messages.length > 0 && (
          <button
            onClick={reset}
            className="font-mono text-[10px] tracking-wide text-ink-soft uppercase hover:text-ink"
          >
            New thread
          </button>
        )}
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <Library selectedIds={selectedIds} onSelectionChange={setSelectedIds} />

        <main className="flex min-w-0 flex-1 flex-col">
          <Conversation messages={messages} ready={ready} onOpenSource={setOpenMark} />
          <Composer
            onAsk={(q) => ask(q, selectedIds.length ? selectedIds : null)}
            onStop={stop}
            busy={busy}
            disabled={!ready}
            hint="Add a document to start asking"
          />
        </main>
      </div>

      <SourceDrawer
        mark={openMark}
        sources={sourcesForOpenMark}
        onClose={() => setOpenMark(null)}
      />
    </div>
  )
}

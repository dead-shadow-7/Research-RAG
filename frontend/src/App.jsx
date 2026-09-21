import { useMemo, useState } from 'react'

import AuthScreen from './auth/AuthScreen'
import { useAuth } from './auth/AuthProvider'
import Composer from './components/Composer'
import Conversation from './components/Conversation'
import Library from './components/Library'
import SourceDrawer from './components/SourceDrawer'
import StarField from './components/StarField'
import { useChatStream } from './hooks/useChatStream'
import { useDocuments } from './hooks/useDocuments'

export default function App() {
  const { user, loading } = useAuth()

  // Two screens gated by one condition -- not worth a router. Rendering nothing while
  // the stored session loads avoids flashing the sign-in form at someone already signed
  // in.
  if (loading) return <div className="h-full bg-paper" />
  if (!user) return <AuthScreen />
  return <Workspace />
}

function Workspace() {
  const { user, signOut } = useAuth()
  const { data: documents = [], isError } = useDocuments()
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
      <header className="edge-lit flex items-center justify-between border-b border-rule bg-panel px-5 py-2.5">
        <div className="flex items-baseline gap-3">
          <h1 className="font-display text-[17px] tracking-tight">Marginalia</h1>
          <p className="font-mono text-[10px] tracking-wide text-ink-faint uppercase">
            {isError ? 'offline' : `${readyDocs.length} indexed · ${indexedChunks} chunks`}
          </p>
        </div>
        <div className="flex items-center gap-4">
          {messages.length > 0 && (
            <button
              onClick={reset}
              className="font-mono text-[10px] tracking-wide text-ink-soft uppercase hover:text-ink"
            >
              New thread
            </button>
          )}
          <span className="hidden max-w-[16rem] truncate font-mono text-[10px] tracking-wide text-ink-faint sm:block">
            {user.email}
          </span>
          <button
            onClick={signOut}
            className="font-mono text-[10px] tracking-wide text-ink-soft uppercase hover:text-ink"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <Library selectedIds={selectedIds} onSelectionChange={setSelectedIds} />

        {/* The star field is a backdrop: it sits behind the thread, and the composer's
            own panel covers it at the bottom. */}
        <main className="relative flex min-w-0 flex-1 flex-col bg-void">
          <StarField />
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

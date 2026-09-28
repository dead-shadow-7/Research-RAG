import { useEffect, useRef } from 'react'

import Answer from './Answer'

function Question({ text }) {
  return (
    <div className="max-w-[var(--w-reading)] border-l-2 border-signal pl-4">
      <p className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">Asked</p>
      <p className="mt-1.5 text-[15px] leading-relaxed text-ink">{text}</p>
    </div>
  )
}

/**
 * A centred composition rather than content pinned to the top-left: with nothing in
 * the thread yet, left-and-top alignment leaves the copy orphaned in the corner of a
 * very tall pane. Centred on both axes, it reads as a deliberate title card and sits
 * squarely above the composer.
 */
function EmptyState({ ready, onOpenLibrary }) {
  return (
    <div className="max-w-[34rem] text-center">
      <p className="font-mono text-[10px] tracking-[0.3em] text-ink-faint uppercase">
        Grounded answers
      </p>
      <h2 className="mt-4 font-display text-[23px] leading-[1.25] tracking-tight md:mt-5 md:text-[31px]">
        Ask a question. Every claim comes back with the passage it came from.
      </h2>
      <span className="mx-auto mt-5 block h-px w-10 bg-signal/50 md:mt-7" />
      <p className="mx-auto mt-5 max-w-[30rem] text-[14px] leading-relaxed text-ink-soft md:mt-7">
        {ready
          ? 'Answers are drawn only from the documents in your library. Markers in the text open the exact passage in the margin.'
          : 'Add a document to your library. Indexing runs in the background, and the composer unlocks as soon as the first one is ready.'}
      </p>
      {/* On a phone the library is tucked in a drawer, so the first step needs a door. */}
      {!ready && (
        <button
          onClick={onOpenLibrary}
          className="mt-6 rounded-sm border border-signal/40 bg-signal-soft px-4 py-2.5 font-mono text-[11px] tracking-wide text-signal uppercase transition hover:border-signal md:hidden"
        >
          Open library
        </button>
      )}
    </div>
  )
}

export default function Conversation({ messages, ready, onOpenSource, onOpenLibrary }) {
  const bottom = useRef(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-y-auto px-5 py-10 md:px-8">
        <EmptyState ready={ready} onOpenLibrary={onOpenLibrary} />
      </div>
    )
  }

  return (
    <div className="relative min-h-0 flex-1 overflow-y-auto px-5 py-8 md:px-8">
      <div className="mx-auto w-full max-w-[var(--w-thread)] space-y-8">
        {messages.map((message, i) =>
          message.role === 'user' ? (
            <div key={message.id} className={i > 0 ? 'border-t border-rule pt-8' : undefined}>
              <Question text={message.text} />
            </div>
          ) : (
            <Answer key={message.id} message={message} onOpenSource={onOpenSource} />
          ),
        )}
      </div>
      <div ref={bottom} className="h-px" />
    </div>
  )
}

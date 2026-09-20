import { useEffect, useRef } from 'react'

import Answer from './Answer'

function Question({ text }) {
  return (
    <div className="max-w-[62ch]">
      <p className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">Asked</p>
      <p className="mt-1.5 text-[15px] leading-relaxed text-ink-soft">{text}</p>
    </div>
  )
}

function EmptyState({ ready }) {
  return (
    <div>
      <div className="max-w-[46ch]">
        <p className="font-display text-[22px] leading-tight md:text-[26px]">
          Ask a question. Every claim comes back with the passage it came from.
        </p>
        <p className="mt-4 text-[14px] leading-relaxed text-ink-soft">
          {ready
            ? 'Answers are drawn only from the documents in your library. Markers in the text open the exact passage in the margin.'
            : 'Add a document to your library. Indexing runs in the background, and the composer unlocks as soon as the first one is ready.'}
        </p>
      </div>
    </div>
  )
}

export default function Conversation({ messages, ready, onOpenSource }) {
  const bottom = useRef(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-8 md:px-8 md:py-10">
        <EmptyState ready={ready} />
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-5 py-8 md:px-8">
      <div className="space-y-10">
        {messages.map((message) =>
          message.role === 'user' ? (
            <Question key={message.id} text={message.text} />
          ) : (
            <Answer key={message.id} message={message} onOpenSource={onOpenSource} />
          ),
        )}
      </div>
      <div ref={bottom} className="h-px" />
    </div>
  )
}

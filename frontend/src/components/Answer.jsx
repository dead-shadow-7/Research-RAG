/**
 * The answer is rendered as a page with a margin, not as a chat bubble.
 *
 * Claims carry superscript markers; the sources they point to dock in the right-hand
 * margin as they stream in. The marker number is the source's position in the margin,
 * so the same source reused later keeps its number -- ordinary citation behaviour.
 */

function CiteMarker({ number, mark, onOpen }) {
  return (
    <button
      onClick={() => onOpen(mark)}
      title={mark ? `${mark.title}` : undefined}
      className="align-super font-mono text-[10px] text-signal transition hover:bg-signal-soft"
      aria-label={`Source ${number}${mark ? `: ${mark.title}` : ''}`}
    >
      [{number}]
    </button>
  )
}

function MarginNote({ number, mark, onOpen }) {
  return (
    <li className="dock-in">
      <button
        onClick={() => onOpen(mark)}
        className="group block w-full text-left"
      >
        <span className="font-mono text-[10px] text-signal">[{number}]</span>
        <span className="mt-0.5 block truncate text-[11px] text-ink-soft group-hover:text-ink">
          {mark.title}
        </span>
        {/* A verified quote is shown as one; a retrieved snippet is not dressed up as
            something the model actually quoted. */}
        {mark.cited_text ? (
          <span className="mt-1 block font-display text-[12.5px] leading-snug text-ink-soft italic">
            “{mark.cited_text.slice(0, 110)}
            {mark.cited_text.length > 110 ? '…' : ''}”
          </span>
        ) : mark.snippet ? (
          <span className="mt-1 block font-display text-[12.5px] leading-snug text-ink-soft">
            {mark.snippet.slice(0, 110)}
            {mark.snippet.length > 110 ? '…' : ''}
          </span>
        ) : null}
      </button>
    </li>
  )
}

export default function Answer({ message, onOpenSource }) {
  const streaming = message.status === 'streaming'
  const hasText = message.segments.some((s) => s.text)

  if (message.status === 'error') {
    return (
      <div className="border-l-2 border-alert bg-alert-soft px-4 py-3">
        <p className="font-mono text-[10px] tracking-wide text-alert uppercase">
          Request failed
        </p>
        <p className="mt-1 text-[13px] leading-relaxed text-ink">{message.error}</p>
      </div>
    )
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,62ch)_13rem]">
      <div className="font-display text-[18px] leading-[1.65] whitespace-pre-wrap">
        {message.segments.map((seg, i) => (
          <span key={i}>
            {seg.text}
            {seg.citations.map((n) => (
              <CiteMarker key={n} number={n} mark={message.marks[n - 1]} onOpen={onOpenSource} />
            ))}
          </span>
        ))}
        {streaming && (hasText ? <span className="caret" /> : <ThinkingLine />)}
      </div>

      {message.marks.length > 0 && (
        <aside className="lg:border-l lg:border-rule lg:pl-4">
          <h3 className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">
            Sources
          </h3>
          <ul className="mt-2.5 space-y-3">
            {message.marks.map((mark, i) => (
              <MarginNote key={mark.source ?? i} number={i + 1} mark={mark} onOpen={onOpenSource} />
            ))}
          </ul>
        </aside>
      )}
    </div>
  )
}

function ThinkingLine() {
  return (
    <span className="font-mono text-[11px] tracking-wide text-ink-faint uppercase">
      Reading sources<span className="caret" />
    </span>
  )
}

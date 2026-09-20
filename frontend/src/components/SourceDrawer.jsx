import { useEffect } from 'react'

/**
 * The full retrieved passage behind a citation.
 *
 * `mark.source` is the chunk's vector id, which is also what the retrieval step
 * reports in its `sources` event -- so the exact passage the model saw can be shown,
 * not an approximation of it.
 */
export default function SourceDrawer({ mark, sources, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!mark) return null

  const chunk = sources.find((s) => s.vector_id === mark.source)

  return (
    <>
      <button
        onClick={onClose}
        aria-label="Close source"
        className="fixed inset-0 z-10 bg-ink/15"
      />
      <div
        role="dialog"
        aria-label="Source passage"
        className="dock-in fixed top-0 right-0 z-20 flex h-full w-[min(30rem,100vw)] flex-col border-l border-rule bg-panel shadow-xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-rule px-5 py-4">
          <div className="min-w-0">
            <p className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">
              Source
            </p>
            <h3 className="mt-1 truncate text-[14px]">{mark.title}</h3>
          </div>
          <button
            onClick={onClose}
            className="shrink-0 font-mono text-[10px] tracking-wide text-ink-soft uppercase hover:text-ink"
          >
            Close
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          {mark.cited_text && (
            <section>
              <h4 className="font-mono text-[10px] tracking-[0.18em] text-signal uppercase">
                Cited
              </h4>
              <blockquote className="mt-2 border-l-2 border-signal bg-signal-soft px-3 py-2 font-display text-[15px] leading-relaxed">
                {mark.cited_text}
              </blockquote>
            </section>
          )}

          <section className="mt-6">
            <h4 className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">
              Retrieved passage
            </h4>
            {chunk ? (
              <>
                <p className="mt-2 font-display text-[15px] leading-relaxed whitespace-pre-wrap">
                  {chunk.snippet}
                </p>
                <p className="mt-3 font-mono text-[10px] tracking-wide text-ink-faint uppercase">
                  {chunk.page_from != null && `page ${chunk.page_from} · `}
                  similarity {chunk.score?.toFixed(3)}
                </p>
              </>
            ) : (
              <p className="mt-2 text-[13px] text-ink-soft">
                This passage is no longer in the current result set.
              </p>
            )}
          </section>
        </div>
      </div>
    </>
  )
}

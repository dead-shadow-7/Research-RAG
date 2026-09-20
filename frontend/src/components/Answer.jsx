import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * The answer is rendered as a page with a margin, not as a chat bubble.
 *
 * Citations arrive as markdown links with a `#cite-n` href, so they parse as ordinary
 * inline content and can sit anywhere — mid-sentence, in a list item, in a table cell.
 * The link component below swaps them for the superscript marker. The source they point
 * to docks in the right-hand margin, and the marker number is its position there, so a
 * source reused later keeps its number.
 */

const CITE_HREF = /^#cite-(\d+)$/

// Retrieved text often carries form-feed runs and signature rules ("____________"),
// which are noise in a 110-character preview.
const tidy = (s) => s.replace(/[_\-·.]{3,}/g, ' ').replace(/\s+/g, ' ').trim()

function CiteMarker({ number, mark, onOpen }) {
  const open = () => mark && onOpen(mark)
  // A <button> is an atomic inline box, and browsers may wrap at its boundary -- which
  // stranded the sentence's full stop on its own line. A span participates in normal
  // inline layout, so "studies[1]." stays together; role and key handling keep it a
  // real control for keyboard and screen-reader users.
  return (
    <span
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          open()
        }
      }}
      title={mark?.title}
      className="align-super font-mono text-[10px] text-signal transition hover:bg-signal-soft"
      aria-label={`Source ${number}${mark ? `: ${mark.title}` : ''}`}
    >
      [{number}]
    </span>
  )
}

function MarginNote({ number, mark, onOpen }) {
  const preview = mark.cited_text || mark.snippet || ''
  return (
    <li className="dock-in">
      <button
        onClick={() => onOpen(mark)}
        className="group block w-full border-l border-rule pl-3 text-left transition hover:border-signal"
      >
        <span className="flex items-baseline gap-1.5">
          <span className="font-mono text-[10px] text-signal">[{number}]</span>
          <span className="truncate text-[11px] text-ink-soft group-hover:text-ink">
            {mark.title}
          </span>
        </span>
        {preview && (
          <span className="mt-1 block font-display text-[12.5px] leading-snug text-ink-faint">
            {tidy(preview).slice(0, 120)}
            {tidy(preview).length > 120 ? '…' : ''}
          </span>
        )}
      </button>
    </li>
  )
}

export default function Answer({ message, onOpenSource }) {
  const streaming = message.status === 'streaming'

  const components = useMemo(
    () => ({
      a({ href, children, ...props }) {
        const match = CITE_HREF.exec(href || '')
        if (match) {
          const number = Number(match[1])
          // A marker is an atomic inline box, so the browser may wrap between it and
          // the text either side -- which strands the sentence's full stop on its own
          // line. The word joiners (U+2060) remove those break opportunities.
          return (
            <>
              {'⁠'}
              <CiteMarker number={number} mark={message.marks[number - 1]} onOpen={onOpenSource} />
              {'⁠'}
            </>
          )
        }
        return (
          <a
            {...props}
            href={href}
            target="_blank"
            rel="noreferrer"
            className="text-signal underline underline-offset-2"
          >
            {children}
          </a>
        )
      },
    }),
    [message.marks, onOpenSource],
  )

  if (message.status === 'error') {
    return (
      <div className="border-l-2 border-alert bg-alert-soft px-4 py-3">
        <p className="font-mono text-[10px] tracking-wide text-alert uppercase">Request failed</p>
        <p className="mt-1 text-[13px] leading-relaxed text-ink">{message.error}</p>
      </div>
    )
  }

  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,68ch)_15rem]">
      <div className="answer-prose">
        {message.markdown ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
            {message.markdown}
          </ReactMarkdown>
        ) : (
          streaming && <ThinkingLine />
        )}
        {streaming && message.markdown && <span className="caret" />}
      </div>

      {message.marks.length > 0 && (
        <aside className="lg:sticky lg:top-8 lg:self-start">
          <h3 className="font-mono text-[10px] tracking-[0.18em] text-ink-faint uppercase">
            Sources
          </h3>
          <ul className="mt-3 space-y-3.5">
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
    <p className="font-mono text-[11px] tracking-wide text-ink-faint uppercase">
      Reading sources<span className="caret" />
    </p>
  )
}

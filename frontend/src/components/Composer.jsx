import { useRef, useState } from 'react'

import { IconSend } from './icons'

export default function Composer({ onAsk, onStop, busy, disabled, hint }) {
  const [value, setValue] = useState('')
  const textarea = useRef(null)

  const submit = () => {
    if (busy || disabled || !value.trim()) return
    onAsk(value.trim())
    setValue('')
    if (textarea.current) textarea.current.style.height = 'auto'
  }

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="edge-lit relative border-t border-rule bg-panel px-5 py-4 md:px-8">
      {/* Exactly the width of the thread above it, so the two share an edge. */}
      <div className="mx-auto w-full max-w-[var(--w-thread)]">
        <div className="flex items-end gap-3 rounded border border-rule bg-raised px-3 py-2 transition focus-within:border-signal">
          <textarea
            ref={textarea}
            rows={1}
            value={value}
            disabled={disabled}
            // Matches MAX_MESSAGE_CHARS in api/app/schemas.py; the server still enforces it.
            maxLength={4000}
            onChange={(e) => {
              setValue(e.target.value)
              e.target.style.height = 'auto'
              e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`
            }}
            onKeyDown={onKeyDown}
            placeholder={disabled ? hint : 'Ask about your documents'}
            className="max-h-40 min-w-0 flex-1 resize-none bg-transparent text-[14px] leading-relaxed placeholder:text-ink-faint focus:outline-none disabled:cursor-not-allowed"
          />
          {busy ? (
            <button
              onClick={onStop}
              className="mb-0.5 flex size-7 shrink-0 items-center justify-center rounded-sm border border-rule text-ink-soft transition hover:border-alert hover:text-alert"
              aria-label="Stop generating"
              title="Stop"
            >
              <span className="size-2 bg-current" />
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={disabled || !value.trim()}
              className="mb-0.5 flex size-7 shrink-0 items-center justify-center rounded-sm border border-signal/40 bg-signal-soft text-signal transition hover:border-signal disabled:border-rule disabled:bg-transparent disabled:text-ink-faint"
              aria-label="Ask"
              title="Ask"
            >
              <IconSend size={15} />
            </button>
          )}
        </div>
        <p className="mt-1.5 font-mono text-[10px] tracking-wide text-ink-faint uppercase">
          Enter to ask · Shift + Enter for a new line
        </p>
      </div>
    </div>
  )
}

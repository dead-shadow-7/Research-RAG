import { useRef, useState } from 'react'

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
    <div className="border-t border-rule bg-panel px-5 py-4 md:px-8">
      {/* Aligned with the reading column above it, not centred in the pane. */}
      <div className="max-w-[62ch]">
        <div className="flex items-end gap-3 border border-rule bg-paper px-3 py-2 focus-within:border-signal">
          <textarea
            ref={textarea}
            rows={1}
            value={value}
            disabled={disabled}
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
              className="shrink-0 font-mono text-[10px] tracking-wide text-ink-soft uppercase hover:text-alert"
            >
              Stop
            </button>
          ) : (
            <button
              onClick={submit}
              disabled={disabled || !value.trim()}
              className="shrink-0 font-mono text-[10px] tracking-wide text-signal uppercase transition disabled:text-ink-faint"
            >
              Ask
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

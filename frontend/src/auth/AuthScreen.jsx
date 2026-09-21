import { useState } from 'react'

import StarField from '../components/StarField'
import { supabase } from '../lib/supabase'

const FIELD =
  'w-full rounded border border-rule bg-raised px-3 py-2 text-[14px] transition ' +
  'placeholder:text-ink-faint focus:border-signal focus:outline-none'

export default function AuthScreen() {
  const [mode, setMode] = useState('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const signingUp = mode === 'signup'

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const { data, error: failure } = signingUp
        ? await supabase.auth.signUp({ email, password })
        : await supabase.auth.signInWithPassword({ email, password })

      if (failure) {
        setError(failure.message)
      } else if (!data.session) {
        // Signing up returns a session unless the project still requires email
        // confirmation. Say which, rather than leaving the form sitting there having
        // apparently done nothing.
        setError(
          'Account created, but this project still has email confirmation switched on. ' +
            'Check your inbox, or turn off "Confirm email" in Supabase.',
        )
      }
      // On success AuthProvider's onAuthStateChange swaps this screen out; there is
      // nothing to navigate to.
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden bg-void px-5">
      <StarField />

      <div className="relative w-full max-w-[22rem]">
        <header className="mb-7 text-center">
          <h1 className="font-display text-[26px] tracking-tight">Marginalia</h1>
          <p className="mt-1 font-mono text-[10px] tracking-wide text-ink-faint uppercase">
            Your documents, answered with sources
          </p>
        </header>

        <form onSubmit={submit} className="edge-lit rounded border border-rule bg-panel p-5">
          <label className="mb-3 block">
            <span className="mb-1.5 block font-mono text-[10px] tracking-wide text-ink-faint uppercase">
              Email
            </span>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={FIELD}
            />
          </label>

          <label className="block">
            <span className="mb-1.5 block font-mono text-[10px] tracking-wide text-ink-faint uppercase">
              Password
            </span>
            <input
              type="password"
              required
              minLength={6}
              autoComplete={signingUp ? 'new-password' : 'current-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={FIELD}
            />
          </label>

          {error && (
            <p className="mt-3 rounded border border-alert/40 bg-alert-soft px-3 py-2 text-[13px] text-alert">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="mt-4 w-full rounded-sm border border-signal/40 bg-signal-soft py-2 text-[13px] text-signal transition hover:border-signal disabled:border-rule disabled:bg-transparent disabled:text-ink-faint"
          >
            {busy ? 'Working…' : signingUp ? 'Create account' : 'Sign in'}
          </button>

          {/* Sign in and sign up stay separate on purpose. A single "continue" button
              that creates the account when the password does not match would turn a
              typo'd email into a new, empty library rather than an error. */}
          <p className="mt-4 text-center text-[13px] text-ink-faint">
            {signingUp ? 'Already have an account?' : 'No account yet?'}{' '}
            <button
              type="button"
              onClick={() => {
                setMode(signingUp ? 'signin' : 'signup')
                setError(null)
              }}
              className="text-ink-soft underline decoration-rule underline-offset-2 transition hover:text-signal hover:decoration-signal"
            >
              {signingUp ? 'Sign in' : 'Create one'}
            </button>
          </p>
        </form>
      </div>
    </div>
  )
}

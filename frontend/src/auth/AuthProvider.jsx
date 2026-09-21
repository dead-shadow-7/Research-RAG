import { useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useState } from 'react'

import { supabase } from '../lib/supabase'

const AuthContext = createContext(null)

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>')
  return value
}

export function AuthProvider({ children }) {
  const [session, setSession] = useState(null)
  // Starts true so the app renders nothing rather than flashing the sign-in form while
  // the stored session is still being read.
  const [loading, setLoading] = useState(true)
  const queryClient = useQueryClient()

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session)
      setLoading(false)
    })

    // Covers sign-in, sign-out and the silent token refresh. Signing up is included:
    // with email confirmation off, Supabase returns a session immediately and this is
    // what swaps the sign-in screen out.
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next))
    return () => data.subscription.unsubscribe()
  }, [])

  const value = useMemo(
    () => ({
      session,
      user: session?.user ?? null,
      loading,
      signOut: async () => {
        await supabase.auth.signOut()
        // Scoping the query key to the user is not enough on its own: the previous
        // user's documents would still be sitting in the cache for anyone who signs in
        // next on this machine.
        queryClient.clear()
      },
    }),
    [session, loading, queryClient],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

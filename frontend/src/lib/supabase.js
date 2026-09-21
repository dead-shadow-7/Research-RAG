import { createClient } from '@supabase/supabase-js'

// The anon key is designed to be published in a browser bundle -- it grants nothing on
// its own. Authority comes from the JWT the user signs in for, which the API verifies
// against Supabase's public keys. Do not reach for the service-role key here.
const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!url || !anonKey) {
  // Vite inlines these at build time, so a missing value is a build/deploy problem, not
  // something a restart fixes. Failing loudly here beats a blank sign-in form.
  throw new Error('VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY must be set')
}

export const supabase = createClient(url, anonKey)

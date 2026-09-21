import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { deleteDocument, ingestUrl, listDocuments, uploadDocument } from '../api/client'
import { useAuth } from '../auth/AuthProvider'

const INDEXING = new Set(['queued', 'parsing', 'chunking', 'embedding'])

export function isIndexing(doc) {
  return INDEXING.has(doc.status)
}

// Scoped to the user so one account's library can never be served from cache to the
// next. AuthProvider.signOut also clears the cache outright, which is what actually
// evicts it from memory.
const documentsKey = (userId) => ['documents', userId]

export function useDocuments() {
  const { user } = useAuth()

  return useQuery({
    queryKey: documentsKey(user?.id),
    queryFn: listDocuments,
    enabled: Boolean(user),
    // Poll while something is indexing, and keep retrying while the API is
    // unreachable so the UI recovers by itself once it is back. Quiet otherwise.
    refetchInterval: (query) => {
      // A 401 never fixes itself by asking again; AuthProvider handles the session.
      if (query.state.error?.status === 401) return false
      if (query.state.status === 'error') return 5000
      return query.state.data?.some(isIndexing) ? 2000 : false
    },
  })
}

export function useDocumentMutations() {
  const qc = useQueryClient()
  const { user } = useAuth()
  const invalidate = () => qc.invalidateQueries({ queryKey: documentsKey(user?.id) })

  return {
    upload: useMutation({ mutationFn: uploadDocument, onSuccess: invalidate }),
    addUrl: useMutation({ mutationFn: ingestUrl, onSuccess: invalidate }),
    remove: useMutation({ mutationFn: deleteDocument, onSuccess: invalidate }),
  }
}

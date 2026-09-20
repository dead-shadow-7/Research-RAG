import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { deleteDocument, ingestUrl, listDocuments, uploadDocument } from '../api/client'

const INDEXING = new Set(['queued', 'parsing', 'chunking', 'embedding'])

export function isIndexing(doc) {
  return INDEXING.has(doc.status)
}

export function useDocuments() {
  return useQuery({
    queryKey: ['documents'],
    queryFn: listDocuments,
    // Poll only while something is still being indexed, then fall quiet.
    refetchInterval: (query) => (query.state.data?.some(isIndexing) ? 2000 : false),
  })
}

export function useDocumentMutations() {
  const qc = useQueryClient()
  const invalidate = () => qc.invalidateQueries({ queryKey: ['documents'] })

  return {
    upload: useMutation({ mutationFn: uploadDocument, onSuccess: invalidate }),
    addUrl: useMutation({ mutationFn: ingestUrl, onSuccess: invalidate }),
    remove: useMutation({ mutationFn: deleteDocument, onSuccess: invalidate }),
  }
}

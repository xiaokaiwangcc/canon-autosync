import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

const PAGE_SIZE = 200

function refreshFirstPage(previous, firstPage) {
  if (!previous || !firstPage.next_cursor) return firstPage

  // Keep the loaded tail when the refreshed page still joins it. A change in
  // the remaining count means a file may have changed outside the first page.
  const last = firstPage.items[firstPage.items.length - 1]
  const boundary = previous.items.findIndex((item) => item.id === last.id)
  if (boundary < 0 || previous.total - boundary - 1 !== firstPage.total - firstPage.items.length) {
    return firstPage
  }
  return {
    ...firstPage,
    items: [...firstPage.items, ...previous.items.slice(boundary + 1)],
    next_cursor: previous.next_cursor,
  }
}

export function useBackedUpFiles(query) {
  const [result, setResult] = useState(null)
  const [revision, setRevision] = useState(0)
  const loadNextRef = useRef(null)

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    let page = null
    let firstPageFailed = false
    let nextPageFailed = false
    let inFlight = null
    let nextRequested = false

    const publish = (loadingMore = false) => {
      setResult({ query, revision, page, firstPageFailed, nextPageFailed, loadingMore })
    }

    const load = async (next = false) => {
      const cursor = next ? page?.next_cursor : null
      if (signal.aborted || (next && !cursor)) return
      if (inFlight) {
        if (next && inFlight === 'first') nextRequested = true
        return
      }
      inFlight = next ? 'next' : 'first'
      if (next) {
        nextPageFailed = false
        publish(true)
      }
      try {
        const response = await api.files(0, PAGE_SIZE, { q: query, cursor, signal })
        if (signal.aborted) return
        if (next) {
          page = {
            ...page,
            items: [...page.items, ...response.items],
            next_cursor: response.next_cursor,
          }
        } else {
          page = refreshFirstPage(page, response)
          firstPageFailed = false
          if (!page.next_cursor) nextPageFailed = false
        }
      } catch {
        if (signal.aborted) return
        if (next) nextPageFailed = true
        else firstPageFailed = true
      } finally {
        inFlight = null
        if (!signal.aborted) {
          publish()
          if (nextRequested) {
            nextRequested = false
            if (!nextPageFailed) void load(true)
          }
        }
      }
    }

    loadNextRef.current = () => load(true)
    void load()
    const timer = setInterval(load, 5000)
    return () => {
      clearInterval(timer)
      controller.abort()
      loadNextRef.current = null
    }
  }, [query, revision])

  const loadMore = useCallback(() => loadNextRef.current?.(), [])
  const reload = useCallback(() => setRevision((value) => value + 1), [])
  const current = result?.query === query && result.revision === revision ? result : null
  return {
    ...current,
    total: result?.page?.unfiltered_total ?? 0,
    loaded: Boolean(current?.page || current?.firstPageFailed),
    loadMore,
    reload,
  }
}

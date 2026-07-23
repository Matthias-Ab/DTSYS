import { useEffect, useState } from 'react'
import { useAuthStore } from '../store/authStore'
import type { ActivityEvent } from '../types'

export function useActivityStream(enabled = true) {
  const [events, setEvents] = useState<ActivityEvent[]>([])

  useEffect(() => {
    if (!enabled) {
      return undefined
    }

    const token = useAuthStore.getState().accessToken
    if (!token) {
      return undefined
    }

    const controller = new AbortController()
    let cancelled = false

    const consume = async () => {
      while (!cancelled) {
        try {
          const response = await fetch('/api/v1/events/activity-stream', {
            headers: { Authorization: `Bearer ${token}` },
            signal: controller.signal,
          })
          if (!response.ok || !response.body) {
            throw new Error(`SSE request failed with ${response.status}`)
          }

          const reader = response.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''

          while (!cancelled) {
            const { done, value } = await reader.read()
            if (done) {
              break
            }

            buffer += decoder.decode(value, { stream: true })
            const chunks = buffer.split('\n\n')
            buffer = chunks.pop() ?? ''

            for (const chunk of chunks) {
              const line = chunk
                .split('\n')
                .find((entry) => entry.startsWith('data: '))
              if (!line) {
                continue
              }

              try {
                const payload = JSON.parse(line.slice(6)) as ActivityEvent
                setEvents((current) => [...current, payload].slice(-20))
              } catch {
                // Ignore malformed events and continue streaming.
              }
            }
          }
        } catch (error) {
          if (cancelled || controller.signal.aborted) {
            break
          }
          await new Promise((resolve) => window.setTimeout(resolve, 3000))
          if (error instanceof Error && error.name === 'AbortError') {
            break
          }
        }
      }
    }

    void consume()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [enabled])

  return events
}

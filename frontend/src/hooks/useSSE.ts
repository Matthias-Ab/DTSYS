import { useEffect, useRef } from 'react'
import { useAlertStore } from '../store/alertStore'
import { useAuthStore } from '../store/authStore'
import type { Alert } from '../types'

let sseActive = false
let sseAbortController: AbortController | null = null

export function useSSE(enabled = true) {
  const unresolved = useAlertStore((state) => state.unresolved)
  const setUnresolved = useAlertStore((state) => state.setUnresolved)
  const mountedRef = useRef(true)
  const prevAlertsRef = useRef<Alert[]>(unresolved)

  useEffect(() => {
    prevAlertsRef.current = unresolved
  }, [unresolved])

  useEffect(() => {
    mountedRef.current = true
    if (!enabled) {
      return undefined
    }

    const token = useAuthStore.getState().accessToken
    if (!token) {
      return undefined
    }

    if (sseActive) {
      return () => {
        mountedRef.current = false
      }
    }

    sseActive = true
    const controller = new AbortController()
    sseAbortController = controller

    let backoffMs = 5000

    const scheduleReconnect = (delayMs: number) => {
      if (!mountedRef.current || controller.signal.aborted) {
        return
      }
      window.setTimeout(() => {
        if (mountedRef.current) {
          void connect()
        }
      }, delayMs)
    }

    async function connect() {
      if (!mountedRef.current || controller.signal.aborted) {
        return
      }

      try {
        const response = await fetch('/api/v1/events/stream', {
          headers: {
            Authorization: `Bearer ${token}`,
          },
          signal: controller.signal,
        })

        if (response.status === 429) {
          scheduleReconnect(60_000)
          return
        }

        if (!response.body) {
          scheduleReconnect(backoffMs)
          backoffMs = Math.min(backoffMs * 2, 60_000)
          return
        }

        backoffMs = 5000
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (mountedRef.current) {
          const { value, done } = await reader.read()
          if (done) {
            break
          }

          buffer += decoder.decode(value, { stream: true })
          const chunks = buffer.split('\n\n')
          buffer = chunks.pop() ?? ''

          for (const chunk of chunks) {
            const dataLine = chunk
              .split('\n')
              .find((line) => line.startsWith('data: '))

            if (!dataLine) {
              continue
            }

            const alert = JSON.parse(dataLine.slice(6)) as Alert
            const current = prevAlertsRef.current
            if (!current.some((existing) => existing.id === alert.id)) {
              const next = [alert, ...current]
              const changed =
                next.length !== current.length ||
                next.some((item, index) => item.id !== current[index]?.id)
              if (changed) {
                prevAlertsRef.current = next
                setUnresolved(next)
              }
            }

            if (typeof Notification === 'undefined') {
              continue
            }

            if (Notification.permission === 'granted') {
              new Notification(`${alert.severity.toUpperCase()}: ${alert.alert_type.replace(/_/g, ' ')}`, {
                body: alert.message,
              })
            } else if (Notification.permission === 'default') {
              void Notification.requestPermission()
            }
          }
        }
      } catch {
        if (!mountedRef.current || controller.signal.aborted) {
          return
        }
      }

      scheduleReconnect(backoffMs)
      backoffMs = Math.min(backoffMs * 2, 60_000)
    }

    void connect()
    return () => {
      mountedRef.current = false
      controller.abort()
      if (sseAbortController === controller) {
        sseAbortController = null
        sseActive = false
      }
    }
  }, [enabled, setUnresolved])
}

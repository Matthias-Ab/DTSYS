import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ClipboardList, Download, RefreshCw, Wifi } from 'lucide-react'
import api from '../api/client'

interface AuditEntry {
  id: string
  timestamp: string | null
  username: string
  action: string
  resource_type: string | null
  resource_id: string | null
  ip_address: string | null
  details: Record<string, unknown> | null
}

interface AuditResponse {
  total: number
  offset: number
  limit: number
  items: AuditEntry[]
}

const ACTION_COLOR: Record<string, string> = {
  login_success: 'text-green-600 bg-green-50 dark:bg-green-950/30',
  login_failed: 'text-red-500 bg-red-50 dark:bg-red-950/30',
  command_dispatched: 'text-blue-600 bg-blue-50 dark:bg-blue-950/30',
  device_deleted: 'text-red-500 bg-red-50 dark:bg-red-950/30',
  device_revoked: 'text-orange-500 bg-orange-50 dark:bg-orange-950/30',
  org_created: 'text-purple-600 bg-purple-50 dark:bg-purple-950/30',
  org_deleted: 'text-red-500 bg-red-50 dark:bg-red-950/30',
}

function actionClass(action: string): string {
  return ACTION_COLOR[action] ?? 'text-slate-600 bg-slate-100 dark:bg-gray-800 dark:text-gray-300'
}

export default function AuditLog() {
  const [filters, setFilters] = useState({
    action: '',
    username: '',
    resource_type: '',
    since: '',
    until: '',
  })
  const [page, setPage] = useState(0)
  const [liveEntries, setLiveEntries] = useState<AuditEntry[]>([])
  const [liveMode, setLiveMode] = useState(false)
  const sseRef = useRef<EventSource | null>(null)
  const limit = 50

  const { data, isLoading, refetch } = useQuery<AuditResponse>({
    queryKey: ['audit-log', filters, page],
    queryFn: () => {
      const params: Record<string, string | number> = { limit, offset: page * limit }
      if (filters.action) params.action = filters.action
      if (filters.username) params.username = filters.username
      if (filters.resource_type) params.resource_type = filters.resource_type
      if (filters.since) params.since = filters.since
      if (filters.until) params.until = filters.until
      return api.get('/audit', { params }).then((r) => r.data)
    },
  })

  const startLive = useCallback(async () => {
    if (sseRef.current) sseRef.current.close()
    // EventSource can't send an Authorization header, so we first mint a
    // single-use, 30-second ticket over a normal authenticated request and
    // pass only that (not the real access token) in the stream URL.
    const { data } = await api.post<{ ticket: string }>('/audit/stream/ticket')
    const sse = new EventSource(`/api/v1/audit/stream?ticket=${data.ticket}`)
    sse.onmessage = (e) => {
      try {
        const entry: AuditEntry = JSON.parse(e.data)
        setLiveEntries((prev) => [entry, ...prev].slice(0, 200))
      } catch { /* ignore */ }
    }
    sseRef.current = sse
    setLiveMode(true)
    setLiveEntries([])
  }, [])

  const stopLive = useCallback(() => {
    sseRef.current?.close()
    sseRef.current = null
    setLiveMode(false)
  }, [])

  useEffect(() => () => { sseRef.current?.close() }, [])

  const handleExport = async () => {
    // A bare navigation can't carry the Authorization header this API needs,
    // so fetch the CSV as a blob through the authenticated client instead.
    const params: Record<string, string> = {}
    if (filters.action) params.action = filters.action
    if (filters.since) params.since = filters.since
    if (filters.until) params.until = filters.until
    const response = await api.get('/audit/export/csv', { params, responseType: 'blob' })
    const url = URL.createObjectURL(response.data as Blob)
    const link = document.createElement('a')
    link.href = url
    link.download = 'audit-log.csv'
    link.click()
    URL.revokeObjectURL(url)
  }

  const items = liveMode ? liveEntries : (data?.items ?? [])
  const total = liveMode ? liveEntries.length : (data?.total ?? 0)

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-slate-200 px-6 py-4 dark:border-gray-800">
        <ClipboardList size={20} className="text-blue-500" />
        <h1 className="text-lg font-semibold text-slate-900 dark:text-gray-100">Audit Log</h1>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleExport}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            <Download size={14} />
            Export CSV
          </button>
          {liveMode ? (
            <button
              onClick={stopLive}
              className="flex items-center gap-1.5 rounded-lg bg-red-500 px-3 py-1.5 text-sm text-white hover:bg-red-600"
            >
              <Wifi size={14} />
              Stop Live
            </button>
          ) : (
            <button
              onClick={startLive}
              className="flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-sm text-white hover:bg-green-700"
            >
              <Wifi size={14} />
              Live
            </button>
          )}
          <button
            onClick={() => refetch()}
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 dark:border-gray-700 dark:text-gray-400 dark:hover:bg-gray-800"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-200 px-6 py-3 dark:border-gray-800">
        {(['action', 'username', 'resource_type'] as const).map((field) => (
          <input
            key={field}
            value={filters[field]}
            onChange={(e) => { setPage(0); setFilters((f) => ({ ...f, [field]: e.target.value })) }}
            placeholder={field.replace('_', ' ')}
            className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
        ))}
        <input
          type="datetime-local"
          value={filters.since}
          onChange={(e) => { setPage(0); setFilters((f) => ({ ...f, since: e.target.value })) }}
          className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          title="Since"
        />
        <input
          type="datetime-local"
          value={filters.until}
          onChange={(e) => { setPage(0); setFilters((f) => ({ ...f, until: e.target.value })) }}
          className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          title="Until"
        />
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 dark:bg-gray-900">
            <tr className="border-b border-slate-200 dark:border-gray-800">
              <th className="px-4 py-2 text-left font-medium text-slate-500 dark:text-gray-400 whitespace-nowrap">Time</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500 dark:text-gray-400">User</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500 dark:text-gray-400">Action</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500 dark:text-gray-400">Resource</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500 dark:text-gray-400">IP</th>
              <th className="px-4 py-2 text-left font-medium text-slate-500 dark:text-gray-400">Details</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && !liveMode && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">Loading...</td>
              </tr>
            )}
            {!isLoading && items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">No audit log entries found.</td>
              </tr>
            )}
            {items.map((entry) => (
              <tr
                key={entry.id}
                className="border-b border-slate-100 hover:bg-slate-50 dark:border-gray-800 dark:hover:bg-gray-800/40"
              >
                <td className="px-4 py-2 text-xs text-slate-500 dark:text-gray-400 whitespace-nowrap font-mono">
                  {entry.timestamp ? new Date(entry.timestamp).toLocaleString() : '—'}
                </td>
                <td className="px-4 py-2 text-slate-700 dark:text-gray-200 font-medium">{entry.username}</td>
                <td className="px-4 py-2">
                  <span className={`rounded px-2 py-0.5 text-xs font-medium ${actionClass(entry.action)}`}>
                    {entry.action}
                  </span>
                </td>
                <td className="px-4 py-2 text-slate-500 dark:text-gray-400 text-xs">
                  {entry.resource_type && (
                    <span>{entry.resource_type}{entry.resource_id ? ` / ${entry.resource_id.slice(0, 8)}…` : ''}</span>
                  )}
                </td>
                <td className="px-4 py-2 text-xs font-mono text-slate-400 dark:text-gray-500">{entry.ip_address ?? '—'}</td>
                <td className="px-4 py-2 text-xs text-slate-400 dark:text-gray-500 max-w-xs truncate">
                  {entry.details ? JSON.stringify(entry.details) : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination (non-live mode) */}
      {!liveMode && (
        <div className="flex items-center justify-between border-t border-slate-200 px-6 py-3 dark:border-gray-800">
          <span className="text-sm text-slate-500 dark:text-gray-400">
            {total} total entries
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm disabled:opacity-40 dark:border-gray-700"
            >
              Previous
            </button>
            <span className="text-sm text-slate-500 dark:text-gray-400">Page {page + 1}</span>
            <button
              onClick={() => setPage((p) => p + 1)}
              disabled={(page + 1) * limit >= total}
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm disabled:opacity-40 dark:border-gray-700"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

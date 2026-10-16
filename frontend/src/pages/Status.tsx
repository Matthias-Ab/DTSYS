import { useQuery } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'

interface StatusPayload {
  status: 'operational' | 'degraded' | 'down'
  total_devices: number
  online_devices: number
  offline_devices: number
  active_critical_alerts: number
  last_updated: string
}

export default function Status() {
  const { data } = useQuery({
    queryKey: ['public-status'],
    queryFn: () =>
      fetch('/api/v1/status', { headers: { Accept: 'application/json' } }).then((response) => response.json() as Promise<StatusPayload>),
    refetchInterval: 30_000,
  })

  const status = data?.status ?? 'operational'
  const title =
    status === 'operational'
      ? 'All Systems Operational'
      : status === 'degraded'
        ? 'Degraded'
        : 'Outage Detected'
  const color =
    status === 'operational'
      ? 'text-emerald-600'
      : status === 'degraded'
        ? 'text-amber-500'
        : 'text-red-500'

  return (
    <div className="min-h-screen bg-slate-100 px-4 py-12 text-slate-900 dark:bg-gray-950 dark:text-gray-100">
      <div className="mx-auto max-w-3xl rounded-3xl border border-slate-200 bg-white p-8 shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <div className={`text-center text-4xl font-bold ${color}`}>{title}</div>
        <p className="mt-3 text-center text-sm text-slate-500 dark:text-gray-500">
          {data ? `${data.online_devices}/${data.total_devices} devices online` : 'Loading status...'}
        </p>
        <div className="mt-8 grid gap-4 sm:grid-cols-4">
          <StatusCard label="Total Devices" value={String(data?.total_devices ?? 0)} />
          <StatusCard label="Online" value={String(data?.online_devices ?? 0)} />
          <StatusCard label="Offline" value={String(data?.offline_devices ?? 0)} />
          <StatusCard label="Critical Alerts" value={String(data?.active_critical_alerts ?? 0)} />
        </div>
        <p className="mt-6 text-center text-xs text-slate-400 dark:text-gray-600">
          Last updated {data?.last_updated ? formatDistanceToNow(new Date(data.last_updated), { addSuffix: true }) : 'just now'}
        </p>
      </div>
    </div>
  )
}

function StatusCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-center dark:border-gray-800 dark:bg-gray-950/40">
      <div className="text-xs uppercase tracking-wide text-slate-400 dark:text-gray-600">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-slate-900 dark:text-gray-100">{value}</div>
    </div>
  )
}

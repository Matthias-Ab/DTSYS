import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Plus, RefreshCw, RotateCcw, Upload, X } from 'lucide-react'
import { format } from 'date-fns'
import api from '../api/client'
import { adminApi } from '../api/admin'
import { devicesApi } from '../api/devices'
import { notificationRulesApi } from '../api/notificationRules'
import Pagination from '../components/ui/Pagination'
import { useAuthStore } from '../store/authStore'
import { useBrandingStore } from '../store/brandingStore'
import { exportToCSV } from '../utils/export'
import MfaCard from '../components/MfaCard'

export default function Settings() {
  const { role } = useAuthStore()
  const branding = useBrandingStore()
  const [brandingDraft, setBrandingDraft] = useState({
    companyName: branding.companyName,
    sidebarLabel: branding.sidebarLabel,
    accentColor: branding.accentColor,
  })
  const [brandingSaved, setBrandingSaved] = useState(false)
  const logoInputRef = useRef<HTMLInputElement>(null)
  const faviconInputRef = useRef<HTMLInputElement>(null)

  const handleLogoUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => branding.setLogoUrl(reader.result as string)
    reader.readAsDataURL(file)
  }

  const handleFaviconUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = reader.result as string
      branding.setFaviconUrl(dataUrl)
      // Apply favicon live
      const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']") ?? document.createElement('link')
      link.rel = 'icon'
      link.href = dataUrl
      document.head.appendChild(link)
    }
    reader.readAsDataURL(file)
  }

  const saveBranding = () => {
    branding.setCompanyName(brandingDraft.companyName || 'DTSYS')
    branding.setSidebarLabel(brandingDraft.sidebarLabel)
    branding.setAccentColor(brandingDraft.accentColor)
    setBrandingSaved(true)
    setTimeout(() => setBrandingSaved(false), 2000)
  }

  const resetBranding = () => {
    branding.reset()
    setBrandingDraft({ companyName: 'DTSYS', sidebarLabel: 'IT Management', accentColor: '#3b82f6' })
  }

  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'viewer' })
  const [newRule, setNewRule] = useState({
    alert_type: '*',
    severity_min: 'warning',
    channel: 'browser' as 'browser' | 'webhook' | 'email',
    webhook_url: '',
    email_address: '',
  })
  const [token, setToken] = useState<string | null>(null)
  const [auditAction, setAuditAction] = useState('')
  const [auditPage, setAuditPage] = useState(1)
  const [msg, setMsg] = useState('')
  const queryClient = useQueryClient()

  const { data: rules = [] } = useQuery({
    queryKey: ['notification-rules'],
    queryFn: notificationRulesApi.list,
    enabled: role === 'admin',
  })
  const auditPageSize = 25
  const { data: auditResponse } = useQuery({
    queryKey: ['audit-log', auditAction, auditPage],
    queryFn: () => adminApi.auditLogPage({ action: auditAction || undefined, limit: auditPageSize, skip: (auditPage - 1) * auditPageSize }),
    enabled: role === 'admin',
    refetchInterval: 30_000,
  })
  const auditLog = auditResponse?.data ?? []
  const auditTotal = auditResponse?.total ?? 0
  const { data: authConfig } = useQuery({
    queryKey: ['auth-config'],
    queryFn: adminApi.authConfig,
    enabled: role === 'admin',
  })
  const { data: connections } = useQuery({
    queryKey: ['admin-connections'],
    queryFn: adminApi.connections,
    enabled: role === 'admin',
    refetchInterval: 10_000,
  })
  const { data: storageStats } = useQuery({
    queryKey: ['storage-stats'],
    queryFn: adminApi.storageStats,
    enabled: role === 'admin',
  })

  const createRule = useMutation({
    mutationFn: () =>
      notificationRulesApi.create({
        ...newRule,
        webhook_url: newRule.channel === 'webhook' ? newRule.webhook_url : null,
        email_address: newRule.channel === 'email' ? newRule.email_address : null,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-rules'] })
      setNewRule({ alert_type: '*', severity_min: 'warning', channel: 'browser', webhook_url: '', email_address: '' })
    },
  })

  const updateRule = useMutation({
    mutationFn: ({ id, is_enabled }: { id: string; is_enabled: boolean }) =>
      notificationRulesApi.update(id, { is_enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-rules'] })
    },
  })
  const disconnectDevice = useMutation({
    mutationFn: (deviceId: string) => devicesApi.disconnect(deviceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-connections'] })
    },
  })
  const cleanupNow = useMutation({
    mutationFn: adminApi.cleanup,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['storage-stats'] })
    },
  })

  const createUser = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await api.post('/admin/users', newUser)
      setMsg('User created successfully')
      setNewUser({ username: '', password: '', role: 'viewer' })
    } catch {
      setMsg('Failed to create user')
    }
  }

  const generateToken = async () => {
    const response = await api.post('/admin/enrollment-tokens')
    setToken(response.data.enrollment_token)
  }

  if (role !== 'admin') {
    return (
      <div className="p-4 text-sm text-slate-500 dark:text-gray-500">
        Admin access required to view settings.
      </div>
    )
  }

  return (
    <div className="max-w-4xl space-y-8">
      <div>
        <h1 className="text-xl font-bold text-slate-900 dark:text-gray-100">Settings</h1>
        <p className="mt-0.5 text-sm text-slate-500 dark:text-gray-500">Administration</p>
      </div>

      {/* ── Branding ── */}
      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900 space-y-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900 dark:text-gray-200">Company Branding</h2>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-gray-500">
              Customize the logo, name, and accent colour shown in the sidebar and topbar.
              Settings are saved in your browser.
            </p>
          </div>
          <button
            onClick={resetBranding}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-50 dark:border-gray-700 dark:text-gray-400 dark:hover:bg-gray-800"
            title="Reset to defaults"
          >
            <RotateCcw size={12} />
            Reset
          </button>
        </div>

        {/* Live preview */}
        <div className="flex items-center gap-3 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-800/40">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 bg-white dark:border-gray-700 dark:bg-gray-900">
            {branding.logoUrl ? (
              <img src={branding.logoUrl} alt="logo" className="h-7 w-7 rounded object-contain" />
            ) : (
              <span
                className="text-lg font-bold"
                style={{ color: brandingDraft.accentColor }}
              >
                {(brandingDraft.companyName || 'D')[0].toUpperCase()}
              </span>
            )}
          </div>
          <div>
            <div className="text-sm font-bold text-slate-900 dark:text-gray-100">
              {brandingDraft.companyName || 'DTSYS'}
            </div>
            <div className="text-[10px] text-slate-400 dark:text-gray-500">
              {brandingDraft.sidebarLabel || 'IT Management'}
            </div>
          </div>
          <div
            className="ml-auto h-4 w-16 rounded-full"
            style={{ backgroundColor: brandingDraft.accentColor }}
            title="Accent colour"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          {/* Company name */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-gray-400">Company Name</label>
            <input
              value={brandingDraft.companyName}
              onChange={(e) => setBrandingDraft((d) => ({ ...d, companyName: e.target.value }))}
              placeholder="DTSYS"
              maxLength={40}
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            />
          </div>

          {/* Sidebar label */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-gray-400">Sidebar Subtitle</label>
            <input
              value={brandingDraft.sidebarLabel}
              onChange={(e) => setBrandingDraft((d) => ({ ...d, sidebarLabel: e.target.value }))}
              placeholder="IT Management"
              maxLength={40}
              className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            />
          </div>

          {/* Accent colour */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-gray-400">Accent Colour</label>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={brandingDraft.accentColor}
                onChange={(e) => setBrandingDraft((d) => ({ ...d, accentColor: e.target.value }))}
                className="h-9 w-14 cursor-pointer rounded-lg border border-slate-200 bg-transparent p-0.5 dark:border-gray-700"
              />
              <input
                value={brandingDraft.accentColor}
                onChange={(e) => setBrandingDraft((d) => ({ ...d, accentColor: e.target.value }))}
                placeholder="#3b82f6"
                maxLength={7}
                className="flex-1 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
              />
            </div>
          </div>

          {/* Logo upload */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-gray-400">Logo (replaces icon)</label>
            <div className="flex items-center gap-2">
              <button
                onClick={() => logoInputRef.current?.click()}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                <Upload size={13} />
                {branding.logoUrl ? 'Replace' : 'Upload'} logo
              </button>
              {branding.logoUrl && (
                <>
                  <img src={branding.logoUrl} alt="logo preview" className="h-8 w-8 rounded border border-slate-200 object-contain dark:border-gray-700" />
                  <button
                    onClick={() => branding.setLogoUrl('')}
                    className="rounded p-1 text-slate-400 hover:text-red-500"
                    title="Remove logo"
                  >
                    <X size={14} />
                  </button>
                </>
              )}
              <input
                ref={logoInputRef}
                type="file"
                accept="image/png,image/jpeg,image/svg+xml,image/webp"
                className="hidden"
                onChange={handleLogoUpload}
              />
            </div>
            <p className="mt-1 text-xs text-slate-400 dark:text-gray-600">PNG, JPG, SVG or WebP. Square recommended. Max display size 28×28px.</p>
          </div>

          {/* Favicon upload */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-600 dark:text-gray-400">Favicon (browser tab icon)</label>
            <div className="flex items-center gap-2">
              <button
                onClick={() => faviconInputRef.current?.click()}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
              >
                <Upload size={13} />
                {branding.faviconUrl ? 'Replace' : 'Upload'} favicon
              </button>
              {branding.faviconUrl && (
                <>
                  <img src={branding.faviconUrl} alt="favicon preview" className="h-6 w-6 rounded border border-slate-200 object-contain dark:border-gray-700" />
                  <button
                    onClick={() => branding.setFaviconUrl('')}
                    className="rounded p-1 text-slate-400 hover:text-red-500"
                    title="Remove favicon"
                  >
                    <X size={14} />
                  </button>
                </>
              )}
              <input
                ref={faviconInputRef}
                type="file"
                accept="image/png,image/x-icon,image/svg+xml"
                className="hidden"
                onChange={handleFaviconUpload}
              />
            </div>
            <p className="mt-1 text-xs text-slate-400 dark:text-gray-600">PNG, ICO or SVG. 32×32px recommended.</p>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={saveBranding}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-500 transition-colors"
          >
            {brandingSaved ? 'Saved!' : 'Save Branding'}
          </button>
          <p className="text-xs text-slate-400 dark:text-gray-600">
            Branding is stored locally in your browser — no server changes required.
          </p>
        </div>
      </section>

      <MfaCard />

      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
        <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Enrollment Token</h2>
        <p className="mb-4 text-xs text-slate-500 dark:text-gray-500">
          Generate a one-time token to register a new device agent.
        </p>
        {token ? (
          <div className="flex items-center gap-2">
            <code className="flex-1 rounded-lg bg-slate-100 px-3 py-2 font-mono text-sm text-green-600 dark:bg-gray-800 dark:text-green-400">
              {token}
            </code>
            <button
              onClick={() => navigator.clipboard.writeText(token)}
              className="text-slate-500 transition-colors hover:text-slate-900 dark:text-gray-400 dark:hover:text-gray-100"
            >
              <Copy size={15} />
            </button>
          </div>
        ) : (
          <button
            onClick={generateToken}
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-500"
          >
            <RefreshCw size={14} />
            Generate Token
          </button>
        )}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
        <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Create User</h2>
        <p className="mb-4 text-xs text-slate-500 dark:text-gray-500">Add a new dashboard user account.</p>

        {msg && (
          <div className="mb-3 rounded px-3 py-2 text-xs text-blue-400 bg-blue-400/10">{msg}</div>
        )}

        <form onSubmit={createUser} className="space-y-3">
          <input
            value={newUser.username}
            onChange={(event) => setNewUser((current) => ({ ...current, username: event.target.value }))}
            placeholder="Username"
            required
            className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
          <input
            type="password"
            value={newUser.password}
            onChange={(event) => setNewUser((current) => ({ ...current, password: event.target.value }))}
            placeholder="Password"
            required
            className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
          <select
            value={newUser.role}
            onChange={(event) => setNewUser((current) => ({ ...current, role: event.target.value }))}
            className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="viewer">Viewer</option>
            <option value="admin">Admin</option>
          </select>
          <button
            type="submit"
            className="flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-500"
          >
            <Plus size={14} />
            Create User
          </button>
        </form>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
        <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Notification Rules</h2>
        <p className="mb-4 text-xs text-slate-500 dark:text-gray-500">
          Control browser, webhook, and email notifications for new alerts.
        </p>

        <div className="mb-5 overflow-x-auto rounded-lg border border-slate-200 dark:border-gray-800">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-500 dark:bg-gray-950/60 dark:text-gray-400">
              <tr>
                <th className="px-3 py-2">Alert</th>
                <th className="px-3 py-2">Severity</th>
                <th className="px-3 py-2">Channel</th>
                <th className="px-3 py-2">Target</th>
                <th className="px-3 py-2">Enabled</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id} className="border-t border-slate-200 dark:border-gray-800">
                  <td className="px-3 py-2 text-slate-900 dark:text-gray-100">{rule.alert_type}</td>
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">{rule.severity_min}</td>
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">{rule.channel}</td>
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">{rule.webhook_url ?? rule.email_address ?? 'Browser session'}</td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => updateRule.mutate({ id: rule.id, is_enabled: !rule.is_enabled })}
                      className={`rounded-full px-2 py-1 text-xs ${
                        rule.is_enabled
                          ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300'
                          : 'bg-slate-200 text-slate-600 dark:bg-gray-800 dark:text-gray-400'
                      }`}
                    >
                      {rule.is_enabled ? 'Enabled' : 'Disabled'}
                    </button>
                  </td>
                </tr>
              ))}
              {rules.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-4 text-center text-slate-500 dark:text-gray-500">
                    No notification rules configured.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <input
            value={newRule.alert_type}
            onChange={(event) => setNewRule((current) => ({ ...current, alert_type: event.target.value }))}
            placeholder="Alert type or *"
            className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
          <select
            value={newRule.severity_min}
            onChange={(event) => setNewRule((current) => ({ ...current, severity_min: event.target.value }))}
            className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="info">Info+</option>
            <option value="warning">Warning+</option>
            <option value="critical">Critical only</option>
          </select>
          <select
            value={newRule.channel}
            onChange={(event) => setNewRule((current) => ({ ...current, channel: event.target.value as 'browser' | 'webhook' | 'email' }))}
            className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="browser">Browser</option>
            <option value="webhook">Webhook</option>
            <option value="email">Email</option>
          </select>
          <input
            value={newRule.channel === 'email' ? newRule.email_address : newRule.webhook_url}
            onChange={(event) =>
              setNewRule((current) => ({
                ...current,
                webhook_url: current.channel === 'webhook' ? event.target.value : current.webhook_url,
                email_address: current.channel === 'email' ? event.target.value : current.email_address,
              }))
            }
            placeholder={newRule.channel === 'email' ? 'Email address' : 'Webhook URL'}
            disabled={newRule.channel === 'browser'}
            className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 disabled:opacity-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          />
        </div>
        <button
          onClick={() => createRule.mutate()}
          className="mt-3 rounded-lg bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-500"
        >
          Add Rule
        </button>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Storage</h2>
            <p className="text-xs text-slate-500 dark:text-gray-500">Current row counts and retention policy.</p>
          </div>
          <button
            onClick={() => cleanupNow.mutate()}
            disabled={cleanupNow.isPending}
            className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white transition-colors hover:bg-blue-500 disabled:opacity-50"
          >
            Run Cleanup Now
          </button>
        </div>
        <div className="grid gap-3 md:grid-cols-3">
          <StorageStat label="Devices" value={storageStats?.devices ?? 0} />
          <StorageStat label="Metrics Rows" value={storageStats?.metrics_rows ?? 0} />
          <StorageStat label="Events Rows" value={storageStats?.events_rows ?? 0} />
          <StorageStat label="Commands Rows" value={storageStats?.commands_rows ?? 0} />
          <StorageStat label="Alerts Rows" value={storageStats?.alerts_rows ?? 0} />
          <StorageStat label="Disk Estimate" value={`${storageStats?.disk_estimate_mb ?? 0} MB`} />
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-4">
          <StorageStat label="Metric Retention" value={`${storageStats?.retention_days.metrics ?? 90} days`} subtle />
          <StorageStat label="Event Retention" value={`${storageStats?.retention_days.events ?? 365} days`} subtle />
          <StorageStat label="Command Retention" value={`${storageStats?.retention_days.commands ?? 180} days`} subtle />
          <StorageStat label="Alert Retention" value={`${storageStats?.retention_days.alerts ?? 365} days`} subtle />
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
        <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Authentication</h2>
        <p className="mb-4 text-xs text-slate-500 dark:text-gray-500">
          Authentication mode is configured by environment variables on the server.
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-gray-800 dark:bg-gray-950/40">
            <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-gray-600">Mode</p>
            <p className="mt-1 text-sm text-slate-900 dark:text-gray-100">{authConfig?.mode ?? 'Local'}</p>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-gray-800 dark:bg-gray-950/40">
            <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-gray-600">LDAP Server</p>
            <p className="mt-1 text-sm text-slate-900 dark:text-gray-100">
              {authConfig?.ldap_enabled ? `${authConfig.ldap_server}:${authConfig.ldap_port}` : 'Disabled'}
            </p>
          </div>
          {authConfig?.ldap_enabled && (
            <>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-gray-800 dark:bg-gray-950/40">
                <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-gray-600">Base DN</p>
                <p className="mt-1 text-sm text-slate-900 dark:text-gray-100">{authConfig.ldap_base_dn || '—'}</p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 dark:border-gray-800 dark:bg-gray-950/40">
                <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-gray-600">Admin Group DN</p>
                <p className="mt-1 text-sm text-slate-900 dark:text-gray-100">{authConfig.ldap_admin_group_dn || '—'}</p>
              </div>
            </>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Live Connections</h2>
            <p className="text-xs text-slate-500 dark:text-gray-500">Currently connected agent WebSocket sessions.</p>
          </div>
          <span className="rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-700 dark:bg-blue-500/15 dark:text-blue-300">
            {connections?.total ?? 0} connected
          </span>
        </div>
        <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-gray-800">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-500 dark:bg-gray-950/60 dark:text-gray-400">
              <tr>
                <th className="px-3 py-2">Hostname</th>
                <th className="px-3 py-2">IP</th>
                <th className="px-3 py-2">Connected Since</th>
                <th className="px-3 py-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {(connections?.devices ?? []).map((connection) => (
                <tr key={connection.device_id} className="border-t border-slate-200 dark:border-gray-800">
                  <td className="px-3 py-2 text-slate-900 dark:text-gray-100">{connection.hostname}</td>
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">{connection.ip ?? '—'}</td>
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">
                    {format(new Date(connection.connected_since), 'PPpp')}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => disconnectDevice.mutate(connection.device_id)}
                      className="rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-600 dark:border-red-500/30 dark:text-red-300"
                    >
                      Disconnect
                    </button>
                  </td>
                </tr>
              ))}
              {(connections?.devices ?? []).length === 0 && (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-slate-500 dark:text-gray-500">
                    No live agent connections.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-5 dark:border-gray-800 dark:bg-gray-900">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="mb-1 text-sm font-semibold text-slate-900 dark:text-gray-200">Audit Log</h2>
            <p className="text-xs text-slate-500 dark:text-gray-500">Recent administrative and authentication activity.</p>
          </div>
          <select
            value={auditAction}
            onChange={(event) => setAuditAction(event.target.value)}
            className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-900 outline-none focus:border-blue-500 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
          >
            <option value="">All actions</option>
            <option value="login_success">login_success</option>
            <option value="login_failed">login_failed</option>
            <option value="user_created">user_created</option>
            <option value="device_revoked">device_revoked</option>
            <option value="command_dispatched">command_dispatched</option>
            <option value="scheduled_command_created">scheduled_command_created</option>
            <option value="scheduled_command_deleted">scheduled_command_deleted</option>
          </select>
          <button
            onClick={() =>
              exportToCSV(
                'audit-log.csv',
                ['Timestamp', 'User', 'Action', 'Resource', 'Details'],
                auditLog.map((entry) => [
                  entry.timestamp ?? '',
                  entry.username,
                  entry.action,
                  entry.resource_type ? `${entry.resource_type}:${entry.resource_id ?? ''}` : '',
                  entry.details ? JSON.stringify(entry.details) : '',
                ])
              )
            }
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 dark:border-gray-700 dark:text-gray-300"
          >
            Export CSV
          </button>
        </div>
        <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-gray-800">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-slate-500 dark:bg-gray-950/60 dark:text-gray-400">
              <tr>
                <th className="px-3 py-2">Timestamp</th>
                <th className="px-3 py-2">User</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Resource</th>
                <th className="px-3 py-2">Details</th>
              </tr>
            </thead>
            <tbody>
              {auditLog.map((entry) => (
                <tr key={entry.id} className="border-t border-slate-200 dark:border-gray-800">
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">
                    {entry.timestamp ? format(new Date(entry.timestamp), 'PPpp') : '—'}
                  </td>
                  <td className="px-3 py-2 text-slate-900 dark:text-gray-100">{entry.username}</td>
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">{entry.action}</td>
                  <td className="px-3 py-2 text-slate-600 dark:text-gray-300">
                    {entry.resource_type ? `${entry.resource_type}:${entry.resource_id ?? '—'}` : '—'}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-500 dark:text-gray-500">
                    {entry.details ? JSON.stringify(entry.details) : '—'}
                  </td>
                </tr>
              ))}
              {auditLog.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-slate-500 dark:text-gray-500">
                    No audit entries found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="mt-4">
          <Pagination page={auditPage} totalPages={Math.max(1, Math.ceil(auditTotal / auditPageSize))} onChange={setAuditPage} />
        </div>
      </section>
    </div>
  )
}

function StorageStat({ label, value, subtle = false }: { label: string; value: string | number; subtle?: boolean }) {
  return (
    <div className={`rounded-lg border p-3 ${subtle ? 'border-slate-200 bg-slate-50 dark:border-gray-800 dark:bg-gray-950/40' : 'border-slate-200 bg-white dark:border-gray-800 dark:bg-gray-950/20'}`}>
      <p className="text-xs uppercase tracking-wide text-slate-400 dark:text-gray-600">{label}</p>
      <p className="mt-1 text-sm font-medium text-slate-900 dark:text-gray-100">{value}</p>
    </div>
  )
}

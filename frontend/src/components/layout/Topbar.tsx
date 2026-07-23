import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, Building2, ChevronDown, LogOut, Menu, Moon, Search, Sun, User } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useAlerts } from '../../hooks/useAlerts'
import { useSSE } from '../../hooks/useSSE'
import { useAlertStore } from '../../store/alertStore'
import { useAuthStore } from '../../store/authStore'
import { useThemeStore } from '../../store/themeStore'
import { useGlobalSearchStore } from '../../store/globalSearchStore'
import { useLayoutStore } from '../../store/layoutStore'
import { orgsApi } from '../../api/organizations'
import { authApi } from '../../api/auth'

export default function Topbar() {
  const { username, role, orgId, orgName, logout, setOrgContext } = useAuthStore()
  const { theme, toggle } = useThemeStore()
  const openSearch = useGlobalSearchStore((state) => state.openSearch)
  const toggleSidebar = useLayoutStore((state) => state.toggleSidebar)
  const navigate = useNavigate()
  const { data: unresolvedAlerts = [] } = useAlerts({ resolved: false })
  const unresolved = useAlertStore((state) => state.unresolved)
  const setUnresolved = useAlertStore((state) => state.setUnresolved)
  const [orgMenuOpen, setOrgMenuOpen] = useState(false)
  const orgMenuRef = useRef<HTMLDivElement>(null)

  useSSE(Boolean(username))

  const syncUnresolved = useCallback(
    (alerts: typeof unresolvedAlerts) => {
      setUnresolved(alerts)
    },
    [setUnresolved]
  )

  useEffect(() => {
    syncUnresolved(unresolvedAlerts)
  }, [syncUnresolved, unresolvedAlerts])

  const { data: orgs = [] } = useQuery({
    queryKey: ['organizations'],
    queryFn: orgsApi.list,
    enabled: Boolean(username),
  })

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (orgMenuRef.current && !orgMenuRef.current.contains(e.target as Node)) {
        setOrgMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleLogout = () => {
    authApi.logout().catch(() => {
      // best-effort server-side revocation; local session is cleared regardless
    })
    logout()
    navigate('/login')
  }

  const switchOrg = async (targetOrgId: string, targetOrgName: string) => {
    try {
      const tokens = await orgsApi.switch(targetOrgId)
      setOrgContext(targetOrgId, targetOrgName, tokens.access_token)
      setOrgMenuOpen(false)
      window.location.href = '/'
    } catch {
      // silently ignore — user stays on current org
    }
  }

  const showOrgSwitcher = orgs.length > 1

  return (
    <header className="flex h-12 items-center justify-between gap-3 border-b border-slate-200 bg-white px-6 dark:border-gray-800 dark:bg-gray-900">
      <div className="flex items-center gap-3">
        <button
          onClick={toggleSidebar}
          className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 md:hidden dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
          aria-label="Open sidebar"
        >
          <Menu size={18} />
        </button>
        <button
          onClick={openSearch}
          className="flex min-w-0 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-sm text-slate-500 transition-colors hover:border-blue-300 hover:text-slate-900 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-400 dark:hover:border-blue-700 dark:hover:text-gray-100"
        >
          <Search size={14} />
          <span className="hidden flex-1 text-left sm:block">Search devices, software</span>
          <span className="rounded border border-slate-200 px-1.5 py-0.5 text-[11px] dark:border-gray-700">Ctrl K</span>
        </button>
      </div>

      <div className="flex items-center gap-3">
        {/* Org switcher */}
        {orgName && (
          <div className="relative" ref={orgMenuRef}>
            <button
              onClick={() => showOrgSwitcher && setOrgMenuOpen((v) => !v)}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1 text-sm text-slate-700 transition-colors hover:border-blue-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-200"
              title={showOrgSwitcher ? 'Switch organization' : orgName}
            >
              <Building2 size={13} className="text-blue-500" />
              <span className="max-w-[120px] truncate">{orgName}</span>
              {showOrgSwitcher && <ChevronDown size={12} className="text-slate-400" />}
            </button>
            {orgMenuOpen && showOrgSwitcher && (
              <div className="absolute right-0 top-full z-50 mt-1 w-56 rounded-lg border border-slate-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-900">
                {orgs.map((org) => (
                  <button
                    key={org.id}
                    onClick={() => switchOrg(org.id, org.name)}
                    className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-slate-50 dark:hover:bg-gray-800 ${
                      org.id === orgId
                        ? 'font-medium text-blue-600 dark:text-blue-400'
                        : 'text-slate-700 dark:text-gray-200'
                    }`}
                  >
                    <Building2 size={13} />
                    <span className="flex-1 truncate">{org.name}</span>
                    <span className="text-xs text-slate-400 dark:text-gray-500">{org.role}</span>
                    {org.id === orgId && (
                      <span className="h-1.5 w-1.5 rounded-full bg-blue-500" />
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <button
          onClick={() => navigate('/alerts')}
          className="relative rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
          title="Open alerts"
        >
          <Bell size={16} />
          {unresolved.length > 0 && (
            <span className="absolute -right-1 -top-1 rounded-full bg-red-500 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              {unresolved.length}
            </span>
          )}
        </button>
        <button
          onClick={toggle}
          className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
          title="Toggle theme"
        >
          {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
        </button>
        <span className="hidden items-center gap-1.5 text-sm text-slate-500 sm:flex dark:text-gray-400">
          <User size={14} />
          <span className="text-slate-900 dark:text-gray-200">{username}</span>
          <span className="text-slate-400 dark:text-gray-600">({role})</span>
        </span>
        <button
          onClick={handleLogout}
          className="flex items-center gap-1.5 text-sm text-slate-500 transition-colors hover:text-slate-900 dark:text-gray-400 dark:hover:text-gray-100"
        >
          <LogOut size={14} />
          <span className="hidden sm:inline">Logout</span>
        </button>
      </div>
    </header>
  )
}

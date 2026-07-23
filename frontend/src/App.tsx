import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { authApi } from './api/auth'
import Layout from './components/layout/Layout'
import ErrorBoundary from './components/ui/ErrorBoundary'
import WhatsNew from './components/ui/WhatsNew'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import DeviceDetail from './pages/DeviceDetail'
import Alerts from './pages/Alerts'
import Reports from './pages/Reports'
import ScheduledCommands from './pages/ScheduledCommands'
import Settings from './pages/Settings'
import SoftwareUpdates from './pages/SoftwareUpdates'
import Onboarding from './pages/Onboarding'
import DeviceCompare from './pages/DeviceCompare'
import NetworkMap from './pages/NetworkMap'
import Status from './pages/Status'
import Users from './pages/Users'
import Inventory from './pages/Inventory'
import CommandLibrary from './pages/CommandLibrary'
import CustomDashboard from './pages/CustomDashboard'
import Organizations from './pages/Organizations'
import Compliance from './pages/Compliance'
import AuditLog from './pages/AuditLog'
import Downloads from './pages/Downloads'
import { devicesApi } from './api/devices'
import { useAuthStore } from './store/authStore'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 10_000,
    },
  },
})

// The access token lives only in memory, so a hard page reload starts with
// none. Before deciding whether to bounce to /login, we try one silent
// /auth/refresh — the httpOnly cookie set at login carries the real
// credential, so this succeeds transparently for anyone with a valid session.
function useAuthInit() {
  const { isAuthenticated, isInitializing, setAccessToken, finishInitializing, logout } = useAuthStore()

  useEffect(() => {
    if (isAuthenticated) {
      finishInitializing()
      return
    }
    authApi
      .refresh()
      .then((tokens) => setAccessToken(tokens.access_token))
      .catch(() => logout())
      .finally(() => finishInitializing())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return isInitializing
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuthStore()
  return isAuthenticated ? (
    <>
      <WhatsNew />
      {children}
    </>
  ) : (
    <Navigate to="/login" replace />
  )
}

function HomeRoute() {
  const { role } = useAuthStore()
  const { data: devices, isLoading } = useQuery({
    queryKey: ['devices', 'home-route'],
    queryFn: () => devicesApi.list(),
    enabled: role === 'admin',
  })

  if (role === 'admin') {
    if (isLoading) {
      return <div className="p-6 text-sm text-slate-500 dark:text-gray-500">Loading dashboard...</div>
    }
    if ((devices?.length ?? 0) === 0) {
      return <Navigate to="/onboarding" replace />
    }
  }

  return <Dashboard />
}

function withBoundary(element: React.ReactNode) {
  return <ErrorBoundary>{element}</ErrorBoundary>
}

function AppRoutes() {
  const isInitializing = useAuthInit()

  if (isInitializing) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-slate-500 dark:text-gray-500">Loading...</div>
  }

  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route path="/login" element={withBoundary(<Login />)} />
          <Route path="/status" element={withBoundary(<Status />)} />
          <Route
            path="/onboarding"
            element={
              <ProtectedRoute>
                {withBoundary(<Onboarding />)}
              </ProtectedRoute>
            }
          />
          <Route
            path="/"
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            <Route index element={withBoundary(<HomeRoute />)} />
            <Route path="my-dashboard" element={withBoundary(<CustomDashboard />)} />
            <Route path="devices/:id" element={withBoundary(<DeviceDetail />)} />
            <Route path="compare" element={withBoundary(<DeviceCompare />)} />
            <Route path="network-map" element={withBoundary(<NetworkMap />)} />
            <Route path="alerts" element={withBoundary(<Alerts />)} />
            <Route path="reports" element={withBoundary(<Reports />)} />
            <Route path="inventory" element={withBoundary(<Inventory />)} />
            <Route path="command-library" element={withBoundary(<CommandLibrary />)} />
            <Route path="software-updates" element={withBoundary(<SoftwareUpdates />)} />
            <Route path="scheduled" element={withBoundary(<ScheduledCommands />)} />
            <Route path="users" element={withBoundary(<Users />)} />
            <Route path="compliance" element={withBoundary(<Compliance />)} />
            <Route path="audit" element={withBoundary(<AuditLog />)} />
            <Route path="downloads" element={withBoundary(<Downloads />)} />
            <Route path="organizations" element={withBoundary(<Organizations />)} />
            <Route path="settings" element={withBoundary(<Settings />)} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
    </BrowserRouter>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppRoutes />
    </QueryClientProvider>
  )
}

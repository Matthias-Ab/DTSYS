import { create } from 'zustand'

// The access token lives in memory only (never localStorage) so an XSS payload
// can't read it out of storage after the fact. The refresh token never reaches
// JS at all — the server sets it as an httpOnly cookie. On page load, App.tsx
// calls /auth/refresh (cookie sent automatically) to silently mint a fresh
// access token before rendering protected routes.
interface AuthState {
  accessToken: string | null
  username: string | null
  role: string | null
  orgId: string | null
  orgName: string | null
  isAuthenticated: boolean
  isInitializing: boolean
  login: (accessToken: string, username: string, role: string, orgId?: string, orgName?: string) => void
  logout: () => void
  setOrgContext: (orgId: string, orgName: string, accessToken: string) => void
  setAccessToken: (accessToken: string) => void
  finishInitializing: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  accessToken: null,
  username: localStorage.getItem('username'),
  role: localStorage.getItem('role'),
  orgId: localStorage.getItem('org_id'),
  orgName: localStorage.getItem('org_name'),
  isAuthenticated: false,
  isInitializing: true,

  login: (accessToken, username, role, orgId, orgName) => {
    localStorage.setItem('username', username)
    localStorage.setItem('role', role)
    if (orgId) localStorage.setItem('org_id', orgId)
    if (orgName) localStorage.setItem('org_name', orgName)
    set({
      accessToken,
      username,
      role,
      orgId: orgId ?? null,
      orgName: orgName ?? null,
      isAuthenticated: true,
      isInitializing: false,
    })
  },

  logout: () => {
    localStorage.removeItem('username')
    localStorage.removeItem('role')
    localStorage.removeItem('org_id')
    localStorage.removeItem('org_name')
    set({
      accessToken: null,
      username: null,
      role: null,
      orgId: null,
      orgName: null,
      isAuthenticated: false,
      isInitializing: false,
    })
  },

  setOrgContext: (orgId, orgName, accessToken) => {
    localStorage.setItem('org_id', orgId)
    localStorage.setItem('org_name', orgName)
    set({ orgId, orgName, accessToken })
  },

  setAccessToken: (accessToken) => set({ accessToken, isAuthenticated: true }),

  finishInitializing: () => set({ isInitializing: false }),
}))

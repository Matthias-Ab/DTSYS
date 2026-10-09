import { beforeEach, describe, expect, it } from 'vitest'
import { useAuthStore } from './authStore'

describe('authStore', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.getState().logout()
  })

  it('login keeps the token in memory only', () => {
    useAuthStore.getState().login('tok', 'alice', 'admin', 'org1', 'Acme')
    const state = useAuthStore.getState()
    expect(state.isAuthenticated).toBe(true)
    expect(state.accessToken).toBe('tok')
    expect(localStorage.getItem('username')).toBe('alice')
    expect(JSON.stringify({ ...localStorage })).not.toContain('tok"')
  })

  it('logout clears state and storage', () => {
    useAuthStore.getState().login('tok', 'alice', 'admin', 'org1', 'Acme')
    useAuthStore.getState().logout()
    const state = useAuthStore.getState()
    expect(state.isAuthenticated).toBe(false)
    expect(state.accessToken).toBeNull()
    expect(localStorage.getItem('role')).toBeNull()
    expect(localStorage.getItem('org_id')).toBeNull()
  })
})

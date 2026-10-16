import axios, { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from 'axios'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import api, { shouldAttemptRefresh } from './client'
import { useAuthStore } from '../store/authStore'

function unauthorized(config: InternalAxiosRequestConfig, detail: string): Promise<never> {
  return Promise.reject(
    new AxiosError('Unauthorized', 'ERR_BAD_REQUEST', config, null, {
      status: 401,
      statusText: 'Unauthorized',
      headers: {},
      config,
      data: { detail },
    }),
  )
}

describe('shouldAttemptRefresh', () => {
  it('never refreshes for the login or refresh endpoints', () => {
    expect(shouldAttemptRefresh('/auth/login')).toBe(false)
    expect(shouldAttemptRefresh('auth/login')).toBe(false)
    expect(shouldAttemptRefresh('/auth/refresh')).toBe(false)
  })

  it('refreshes for ordinary API calls', () => {
    expect(shouldAttemptRefresh('/devices')).toBe(true)
    expect(shouldAttemptRefresh('/auth/mfa/status')).toBe(true)
    expect(shouldAttemptRefresh(undefined)).toBe(true)
  })
})

describe('401 handling on login', () => {
  const originalApiAdapter = api.defaults.adapter
  const originalAxiosAdapter = axios.defaults.adapter
  let refreshCalls = 0

  beforeEach(() => {
    refreshCalls = 0
    useAuthStore.getState().login('tok', 'alice', 'admin')
    // The refresh request goes through the global axios instance.
    axios.defaults.adapter = (async (config: InternalAxiosRequestConfig) => {
      refreshCalls += 1
      return unauthorized(config, 'no refresh cookie')
    }) as AxiosAdapter
  })

  afterEach(() => {
    api.defaults.adapter = originalApiAdapter
    axios.defaults.adapter = originalAxiosAdapter
  })

  it('surfaces "MFA code required" to the caller without refreshing or logging out', async () => {
    api.defaults.adapter = ((config: InternalAxiosRequestConfig) => unauthorized(config, 'MFA code required')) as AxiosAdapter

    await expect(api.post('/auth/login', { username: 'a', password: 'b' })).rejects.toMatchObject({
      response: { status: 401, data: { detail: 'MFA code required' } },
    })
    expect(refreshCalls).toBe(0)
    expect(useAuthStore.getState().isAuthenticated).toBe(true) // not logged out
  })

  it('still tries to refresh for an expired session on a normal endpoint', async () => {
    api.defaults.adapter = ((config: InternalAxiosRequestConfig) => unauthorized(config, 'expired')) as AxiosAdapter

    await expect(api.get('/devices')).rejects.toBeDefined()
    expect(refreshCalls).toBe(1)
  })
})

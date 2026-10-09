import api from './client'
import type { AuthTokens } from '../types'

export const authApi = {
  login: (username: string, password: string, otp?: string) =>
    api.post<AuthTokens>('/auth/login', { username, password, ...(otp ? { otp } : {}) }).then((r) => r.data),
  mfaStatus: () => api.get<{ mfa_enabled: boolean }>('/auth/mfa/status').then((r) => r.data),
  mfaSetup: () => api.post<{ secret: string; otpauth_uri: string }>('/auth/mfa/setup').then((r) => r.data),
  mfaEnable: (code: string) => api.post('/auth/mfa/enable', { code }).then((r) => r.data),
  mfaDisable: (password: string, code: string) => api.post('/auth/mfa/disable', { password, code }).then((r) => r.data),
  // No refresh_token argument: the server reads it from the httpOnly cookie
  // it set on login, so the browser never needs to hold or send it directly.
  refresh: () => api.post<AuthTokens>('/auth/refresh', {}).then((r) => r.data),
  logout: () => api.post('/auth/logout', {}).then((r) => r.data),
}

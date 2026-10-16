import axios from 'axios'
import { useAuthStore } from '../store/authStore'

const api = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
  // Sends the httpOnly refresh-token cookie on same-origin requests; the
  // access token itself is attached below from in-memory store state.
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Concurrent 401s share a single in-flight refresh instead of each firing
// their own /auth/refresh call.
let refreshPromise: Promise<string> | null = null

// A 401 from the login endpoint means "bad credentials" or "MFA code required",
// not "session expired". Treating it as an expired session triggered a refresh
// attempt, a logout and a hard reload of /login, which wiped the MFA code step.
const NO_REFRESH_URLS = /^\/?auth\/(login|refresh)(\?|$)/

export function shouldAttemptRefresh(url: string | undefined): boolean {
  return !NO_REFRESH_URLS.test(url ?? '')
}

api.interceptors.response.use(
  (res) => res,
  async (err) => {
    const originalRequest = err.config
    if (err.response?.status === 401 && originalRequest && !originalRequest._retried && shouldAttemptRefresh(originalRequest.url)) {
      originalRequest._retried = true
      try {
        if (!refreshPromise) {
          refreshPromise = axios
            .post('/api/v1/auth/refresh', {}, { withCredentials: true })
            .then((res) => {
              useAuthStore.getState().setAccessToken(res.data.access_token)
              return res.data.access_token as string
            })
            .finally(() => {
              refreshPromise = null
            })
        }
        const newToken = await refreshPromise
        originalRequest.headers.Authorization = `Bearer ${newToken}`
        return api.request(originalRequest)
      } catch {
        useAuthStore.getState().logout()
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
      }
    }
    return Promise.reject(err)
  }
)

export default api

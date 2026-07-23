import { useCallback } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import api from '../api/client'

interface AlertItem {
  id: string
  device_id: string
  alert_type: string
  severity: string
  message: string
  created_at: string
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#ef4444',
  warning: '#f59e0b',
  info: '#3b82f6',
}

const SEVERITY_ORDER: Record<string, number> = { critical: 0, warning: 1, info: 2 }

function AlertCard({ alert, onResolve }: { alert: AlertItem; onResolve: () => void }) {
  const color = SEVERITY_COLORS[alert.severity] ?? '#94a3b8'
  return (
    <View style={[styles.card, { borderLeftColor: color }]}>
      <View style={styles.cardHeader}>
        <Text style={[styles.severity, { color }]}>{alert.severity.toUpperCase()}</Text>
        <Text style={styles.time}>
          {formatDistanceToNow(new Date(alert.created_at), { addSuffix: true })}
        </Text>
      </View>
      <Text style={styles.message}>{alert.message}</Text>
      <Text style={styles.alertType}>{alert.alert_type}</Text>
      <View style={styles.actions}>
        <Text style={styles.resolveBtn} onPress={onResolve}>
          Resolve
        </Text>
      </View>
    </View>
  )
}

export default function AlertsScreen() {
  const queryClient = useQueryClient()

  const { data: rawAlerts = [], isLoading, isError, refetch, isFetching } = useQuery<AlertItem[]>({
    queryKey: ['mobile-all-alerts'],
    queryFn: () => api.get('/alerts', { params: { resolved: false, limit: 200 } }).then((r) => r.data),
    refetchInterval: 30000,
  })

  const alerts = [...rawAlerts].sort((a, b) => {
    const sev = (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99)
    if (sev !== 0) return sev
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  })

  const resolve = useMutation({
    mutationFn: (alertId: string) => api.post(`/alerts/${alertId}/resolve`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mobile-all-alerts'] }),
    onError: () => Alert.alert('Could not resolve alert', 'Please check your connection and try again.'),
  })

  const renderAlert = useCallback(({ item }: { item: AlertItem }) => (
    <AlertCard alert={item} onResolve={() => resolve.mutate(item.id)} />
  ), [resolve])

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#2563eb" />
      </View>
    )
  }

  if (isError) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorText}>Couldn't load alerts. Check your connection and try again.</Text>
        <TouchableOpacity style={styles.retryButton} onPress={() => refetch()}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    )
  }

  return (
    <FlatList
      data={alerts}
      keyExtractor={(item) => item.id}
      renderItem={renderAlert}
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={isFetching} onRefresh={refetch} colors={['#2563eb']} />}
      ListEmptyComponent={
        <View style={styles.center}>
          <Text style={styles.emptyText}>No active alerts</Text>
        </View>
      }
    />
  )
}

const styles = StyleSheet.create({
  list: { padding: 16, gap: 10 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  emptyText: { color: '#94a3b8', fontSize: 15 },
  errorText: { color: '#ef4444', fontSize: 14, textAlign: 'center', marginBottom: 14 },
  retryButton: { backgroundColor: '#2563eb', borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10 },
  retryButtonText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    borderLeftWidth: 4,
    shadowColor: '#000',
    shadowOpacity: 0.05,
    shadowRadius: 6,
    elevation: 2,
  },
  cardHeader: { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
  severity: { fontWeight: '700', fontSize: 11 },
  time: { color: '#94a3b8', fontSize: 11 },
  message: { color: '#374151', fontSize: 14, marginBottom: 4 },
  alertType: { color: '#94a3b8', fontSize: 11, marginBottom: 8 },
  actions: { flexDirection: 'row', justifyContent: 'flex-end' },
  resolveBtn: { color: '#2563eb', fontSize: 13, fontWeight: '600' },
})

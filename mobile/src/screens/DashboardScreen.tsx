import { useCallback } from 'react'
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useQuery } from '@tanstack/react-query'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import api from '../api/client'

interface Device {
  id: string
  hostname: string
  label: string | null
  os_type: string
  status: string
  cpu_percent: number | null
  ram_percent: number | null
  unresolved_alerts: number
}

type RootStackParamList = {
  DeviceDetail: { deviceId: string; hostname: string }
}

function StatusDot({ status }: { status: string }) {
  return (
    <View
      style={[
        styles.statusDot,
        { backgroundColor: status === 'online' ? '#22c55e' : '#94a3b8' },
      ]}
    />
  )
}

function GaugeBar({ value }: { value: number | null }) {
  if (value === null) return <Text style={styles.gaugeText}>—</Text>
  const color = value > 85 ? '#ef4444' : value > 70 ? '#f59e0b' : '#22c55e'
  return (
    <View style={styles.gaugeRow}>
      <View style={styles.gaugeTrack}>
        <View style={[styles.gaugeFill, { width: `${value}%` as any, backgroundColor: color }]} />
      </View>
      <Text style={styles.gaugeText}>{Math.round(value)}%</Text>
    </View>
  )
}

export default function DashboardScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>()

  const { data: devices = [], isLoading, isError, refetch, isFetching } = useQuery<Device[]>({
    queryKey: ['mobile-devices'],
    queryFn: () => api.get('/devices').then((r) => r.data),
    refetchInterval: 30000,
  })

  const { data: alertCounts } = useQuery<Record<string, number>>({
    queryKey: ['mobile-alert-counts'],
    queryFn: async () => {
      const resp = await api.get('/alerts', { params: { resolved: false, limit: 500 } })
      const counts: Record<string, number> = {}
      for (const alert of resp.data) {
        counts[alert.device_id] = (counts[alert.device_id] ?? 0) + 1
      }
      return counts
    },
    refetchInterval: 60000,
  })

  const renderDevice = useCallback(({ item }: { item: Device }) => {
    const name = item.label || item.hostname
    const alertCount = alertCounts?.[item.id] ?? 0
    return (
      <TouchableOpacity
        style={styles.card}
        onPress={() => navigation.navigate('DeviceDetail', { deviceId: item.id, hostname: name })}
        activeOpacity={0.7}
      >
        <View style={styles.cardHeader}>
          <StatusDot status={item.status} />
          <Text style={styles.hostname} numberOfLines={1}>{name}</Text>
          {alertCount > 0 && (
            <View style={styles.alertBadge}>
              <Text style={styles.alertBadgeText}>{alertCount}</Text>
            </View>
          )}
        </View>
        <Text style={styles.osType}>{item.os_type}</Text>
        <View style={styles.metrics}>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>CPU</Text>
            <GaugeBar value={item.cpu_percent} />
          </View>
          <View style={styles.metricRow}>
            <Text style={styles.metricLabel}>RAM</Text>
            <GaugeBar value={item.ram_percent} />
          </View>
        </View>
      </TouchableOpacity>
    )
  }, [navigation, alertCounts])

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
        <Text style={styles.errorText}>Couldn't load devices. Check your connection and try again.</Text>
        <TouchableOpacity style={styles.retryButton} onPress={() => refetch()}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    )
  }

  return (
    <FlatList
      data={devices}
      keyExtractor={(item) => item.id}
      renderItem={renderDevice}
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={isFetching} onRefresh={refetch} colors={['#2563eb']} />}
      ListEmptyComponent={
        <View style={styles.center}>
          <Text style={styles.emptyText}>No devices yet</Text>
        </View>
      }
    />
  )
}

const styles = StyleSheet.create({
  list: { padding: 16, gap: 12 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  emptyText: { color: '#94a3b8', fontSize: 15 },
  errorText: { color: '#ef4444', fontSize: 14, textAlign: 'center', marginBottom: 14 },
  retryButton: { backgroundColor: '#2563eb', borderRadius: 10, paddingHorizontal: 20, paddingVertical: 10 },
  retryButtonText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 14,
    padding: 16,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 3,
  },
  cardHeader: { flexDirection: 'row', alignItems: 'center', marginBottom: 4, gap: 8 },
  statusDot: { width: 10, height: 10, borderRadius: 5 },
  hostname: { flex: 1, fontWeight: '600', fontSize: 16, color: '#1e293b' },
  alertBadge: {
    backgroundColor: '#ef4444',
    borderRadius: 10,
    paddingHorizontal: 7,
    paddingVertical: 2,
    minWidth: 20,
    alignItems: 'center',
  },
  alertBadgeText: { color: '#fff', fontSize: 11, fontWeight: '700' },
  osType: { color: '#94a3b8', fontSize: 12, marginBottom: 10 },
  metrics: { gap: 6 },
  metricRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  metricLabel: { color: '#64748b', fontSize: 12, width: 36 },
  gaugeRow: { flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6 },
  gaugeTrack: {
    flex: 1,
    height: 6,
    backgroundColor: '#e2e8f0',
    borderRadius: 3,
    overflow: 'hidden',
  },
  gaugeFill: { height: 6, borderRadius: 3 },
  gaugeText: { color: '#64748b', fontSize: 11, width: 36, textAlign: 'right' },
})

import { describe, expect, it } from 'vitest'
import { nextRuns, parseCron } from './cron'

describe('parseCron', () => {
  it('describes common schedules', () => {
    expect(parseCron('0 3 * * *')).toBe('Every day at 03:00')
    expect(parseCron('*/5 * * * *')).toBe('Every 5 minutes')
    expect(parseCron('0 * * * *')).toBe('Every hour')
    expect(parseCron('30 8 * * *')).toBe('Every day at 08:30')
  })

  it('rejects malformed expressions', () => {
    expect(parseCron('* * *')).toBe('Invalid cron expression')
  })
})

describe('nextRuns', () => {
  it('returns the requested number of future runs in order', () => {
    const runs = nextRuns('*/15 * * * *', 3)
    expect(runs).toHaveLength(3)
    expect(runs[0].getTime()).toBeGreaterThan(Date.now())
    runs.forEach((run) => expect(run.getMinutes() % 15).toBe(0))
    expect(runs[1].getTime() - runs[0].getTime()).toBe(15 * 60 * 1000)
  })

  it('returns nothing for an invalid expression', () => {
    expect(nextRuns('bad', 3)).toEqual([])
  })
})

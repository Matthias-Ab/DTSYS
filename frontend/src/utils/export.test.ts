import { describe, expect, it } from 'vitest'
import { escapeCSV } from './export'

describe('escapeCSV', () => {
  it('quotes and escapes embedded quotes', () => {
    expect(escapeCSV('say "hi"')).toBe('"say ""hi"""')
  })

  it('neutralises spreadsheet formulas', () => {
    for (const payload of ['=1+1', '+cmd', '-2', '@SUM(A1)', '\tx']) {
      expect(escapeCSV(payload)).toBe(`"'${payload}"`)
    }
  })

  it('leaves ordinary values alone', () => {
    expect(escapeCSV('host-01')).toBe('"host-01"')
    expect(escapeCSV('')).toBe('""')
  })
})

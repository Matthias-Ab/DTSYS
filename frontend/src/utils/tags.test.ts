import { describe, expect, it } from 'vitest'
import { getTagColor } from './tags'

describe('getTagColor', () => {
  it('is deterministic for the same tag', () => {
    expect(getTagColor('prod')).toBe(getTagColor('prod'))
  })

  it('always returns a tailwind class string', () => {
    for (const tag of ['', 'a', 'production', 'ünïcode']) {
      expect(getTagColor(tag)).toMatch(/^bg-.+ text-.+/)
    }
  })
})

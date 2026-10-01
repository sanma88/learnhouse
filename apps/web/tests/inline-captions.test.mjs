import { describe, expect, test } from 'bun:test'
import { resolveInlineCaptions } from '../lib/media/inlineCaptions.ts'

const vtt = 'WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nDéplace le nœud : été, € & <b>n8n</b>.\n'
const track = { code: 'fr', label: 'Français', vtt }

describe('course-owned caption tracks', () => {
  test('native player can fetch exact VTT without an authenticated iframe', async () => {
    const [resolved] = resolveInlineCaptions([track])
    expect(resolved.code).toBe('fr')
    expect(resolved.label).toBe('Français')
    const response = await fetch(resolved.url, { credentials: 'include' })
    expect(response.headers.get('content-type')).toBe('text/vtt;charset=utf-8')
    expect(await response.text()).toBe(vtt)
  })
  test('legacy blocks and malformed imports cannot break the player', () => {
    for (const value of [null, undefined, {}, 'text', [null, 4, {}, { ...track, vtt: 42 }]]) {
      expect(resolveInlineCaptions(value)).toEqual([])
    }
    expect(resolveInlineCaptions([{ ...track, vtt: '\ud800' }])).toEqual([])
  })
  test('never permits imported remote URLs or executable content instead of VTT', () => {
    expect(resolveInlineCaptions([{ code: 'fr', label: 'FR', url: 'https://other.example/private' }])).toEqual([])
    expect(resolveInlineCaptions([{ ...track, vtt: '<script>alert(1)</script>' }])).toEqual([])
    expect(resolveInlineCaptions([{ ...track, vtt: 'WEBVTTbad' }])).toEqual([])
  })
  test('handles BOM, CRLF, regional languages and prevents duplicate menu entries', () => {
    const tracks = resolveInlineCaptions([track, { ...track, code: 'FR' }, { ...track, code: 'pt-BR', vtt: '\ufeffWEBVTT\r\n\r\n' }])
    expect(tracks.map(t => t.code)).toEqual(['fr', 'pt-BR'])
  })
  test('bounds imported content and labels', () => {
    expect(resolveInlineCaptions([{ ...track, vtt: 'WEBVTT\n' + 'a'.repeat(1_000_000) }])).toEqual([])
    expect(resolveInlineCaptions([{ ...track, label: 'a'.repeat(101) }])).toEqual([])
    expect(resolveInlineCaptions([{ ...track, code: '../../fr' }])).toEqual([])
    expect(resolveInlineCaptions([{ ...track, label: ' ' }])).toEqual([])
  })
})

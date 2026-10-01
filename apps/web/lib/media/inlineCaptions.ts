import type { CaptionTrack } from '../../components/Objects/Activities/Video/videoSource'

/** Course-owned VTT text, never an arbitrary URL or an authentication token. */
export function resolveInlineCaptions(value: unknown): CaptionTrack[] {
  if (!Array.isArray(value)) return []
  const tracks: CaptionTrack[] = []
  const codes = new Set<string>()
  for (const entry of value.slice(0, 20)) {
    if (!entry || typeof entry !== 'object') continue
    const { code, label, vtt } = entry
    if (typeof code !== 'string' || !/^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*$/.test(code)) continue
    if (typeof label !== 'string' || !label.trim() || label.length > 100) continue
    if (typeof vtt !== 'string' || vtt.length > 1_000_000 || !/^\uFEFF?WEBVTT(?:[ \t]|\r?\n|$)/.test(vtt)) continue
    if (codes.has(code.toLowerCase())) continue
    try {
      const url = 'data:text/vtt;charset=utf-8,' + encodeURIComponent(vtt)
      tracks.push({ code, label: label.trim(), url })
      codes.add(code.toLowerCase())
    } catch {
      // Invalid Unicode in imported content must not break video playback.
    }
  }
  return tracks
}

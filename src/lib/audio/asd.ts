// Ableton .asd (Analysis) file generation.
//
// Status: stubbed. The .asd binary format is proprietary; a faithful port of
// AbletonParsing's Python writer (DBraun/AbletonParsing) requires the
// reference repo for byte-accurate struct layout. Rather than ship a
// speculative implementation that Live may reject, v1 of the exporter relies
// on the filename-encoded BPM (e.g. "kick_01_128bpm.wav") which Ableton's
// auto-warp respects when importing. Clips still drop into session view
// correctly; the user just sees the standard auto-warp rather than a
// pre-warped state.
//
// When the .asd writer is implemented, populate the fields per the plan
// (two warp markers at the loop boundaries, loop_on, warp_on, sr, start/end
// markers) and regression-test against the Python reader for byte parity.

export interface AsdClipMeta {
  bpm: number
  loopLengthSeconds: number
  loopLengthBeats: number
  sampleRate: number
}

export function buildAsdStub(meta: AsdClipMeta): Uint8Array | null {
  void meta
  return null
}

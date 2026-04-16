import type { Bars } from "./types"

// samplesPerBar at a given BPM, rounded to integer samples. Callers should
// prefer the "from downbeat" helpers below for drift-free math when placing
// multiple cuts.
export function samplesPerBar(bpm: number, sampleRate: number): number {
  return Math.round((4 * 60 * sampleRate) / bpm)
}

// Nearest bar index >= 0 relative to the project downbeat.
export function nearestBarIndex(
  clickSample: number,
  downbeatSample: number,
  bpm: number,
  sampleRate: number,
): number {
  const spb = (4 * 60 * sampleRate) / bpm
  const rel = clickSample - downbeatSample
  return Math.max(0, Math.round(rel / spb))
}

// Compute the exact sample boundaries of `bars` bars starting at the given
// bar index, measured from the downbeat. Recomputing from the downbeat each
// time prevents sub-millisecond drift from accumulating across cuts.
export function barRangeSamples(
  barIndex: number,
  bars: Bars,
  downbeatSample: number,
  bpm: number,
  sampleRate: number,
): { start: number; end: number; length: number } {
  const beatsPerBar = 4
  const start =
    downbeatSample +
    Math.round((barIndex * beatsPerBar * 60 * sampleRate) / bpm)
  const end =
    downbeatSample +
    Math.round(((barIndex + bars) * beatsPerBar * 60 * sampleRate) / bpm)
  return { start, end, length: end - start }
}

// Extract a slice of an AudioBuffer into a new AudioBuffer, zero-padding if
// the requested range runs past the end of the source.
export function sliceBuffer(
  src: AudioBuffer,
  startSample: number,
  length: number,
): AudioBuffer {
  const out = new AudioBuffer({
    length,
    numberOfChannels: src.numberOfChannels,
    sampleRate: src.sampleRate,
  })
  const available = Math.max(0, Math.min(length, src.length - startSample))
  for (let ch = 0; ch < src.numberOfChannels; ch += 1) {
    const srcData = src.getChannelData(ch)
    const dstData = out.getChannelData(ch)
    for (let i = 0; i < available; i += 1) {
      dstData[i] = srcData[startSample + i]
    }
    // remainder is zeroed by AudioBuffer initialization
  }
  return out
}

export interface CutResult {
  buffer: AudioBuffer
  startSample: number
  endSample: number
  barIndex: number
}

export function extractLoopAtClick(
  songBuffer: AudioBuffer,
  clickSample: number,
  bars: Bars,
  bpm: number,
  downbeatSample: number,
): CutResult {
  const sr = songBuffer.sampleRate
  const barIndex = nearestBarIndex(clickSample, downbeatSample, bpm, sr)
  const { start, end, length } = barRangeSamples(
    barIndex,
    bars,
    downbeatSample,
    bpm,
    sr,
  )
  const buffer = sliceBuffer(songBuffer, start, length)
  return { buffer, startSample: start, endSample: end, barIndex }
}

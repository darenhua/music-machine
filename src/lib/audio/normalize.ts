import { PROJECT_SAMPLE_RATE } from "./context"

// Resample an AudioBuffer to the target sample rate using OfflineAudioContext.
// Preserves channel count and duration.
export async function resample(
  src: AudioBuffer,
  targetRate: number = PROJECT_SAMPLE_RATE,
): Promise<AudioBuffer> {
  if (src.sampleRate === targetRate) return src
  const targetLength = Math.max(
    1,
    Math.round((src.length / src.sampleRate) * targetRate),
  )
  const offline = new OfflineAudioContext(
    src.numberOfChannels,
    targetLength,
    targetRate,
  )
  const source = offline.createBufferSource()
  source.buffer = src
  source.connect(offline.destination)
  source.start(0)
  return offline.startRendering()
}

// Scale all channels so the peak absolute value maps to the given target
// level. Returns a NEW AudioBuffer (original untouched). Default -1 dBFS.
export function peakNormalize(
  src: AudioBuffer,
  targetDbFs = -1,
): AudioBuffer {
  const channels = src.numberOfChannels
  const length = src.length
  let peak = 0
  for (let ch = 0; ch < channels; ch += 1) {
    const data = src.getChannelData(ch)
    for (let i = 0; i < length; i += 1) {
      const v = Math.abs(data[i])
      if (v > peak) peak = v
    }
  }
  const targetLin = Math.pow(10, targetDbFs / 20)
  const gain = peak > 1e-9 ? targetLin / peak : 1
  const out = new AudioBuffer({
    length,
    numberOfChannels: channels,
    sampleRate: src.sampleRate,
  })
  for (let ch = 0; ch < channels; ch += 1) {
    const src_ = src.getChannelData(ch)
    const dst = out.getChannelData(ch)
    for (let i = 0; i < length; i += 1) dst[i] = src_[i] * gain
  }
  return out
}

// Apply short linear fade-in and fade-out to kill loop-boundary clicks.
// 64 samples @ 44.1 kHz is about 1.5 ms — inaudible, but enough to mask the
// discontinuity at the splice. Mutates the buffer in place.
export function applyEdgeFades(buf: AudioBuffer, fadeSamples = 64): void {
  const n = Math.min(fadeSamples, Math.floor(buf.length / 2))
  if (n <= 0) return
  for (let ch = 0; ch < buf.numberOfChannels; ch += 1) {
    const data = buf.getChannelData(ch)
    for (let i = 0; i < n; i += 1) {
      const g = i / n
      data[i] *= g
      data[buf.length - 1 - i] *= g
    }
  }
}

// Full export pipeline: resample → peak-normalize → edge fades.
export async function normalizeForExport(src: AudioBuffer): Promise<AudioBuffer> {
  const resampled = await resample(src, PROJECT_SAMPLE_RATE)
  const normed = peakNormalize(resampled, -1)
  applyEdgeFades(normed, 64)
  return normed
}

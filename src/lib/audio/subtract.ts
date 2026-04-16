// Per-sample, per-channel subtraction: target - Σ subtrahends.
//
// Precondition: all inputs share sampleRate and channel count. If a subtrahend
// is shorter than the target, it is tiled (looped) to match length — never
// pre-materialized elsewhere.
//
// Does NOT clamp to [-1, 1] — intermediate peaks may exceed unity. Normalize
// at export time instead.
export function subtract(
  target: AudioBuffer,
  subtrahends: AudioBuffer[],
): AudioBuffer {
  const channels = target.numberOfChannels
  const sampleRate = target.sampleRate
  const length = target.length

  for (const s of subtrahends) {
    if (s.sampleRate !== sampleRate) {
      throw new Error(
        `subtract: sample-rate mismatch (target ${sampleRate} vs ${s.sampleRate})`,
      )
    }
    if (s.numberOfChannels !== channels) {
      throw new Error(
        `subtract: channel-count mismatch (target ${channels} vs ${s.numberOfChannels})`,
      )
    }
  }

  const out = new AudioBuffer({ length, numberOfChannels: channels, sampleRate })
  for (let ch = 0; ch < channels; ch += 1) {
    const tgtData = target.getChannelData(ch)
    const outData = out.getChannelData(ch)
    for (let i = 0; i < length; i += 1) outData[i] = tgtData[i]
    for (const sub of subtrahends) {
      const subData = sub.getChannelData(ch)
      const subLen = sub.length
      if (subLen === 0) continue
      // Tile subtrahend over target length using modulo.
      for (let i = 0; i < length; i += 1) {
        outData[i] -= subData[i % subLen]
      }
    }
  }
  return out
}

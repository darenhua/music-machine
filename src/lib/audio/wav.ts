import { WaveFile } from "wavefile"

export type WavBitDepth = "16" | "24"

// Encode an AudioBuffer to a PCM WAV Blob using wavefile. 16-bit is the
// Ableton-recommended default; 24-bit available for headroom.
export function audioBufferToWavBlob(
  buf: AudioBuffer,
  bitDepth: WavBitDepth = "16",
): Blob {
  const numChannels = buf.numberOfChannels
  const sampleRate = buf.sampleRate
  const frames = buf.length

  // Interleave channels into a flat Float32Array [L,R,L,R,...].
  const interleaved = new Float32Array(frames * numChannels)
  for (let ch = 0; ch < numChannels; ch += 1) {
    const data = buf.getChannelData(ch)
    for (let i = 0; i < frames; i += 1) {
      interleaved[i * numChannels + ch] = data[i]
    }
  }

  // Clamp + scale float32 [-1, 1] to the target integer range.
  const intSamples =
    bitDepth === "16"
      ? new Int16Array(interleaved.length)
      : new Int32Array(interleaved.length)
  const maxVal = bitDepth === "16" ? 0x7fff : 0x7fffff
  for (let i = 0; i < interleaved.length; i += 1) {
    let v = interleaved[i]
    if (v > 1) v = 1
    else if (v < -1) v = -1
    intSamples[i] = Math.round(v * maxVal)
  }

  const wav = new WaveFile()
  wav.fromScratch(numChannels, sampleRate, bitDepth, intSamples)
  const bytes = wav.toBuffer()
  return new Blob([bytes as unknown as ArrayBuffer], { type: "audio/wav" })
}

export function audioBufferToBlobUrl(
  buf: AudioBuffer,
  bitDepth: WavBitDepth = "16",
): string {
  return URL.createObjectURL(audioBufferToWavBlob(buf, bitDepth))
}

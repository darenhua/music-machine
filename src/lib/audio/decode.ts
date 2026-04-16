import {
  PROJECT_CHANNELS,
  PROJECT_SAMPLE_RATE,
  getAudioContext,
} from "./context"

export async function decodeFile(file: File): Promise<AudioBuffer> {
  const arrayBuffer = await file.arrayBuffer()
  const ctx = getAudioContext()
  const raw = await ctx.decodeAudioData(arrayBuffer.slice(0))
  return normalizeBuffer(raw)
}

// Resample to the project rate and force the project channel count via
// OfflineAudioContext. This is essential: everything downstream (subtract,
// .asd, pack) assumes a single uniform rate/channel layout.
export async function normalizeBuffer(src: AudioBuffer): Promise<AudioBuffer> {
  if (
    src.sampleRate === PROJECT_SAMPLE_RATE &&
    src.numberOfChannels === PROJECT_CHANNELS
  ) {
    return src
  }
  const targetLength = Math.max(
    1,
    Math.round((src.length / src.sampleRate) * PROJECT_SAMPLE_RATE),
  )
  const offline = new OfflineAudioContext(
    PROJECT_CHANNELS,
    targetLength,
    PROJECT_SAMPLE_RATE,
  )
  const source = offline.createBufferSource()
  source.buffer = src
  source.connect(offline.destination)
  source.start(0)
  return offline.startRendering()
}

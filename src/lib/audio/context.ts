export const PROJECT_SAMPLE_RATE = 44100
export const PROJECT_CHANNELS = 2

let ctx: AudioContext | null = null

export function getAudioContext(): AudioContext {
  if (!ctx) {
    ctx = new AudioContext({ sampleRate: PROJECT_SAMPLE_RATE })
  }
  return ctx
}

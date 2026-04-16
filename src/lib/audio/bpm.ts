import EssentiaWorker from "@/workers/essentia.worker?worker"
import type {
  AnalyzeError,
  AnalyzeResponse,
} from "@/workers/essentia.worker"

export interface BpmResult {
  bpm: number
  beatsSeconds: number[]
  confidence: number
}

function audioBufferToMono(buf: AudioBuffer): Float32Array {
  if (buf.numberOfChannels === 1) {
    return new Float32Array(buf.getChannelData(0))
  }
  const len = buf.length
  const out = new Float32Array(len)
  const scale = 1 / buf.numberOfChannels
  for (let ch = 0; ch < buf.numberOfChannels; ch += 1) {
    const data = buf.getChannelData(ch)
    for (let i = 0; i < len; i += 1) {
      out[i] += data[i] * scale
    }
  }
  return out
}

export async function analyzeBpm(buffer: AudioBuffer): Promise<BpmResult> {
  const worker = new EssentiaWorker()
  const mono = audioBufferToMono(buffer)
  return new Promise<BpmResult>((resolve, reject) => {
    worker.onmessage = (e: MessageEvent<AnalyzeResponse | AnalyzeError>) => {
      const msg = e.data
      if (msg.type === "result") {
        resolve({
          bpm: msg.bpm,
          beatsSeconds: msg.beatsSeconds,
          confidence: msg.confidence,
        })
      } else {
        reject(new Error(msg.message))
      }
      worker.terminate()
    }
    worker.onerror = (e) => {
      reject(new Error(e.message))
      worker.terminate()
    }
    worker.postMessage({ type: "analyze", pcmMono: mono }, [mono.buffer])
  })
}

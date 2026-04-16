// Essentia.js BPM/beats analysis in a Web Worker. The WASM is embedded as
// a data URI in the ES module, so no separate .wasm fetch is needed.
// @ts-expect-error — no types shipped for the ES build path.
import Essentia from "essentia.js/dist/essentia.js-core.es.js"
// @ts-expect-error — ditto.
import { EssentiaWASM } from "essentia.js/dist/essentia-wasm.es.js"

type EssentiaInstance = {
  arrayToVector: (a: Float32Array) => unknown
  vectorToArray: (v: unknown) => Float32Array
  RhythmExtractor2013: (
    signal: unknown,
    maxTempo?: number,
    method?: string,
    minTempo?: number,
  ) => {
    bpm: number
    beats_position: unknown
    confidence: number
  }
}

let essentia: EssentiaInstance | null = null

function getEssentia(): EssentiaInstance {
  if (!essentia) {
    essentia = new (Essentia as unknown as new (
      wasm: unknown,
    ) => EssentiaInstance)(EssentiaWASM)
  }
  return essentia
}

export interface AnalyzeRequest {
  type: "analyze"
  pcmMono: Float32Array
}

export interface AnalyzeResponse {
  type: "result"
  bpm: number
  beatsSeconds: number[]
  confidence: number
}

export interface AnalyzeError {
  type: "error"
  message: string
}

self.onmessage = (e: MessageEvent<AnalyzeRequest>) => {
  const msg = e.data
  if (msg.type !== "analyze") return
  try {
    const es = getEssentia()
    const sig = es.arrayToVector(msg.pcmMono)
    const res = es.RhythmExtractor2013(sig, 208, "multifeature", 40)
    const beats = Array.from(es.vectorToArray(res.beats_position))
    const out: AnalyzeResponse = {
      type: "result",
      bpm: res.bpm,
      beatsSeconds: beats,
      confidence: res.confidence,
    }
    ;(self as unknown as Worker).postMessage(out)
  } catch (err) {
    const msg: AnalyzeError = {
      type: "error",
      message: err instanceof Error ? err.message : String(err),
    }
    ;(self as unknown as Worker).postMessage(msg)
  }
}

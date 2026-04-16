import JSZip from "jszip"
import type { Category, Tape } from "./types"
import { getBuffer } from "./bufferStore"
import { normalizeForExport } from "./normalize"
import { audioBufferToWavBlob } from "./wav"

const CATEGORY_FOLDER: Record<Category, string> = {
  kicks: "kicks",
  snares: "snares",
  bass: "bass",
  other: "other",
}

const README = `# Sample pack

Every WAV is 44.1 kHz stereo PCM, peak-normalized to -1 dBFS with 64-sample
edge fades. The BPM is encoded in each filename so Ableton Live's auto-warp
can lock the clip to the project tempo on import.

To use in Live:
1. Unzip this folder somewhere in your Ableton User Library.
2. Drag any WAV into a session-view clip slot.
3. Live auto-warps to the filename BPM. Enable Loop on the clip if needed.
`

export interface PackOptions {
  packName: string
  bpm: number
  tapes: Tape[] // tapes to include (typically excludes the source song)
  onProgress?: (done: number, total: number) => void
}

function safeFilename(name: string): string {
  return (
    name
      .trim()
      .replace(/[\\/:*?"<>|]+/g, "_")
      .replace(/\s+/g, "_")
      .slice(0, 80) || "clip"
  )
}

export async function buildPackZip(opts: PackOptions): Promise<Blob> {
  const { packName, bpm, tapes, onProgress } = opts
  const zip = new JSZip()
  const root = zip.folder(safeFilename(packName)) ?? zip
  root.file("README.md", README)

  const countsByCategory = new Map<Category, number>()
  let done = 0

  for (const tape of tapes) {
    const buf = getBuffer(tape.bufferId)
    if (!buf) continue
    const processed = await normalizeForExport(buf)
    const wav = audioBufferToWavBlob(processed, "16")
    const n = (countsByCategory.get(tape.category) ?? 0) + 1
    countsByCategory.set(tape.category, n)
    const idxStr = String(n).padStart(2, "0")
    const base = `${safeFilename(tape.name)}_${idxStr}_${Math.round(bpm)}bpm.wav`
    const folder =
      root.folder(CATEGORY_FOLDER[tape.category]) ?? root
    folder.file(base, wav)
    done += 1
    onProgress?.(done, tapes.length)
  }

  return zip.generateAsync({ type: "blob", compression: "DEFLATE" })
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke after the browser has had a chance to initiate the download.
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

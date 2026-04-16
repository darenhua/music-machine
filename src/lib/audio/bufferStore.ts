import type { TapeId } from "./types"

const buffers = new Map<TapeId, AudioBuffer>()
const blobUrls = new Map<TapeId, string>()

export function setBuffer(id: TapeId, buffer: AudioBuffer): void {
  buffers.set(id, buffer)
  const existing = blobUrls.get(id)
  if (existing) {
    URL.revokeObjectURL(existing)
    blobUrls.delete(id)
  }
}

export function getBuffer(id: TapeId): AudioBuffer | undefined {
  return buffers.get(id)
}

export function deleteBuffer(id: TapeId): void {
  buffers.delete(id)
  const url = blobUrls.get(id)
  if (url) {
    URL.revokeObjectURL(url)
    blobUrls.delete(id)
  }
}

export function setBlobUrl(id: TapeId, url: string): void {
  const prev = blobUrls.get(id)
  if (prev && prev !== url) URL.revokeObjectURL(prev)
  blobUrls.set(id, url)
}

export function getBlobUrl(id: TapeId): string | undefined {
  return blobUrls.get(id)
}

let idCounter = 0
export function newTapeId(prefix = "tape"): TapeId {
  idCounter += 1
  return `${prefix}_${Date.now().toString(36)}_${idCounter}`
}

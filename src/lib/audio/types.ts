export type TapeId = string

export type Category = "kicks" | "snares" | "bass" | "other"

export type Bars = 1 | 2 | 4 | 8

export const BARS_CHOICES: readonly Bars[] = [1, 2, 4, 8] as const

export interface Tape {
  id: TapeId
  name: string
  category: Category
  kind: "source" | "cut" | "derived"
  bufferId: TapeId
  sampleRate: number
  channels: number
  // Constrained loop length (undefined for the source song tape).
  bars?: Bars
  // Sample offsets relative to the project downbeat (undefined for source).
  sourceStartSample?: number
  sourceEndSample?: number
  // Lineage for derived (subtraction result) tapes.
  parents?: { targetId: TapeId; subtrahendIds: TapeId[] }
  muted: boolean
  gainDb: number
}

export const DEFAULT_CATEGORY: Category = "other"

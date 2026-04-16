import { create } from "zustand"
import type { Bars, Tape, TapeId } from "@/lib/audio/types"
import { PROJECT_SAMPLE_RATE } from "@/lib/audio/context"

interface ProjectState {
  songTapeId: TapeId | null
  bpm: number
  downbeatSample: number
  sampleRate: number
  songDurationSamples: number
  beatsSeconds: number[]
  defaultCutBars: Bars
  tapes: Tape[]
  selectedTargetId: TapeId | null
  selectedSubtrahendIds: TapeId[]
  analyzing: boolean

  setSong: (id: TapeId, durationSamples: number) => void
  setBpm: (bpm: number) => void
  setBeats: (beatsSeconds: number[], bpm: number) => void
  setDownbeatSample: (s: number) => void
  setDefaultCutBars: (b: Bars) => void
  addTape: (t: Tape) => void
  updateTape: (id: TapeId, patch: Partial<Tape>) => void
  removeTape: (id: TapeId) => void
  setTarget: (id: TapeId | null) => void
  toggleSubtrahend: (id: TapeId) => void
  clearSelection: () => void
  setAnalyzing: (b: boolean) => void
  reset: () => void
}

const initial = {
  songTapeId: null as TapeId | null,
  bpm: 120,
  downbeatSample: 0,
  sampleRate: PROJECT_SAMPLE_RATE,
  songDurationSamples: 0,
  beatsSeconds: [] as number[],
  defaultCutBars: 2 as Bars,
  tapes: [] as Tape[],
  selectedTargetId: null as TapeId | null,
  selectedSubtrahendIds: [] as TapeId[],
  analyzing: false,
}

export const useProjectStore = create<ProjectState>((set) => ({
  ...initial,

  setSong: (id, durationSamples) =>
    set({ songTapeId: id, songDurationSamples: durationSamples }),
  setBpm: (bpm) => set({ bpm }),
  setBeats: (beatsSeconds, bpm) => set({ beatsSeconds, bpm }),
  setDownbeatSample: (s) => set({ downbeatSample: Math.max(0, Math.round(s)) }),
  setDefaultCutBars: (b) => set({ defaultCutBars: b }),
  addTape: (t) => set((s) => ({ tapes: [...s.tapes, t] })),
  updateTape: (id, patch) =>
    set((s) => ({
      tapes: s.tapes.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    })),
  removeTape: (id) =>
    set((s) => ({
      tapes: s.tapes.filter((t) => t.id !== id),
      selectedTargetId: s.selectedTargetId === id ? null : s.selectedTargetId,
      selectedSubtrahendIds: s.selectedSubtrahendIds.filter((x) => x !== id),
    })),
  setTarget: (id) => set({ selectedTargetId: id, selectedSubtrahendIds: [] }),
  toggleSubtrahend: (id) =>
    set((s) => {
      if (s.selectedTargetId === id) return s
      return {
        selectedSubtrahendIds: s.selectedSubtrahendIds.includes(id)
          ? s.selectedSubtrahendIds.filter((x) => x !== id)
          : [...s.selectedSubtrahendIds, id],
      }
    }),
  clearSelection: () =>
    set({ selectedTargetId: null, selectedSubtrahendIds: [] }),
  setAnalyzing: (b) => set({ analyzing: b }),
  reset: () => set({ ...initial }),
}))

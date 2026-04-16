import { useEffect, useMemo, useRef, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Delete02Icon,
  PauseIcon,
  PlayIcon,
  Scissor01Icon,
  Target01Icon,
} from "@hugeicons/core-free-icons"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { Category, Tape } from "@/lib/audio/types"
import {
  deleteBuffer,
  getBlobUrl,
  getBuffer,
  newTapeId,
  setBlobUrl,
  setBuffer,
} from "@/lib/audio/bufferStore"
import { audioBufferToBlobUrl } from "@/lib/audio/wav"
import { useWaveSurfer } from "@/hooks/useWaveSurfer"
import { useProjectStore } from "@/state/useProjectStore"
import { extractLoopAtClick } from "@/lib/audio/cut"

const CATEGORY_OPTIONS: Category[] = ["kicks", "snares", "bass", "other"]
const CATEGORY_COLOR: Record<Category, string> = {
  kicks: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  snares: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  bass: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  other: "bg-muted text-muted-foreground",
}

interface Props {
  tape: Tape
  knifeActive: boolean
}

export function TapeRow({ tape, knifeActive }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wsOptions = useMemo(
    () => ({
      waveColor: "var(--muted-foreground)",
      progressColor: "var(--primary)",
      cursorColor: "var(--ring)",
      height: 56,
      barWidth: 2,
      barGap: 1,
      barRadius: 1,
      interact: true,
      normalize: true,
    }),
    [],
  )
  const url = useTapeBlobUrl(tape)
  const { ws, ready } = useWaveSurfer(containerRef, wsOptions, url)
  const [playing, setPlaying] = useState(false)

  // Loop short cut/derived tapes via the underlying media element.
  useEffect(() => {
    if (!ws || !ready) return
    const el = ws.getMediaElement()
    if (el) el.loop = tape.kind !== "source"
  }, [ws, ready, tape.kind])

  useEffect(() => {
    if (!ws) return
    const onPlay = () => setPlaying(true)
    const onPause = () => setPlaying(false)
    const onFinish = () => setPlaying(false)
    ws.on("play", onPlay)
    ws.on("pause", onPause)
    ws.on("finish", onFinish)
    return () => {
      ws.un("play", onPlay)
      ws.un("pause", onPause)
      ws.un("finish", onFinish)
    }
  }, [ws])

  const handleCut = useCut(tape)

  // Click-to-cut on the song waveform when the knife is active.
  useEffect(() => {
    if (!ws || tape.kind !== "source" || !knifeActive) return
    const onClick = (progress: number) => {
      handleCut(progress)
    }
    ws.on("click", onClick)
    return () => {
      ws.un("click", onClick)
    }
  }, [ws, tape.kind, knifeActive, handleCut])

  const selectedTargetId = useProjectStore((s) => s.selectedTargetId)
  const selectedSubtrahendIds = useProjectStore(
    (s) => s.selectedSubtrahendIds,
  )
  const setTarget = useProjectStore((s) => s.setTarget)
  const toggleSubtrahend = useProjectStore((s) => s.toggleSubtrahend)
  const removeTape = useProjectStore((s) => s.removeTape)
  const updateTape = useProjectStore((s) => s.updateTape)

  const isTarget = selectedTargetId === tape.id
  const isSubtrahend = selectedSubtrahendIds.includes(tape.id)
  const canBeSubtrahend =
    selectedTargetId != null && selectedTargetId !== tape.id && tape.kind !== "source"

  const togglePlay = () => {
    if (!ws) return
    if (playing) ws.pause()
    else void ws.play()
  }

  const onDelete = () => {
    deleteBuffer(tape.bufferId)
    removeTape(tape.id)
  }

  const cycleCategory = () => {
    const idx = CATEGORY_OPTIONS.indexOf(tape.category)
    const next = CATEGORY_OPTIONS[(idx + 1) % CATEGORY_OPTIONS.length]
    updateTape(tape.id, { category: next })
  }

  return (
    <div
      className={cn(
        "bg-card group/tape relative flex items-center gap-3 rounded-xl border p-3 shadow-sm transition-colors",
        isTarget && "border-primary ring-primary/30 ring-2",
        isSubtrahend && "border-destructive/60 ring-destructive/20 ring-2",
        tape.kind === "source" && knifeActive && "border-amber-500/70",
      )}
    >
      <div className="flex min-w-32 shrink-0 flex-col gap-1">
        <input
          className="text-foreground w-full bg-transparent text-sm font-medium outline-none"
          value={tape.name}
          onChange={(e) => updateTape(tape.id, { name: e.target.value })}
        />
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={cycleCategory}
            className={cn(
              "rounded-md px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
              CATEGORY_COLOR[tape.category],
            )}
            title="Click to change category"
          >
            {tape.category}
          </button>
          {tape.bars != null && (
            <span className="text-muted-foreground rounded-md bg-muted/60 px-1.5 py-0.5 text-[10px]">
              {tape.bars} bar{tape.bars === 1 ? "" : "s"}
            </span>
          )}
          {tape.kind === "derived" && (
            <span className="rounded-md bg-emerald-500/15 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
              derived
            </span>
          )}
        </div>
      </div>

      <div
        ref={containerRef}
        className={cn(
          "min-w-0 flex-1 overflow-hidden rounded-md bg-muted/40",
          tape.kind === "source" && knifeActive && "cursor-crosshair",
        )}
      />

      <div className="flex shrink-0 items-center gap-1">
        <Button
          size="icon-sm"
          variant="ghost"
          onClick={togglePlay}
          disabled={!ready}
          aria-label={playing ? "Pause" : "Play"}
        >
          <HugeiconsIcon icon={playing ? PauseIcon : PlayIcon} size={14} />
        </Button>

        {tape.kind !== "source" && (
          <Button
            size="icon-sm"
            variant={isTarget ? "default" : "ghost"}
            onClick={() => setTarget(isTarget ? null : tape.id)}
            aria-label="Mark as subtract target"
            title="Subtract target"
          >
            <HugeiconsIcon icon={Target01Icon} size={14} />
          </Button>
        )}

        {canBeSubtrahend && (
          <Button
            size="xs"
            variant={isSubtrahend ? "destructive" : "outline"}
            onClick={() => toggleSubtrahend(tape.id)}
          >
            {isSubtrahend ? "− subtracting" : "+ subtract"}
          </Button>
        )}

        {tape.kind === "source" && knifeActive && (
          <span className="text-muted-foreground ml-2 inline-flex items-center gap-1 text-xs">
            <HugeiconsIcon icon={Scissor01Icon} size={12} />
            click to cut
          </span>
        )}

        {tape.kind !== "source" && (
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={onDelete}
            aria-label="Delete tape"
          >
            <HugeiconsIcon icon={Delete02Icon} size={14} />
          </Button>
        )}
      </div>
    </div>
  )
}

// Convert the tape's AudioBuffer to a blob URL for wavesurfer.
// Cached per bufferId in bufferStore. Encoding is synchronous and fast for
// the 2-bar cuts we produce; the song tape's URL is set by Upload directly.
function useTapeBlobUrl(tape: Tape): string | null {
  return useMemo(() => {
    const cached = getBlobUrl(tape.bufferId)
    if (cached) return cached
    const buf = getBuffer(tape.bufferId)
    if (!buf) return null
    const next = audioBufferToBlobUrl(buf, "16")
    setBlobUrl(tape.bufferId, next)
    return next
  }, [tape.bufferId])
}

function useCut(tape: Tape) {
  const bpm = useProjectStore((s) => s.bpm)
  const downbeatSample = useProjectStore((s) => s.downbeatSample)
  const defaultCutBars = useProjectStore((s) => s.defaultCutBars)
  const addTape = useProjectStore((s) => s.addTape)

  return (progress: number) => {
    if (tape.kind !== "source") return
    const song = getBuffer(tape.bufferId)
    if (!song) return
    const clickSample = Math.round(progress * song.length)
    const result = extractLoopAtClick(
      song,
      clickSample,
      defaultCutBars,
      bpm,
      downbeatSample,
    )
    if (result.buffer.length < 128) return // ignore absurd cuts
    const newId = newTapeId("cut")
    setBuffer(newId, result.buffer)
    const barLabel = `${defaultCutBars}bar${defaultCutBars === 1 ? "" : "s"}`
    addTape({
      id: newId,
      name: `Cut @${result.barIndex} (${barLabel})`,
      category: "other",
      kind: "cut",
      bufferId: newId,
      sampleRate: result.buffer.sampleRate,
      channels: result.buffer.numberOfChannels,
      bars: defaultCutBars,
      sourceStartSample: result.startSample,
      sourceEndSample: result.endSample,
      muted: false,
      gainDb: 0,
    })
  }
}

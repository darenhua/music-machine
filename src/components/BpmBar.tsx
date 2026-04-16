import { useEffect, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import {
  Download04Icon,
  Scissor01Icon,
  Upload04Icon,
} from "@hugeicons/core-free-icons"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { BARS_CHOICES, type Bars } from "@/lib/audio/types"
import { getBuffer } from "@/lib/audio/bufferStore"
import { analyzeBpm } from "@/lib/audio/bpm"
import { useProjectStore } from "@/state/useProjectStore"

interface Props {
  knifeActive: boolean
  setKnifeActive: (b: boolean) => void
  onUploadClick: () => void
  onExportClick: () => void
  canExport: boolean
}

export function BpmBar({
  knifeActive,
  setKnifeActive,
  onUploadClick,
  onExportClick,
  canExport,
}: Props) {
  const songTapeId = useProjectStore((s) => s.songTapeId)
  const bpm = useProjectStore((s) => s.bpm)
  const downbeatSample = useProjectStore((s) => s.downbeatSample)
  const sampleRate = useProjectStore((s) => s.sampleRate)
  const defaultCutBars = useProjectStore((s) => s.defaultCutBars)
  const analyzing = useProjectStore((s) => s.analyzing)
  const beatsSeconds = useProjectStore((s) => s.beatsSeconds)
  const setBpm = useProjectStore((s) => s.setBpm)
  const setBeats = useProjectStore((s) => s.setBeats)
  const setDownbeatSample = useProjectStore((s) => s.setDownbeatSample)
  const setDefaultCutBars = useProjectStore((s) => s.setDefaultCutBars)
  const setAnalyzing = useProjectStore((s) => s.setAnalyzing)

  const [bpmText, setBpmText] = useState(bpm.toFixed(1))
  useEffect(() => {
    setBpmText(bpm.toFixed(1))
  }, [bpm])

  const [downbeatSec, setDownbeatSec] = useState(downbeatSample / sampleRate)
  useEffect(() => {
    setDownbeatSec(downbeatSample / sampleRate)
  }, [downbeatSample, sampleRate])

  // Auto-run analysis on song load.
  useEffect(() => {
    if (!songTapeId) return
    if (analyzing) return
    if (beatsSeconds.length > 0) return
    const buf = getBuffer(songTapeId)
    if (!buf) return
    setAnalyzing(true)
    analyzeBpm(buf)
      .then((res) => {
        setBeats(res.beatsSeconds, res.bpm)
        // Seed downbeat to the first detected beat so the grid is musical by default.
        if (res.beatsSeconds.length > 0) {
          setDownbeatSample(Math.round(res.beatsSeconds[0] * sampleRate))
        }
      })
      .catch((err) => console.error("BPM analyze failed", err))
      .finally(() => setAnalyzing(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [songTapeId])

  const applyBpm = () => {
    const v = parseFloat(bpmText)
    if (Number.isFinite(v) && v > 20 && v < 400) setBpm(v)
    else setBpmText(bpm.toFixed(1))
  }

  const applyDownbeat = () => {
    if (Number.isFinite(downbeatSec) && downbeatSec >= 0) {
      setDownbeatSample(Math.round(downbeatSec * sampleRate))
    } else {
      setDownbeatSec(downbeatSample / sampleRate)
    }
  }

  const disabled = !songTapeId

  return (
    <div className="bg-card/60 flex flex-wrap items-center gap-3 border-b px-4 py-2 backdrop-blur">
      <Button size="sm" variant="outline" onClick={onUploadClick}>
        <HugeiconsIcon icon={Upload04Icon} size={14} />
        New song
      </Button>

      <div className="bg-border h-4 w-px" />

      <label className="flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">BPM</span>
        <input
          type="number"
          step="0.1"
          min={20}
          max={400}
          value={bpmText}
          onChange={(e) => setBpmText(e.target.value)}
          onBlur={applyBpm}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur()
          }}
          disabled={disabled}
          className={cn(
            "w-20 rounded-md border bg-background px-2 py-1 text-sm tabular-nums outline-none",
            "focus-visible:border-ring",
          )}
        />
        <Button
          size="xs"
          variant="outline"
          onClick={() => setBpm(bpm / 2)}
          disabled={disabled}
          title="Halve BPM"
        >
          {"\u00F72"}
        </Button>
        <Button
          size="xs"
          variant="outline"
          onClick={() => setBpm(bpm * 2)}
          disabled={disabled}
          title="Double BPM"
        >
          {"\u00D72"}
        </Button>
        {analyzing && (
          <span className="text-muted-foreground text-xs">
            {"analyzing\u2026"}
          </span>
        )}
      </label>

      <div className="bg-border h-4 w-px" />

      <label className="flex items-center gap-2 text-xs">
        <span className="text-muted-foreground">Downbeat (s)</span>
        <input
          type="number"
          step="0.001"
          min={0}
          value={Number.isFinite(downbeatSec) ? downbeatSec.toFixed(3) : "0"}
          onChange={(e) => setDownbeatSec(parseFloat(e.target.value))}
          onBlur={applyDownbeat}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur()
          }}
          disabled={disabled}
          className="w-20 rounded-md border bg-background px-2 py-1 text-sm tabular-nums outline-none focus-visible:border-ring"
        />
      </label>

      <div className="bg-border h-4 w-px" />

      <div className="flex items-center gap-1">
        <span className="text-muted-foreground mr-1 text-xs">Loop</span>
        {BARS_CHOICES.map((b) => (
          <Button
            key={b}
            size="xs"
            variant={defaultCutBars === b ? "default" : "outline"}
            onClick={() => setDefaultCutBars(b as Bars)}
            disabled={disabled}
          >
            {b}
          </Button>
        ))}
        <span className="text-muted-foreground ml-1 text-xs">bars</span>
      </div>

      <div className="bg-border h-4 w-px" />

      <Button
        size="sm"
        variant={knifeActive ? "default" : "outline"}
        onClick={() => setKnifeActive(!knifeActive)}
        disabled={disabled}
      >
        <HugeiconsIcon icon={Scissor01Icon} size={14} />
        {knifeActive ? "Knife on" : "Knife"}
      </Button>

      <div className="flex-1" />

      <Button
        size="sm"
        variant="default"
        onClick={onExportClick}
        disabled={!canExport}
        title={canExport ? "Download sample pack" : "Create cuts first"}
      >
        <HugeiconsIcon icon={Download04Icon} size={14} />
        Export pack
      </Button>
    </div>
  )
}

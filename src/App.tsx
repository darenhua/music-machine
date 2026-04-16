import { useState } from "react"
import { useProjectStore } from "@/state/useProjectStore"
import { BpmBar } from "@/components/BpmBar"
import { SubtractPanel } from "@/components/SubtractPanel"
import { TapeList } from "@/components/TapeList"
import { Upload } from "@/components/Upload"
import { buildPackZip, downloadBlob } from "@/lib/audio/pack"
import { deleteBuffer } from "@/lib/audio/bufferStore"

export function App() {
  const songTapeId = useProjectStore((s) => s.songTapeId)
  const tapes = useProjectStore((s) => s.tapes)
  const bpm = useProjectStore((s) => s.bpm)
  const reset = useProjectStore((s) => s.reset)
  const [knifeActive, setKnifeActive] = useState(false)
  const [exporting, setExporting] = useState(false)

  const exportableTapes = tapes.filter((t) => t.kind !== "source")
  const canExport = exportableTapes.length > 0

  const handleExport = async () => {
    if (!canExport || exporting) return
    setExporting(true)
    try {
      const packName = `MusicMachine_${Math.round(bpm)}bpm_${new Date()
        .toISOString()
        .slice(0, 10)}`
      const blob = await buildPackZip({
        packName,
        bpm,
        tapes: exportableTapes,
      })
      downloadBlob(blob, `${packName}.zip`)
    } catch (e) {
      console.error("Export failed", e)
    } finally {
      setExporting(false)
    }
  }

  const handleNewSong = () => {
    // Revoke every buffer/URL, reset store, and show the upload hero again.
    for (const t of tapes) deleteBuffer(t.bufferId)
    reset()
    setKnifeActive(false)
  }

  return (
    <div className="bg-background text-foreground flex min-h-svh flex-col">
      <header className="flex items-center gap-3 border-b px-4 py-3">
        <div className="bg-primary size-6 rounded-md" />
        <div className="flex flex-col">
          <h1 className="text-sm font-semibold leading-none">Music Machine</h1>
          <p className="text-muted-foreground text-[11px]">
            subtractive stem extractor
          </p>
        </div>
        <div className="flex-1" />
        <span className="text-muted-foreground font-mono text-[11px]">
          press <kbd className="rounded border px-1">d</kbd> for dark mode
        </span>
      </header>

      {songTapeId && (
        <BpmBar
          knifeActive={knifeActive}
          setKnifeActive={setKnifeActive}
          onUploadClick={handleNewSong}
          onExportClick={handleExport}
          canExport={canExport && !exporting}
        />
      )}

      {songTapeId ? (
        <TapeList knifeActive={knifeActive} />
      ) : (
        <Upload />
      )}

      {songTapeId && <SubtractPanel />}
    </div>
  )
}

export default App

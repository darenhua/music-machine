import { useCallback, useRef, useState } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { Upload04Icon } from "@hugeicons/core-free-icons"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { decodeFile } from "@/lib/audio/decode"
import { newTapeId, setBlobUrl, setBuffer } from "@/lib/audio/bufferStore"
import { useProjectStore } from "@/state/useProjectStore"

export function Upload() {
  const [drag, setDrag] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const addTape = useProjectStore((s) => s.addTape)
  const setSong = useProjectStore((s) => s.setSong)

  const ingest = useCallback(
    async (file: File) => {
      setError(null)
      setBusy(true)
      try {
        const buffer = await decodeFile(file)
        const id = newTapeId("song")
        setBuffer(id, buffer)
        // Song tape renders from the original file for fast preview.
        setBlobUrl(id, URL.createObjectURL(file))
        addTape({
          id,
          name: stripExtension(file.name),
          category: "other",
          kind: "source",
          bufferId: id,
          sampleRate: buffer.sampleRate,
          channels: buffer.numberOfChannels,
          muted: false,
          gainDb: 0,
        })
        setSong(id, buffer.length)
      } catch (e) {
        console.error(e)
        setError(
          e instanceof Error ? e.message : "Could not decode that file.",
        )
      } finally {
        setBusy(false)
      }
    },
    [addTape, setSong],
  )

  const onDrop = useCallback(
    async (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault()
      setDrag(false)
      const file = e.dataTransfer.files[0]
      if (file) await ingest(file)
    },
    [ingest],
  )

  return (
    <div
      className={cn(
        "flex flex-1 items-center justify-center px-6 py-12",
        "transition-colors",
      )}
      onDragOver={(e) => {
        e.preventDefault()
        setDrag(true)
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
    >
      <div
        className={cn(
          "border-border bg-card flex w-full max-w-xl flex-col items-center gap-5 rounded-2xl border-2 border-dashed px-10 py-16 text-center",
          drag && "border-ring bg-muted",
          busy && "opacity-60",
        )}
      >
        <div className="bg-muted text-muted-foreground flex size-12 items-center justify-center rounded-xl">
          <HugeiconsIcon icon={Upload04Icon} size={22} />
        </div>
        <div className="space-y-1">
          <h2 className="text-base font-medium">Drop a song to start</h2>
          <p className="text-muted-foreground text-sm">
            MP3, WAV, FLAC, or M4A. We&apos;ll decode, resample to 44.1 kHz, and
            show the waveform.
          </p>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="audio/*"
          className="hidden"
          onChange={async (e) => {
            const f = e.target.files?.[0]
            if (f) await ingest(f)
            if (inputRef.current) inputRef.current.value = ""
          }}
        />
        <Button
          variant="default"
          size="lg"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          {busy ? "Decoding\u2026" : "Choose a file"}
        </Button>
        {error && <p className="text-destructive text-xs">{error}</p>}
      </div>
    </div>
  )
}

function stripExtension(name: string): string {
  return name.replace(/\.[^.]+$/, "")
}

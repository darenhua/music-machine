import { useMemo } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { MinusSignIcon, Target01Icon } from "@hugeicons/core-free-icons"
import { Button } from "@/components/ui/button"
import {
  getBuffer,
  newTapeId,
  setBuffer,
} from "@/lib/audio/bufferStore"
import { subtract } from "@/lib/audio/subtract"
import { useProjectStore } from "@/state/useProjectStore"

export function SubtractPanel() {
  const tapes = useProjectStore((s) => s.tapes)
  const targetId = useProjectStore((s) => s.selectedTargetId)
  const subIds = useProjectStore((s) => s.selectedSubtrahendIds)
  const addTape = useProjectStore((s) => s.addTape)
  const clearSelection = useProjectStore((s) => s.clearSelection)

  const target = useMemo(
    () => (targetId ? tapes.find((t) => t.id === targetId) ?? null : null),
    [tapes, targetId],
  )
  const subs = useMemo(
    () => tapes.filter((t) => subIds.includes(t.id)),
    [tapes, subIds],
  )

  if (!target) return null

  const canRun = subs.length > 0

  const run = () => {
    const tgtBuf = getBuffer(target.bufferId)
    if (!tgtBuf) return
    const subBufs = subs
      .map((t) => getBuffer(t.bufferId))
      .filter((b): b is AudioBuffer => b != null)
    if (subBufs.length === 0) return
    let result: AudioBuffer
    try {
      result = subtract(tgtBuf, subBufs)
    } catch (e) {
      console.error(e)
      return
    }
    const id = newTapeId("sub")
    setBuffer(id, result)
    addTape({
      id,
      name: `${target.name} − ${subs.map((s) => s.name).join(" − ")}`,
      category: target.category,
      kind: "derived",
      bufferId: id,
      sampleRate: result.sampleRate,
      channels: result.numberOfChannels,
      bars: target.bars,
      parents: {
        targetId: target.id,
        subtrahendIds: subs.map((s) => s.id),
      },
      muted: false,
      gainDb: 0,
    })
    clearSelection()
  }

  return (
    <div className="bg-card/95 sticky bottom-0 border-t px-4 py-2 shadow-sm backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 text-xs">
        <span className="text-muted-foreground inline-flex items-center gap-1">
          <HugeiconsIcon icon={Target01Icon} size={12} /> target:
        </span>
        <span className="bg-primary/15 text-primary rounded-md px-2 py-0.5 font-medium">
          {target.name}
        </span>
        <span className="text-muted-foreground">−</span>
        {subs.length === 0 ? (
          <span className="text-muted-foreground italic">
            pick one or more tapes to subtract
          </span>
        ) : (
          subs.map((s) => (
            <span
              key={s.id}
              className="bg-destructive/15 text-destructive rounded-md px-2 py-0.5 font-medium"
            >
              {s.name}
            </span>
          ))
        )}
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={clearSelection}>
          Cancel
        </Button>
        <Button size="sm" onClick={run} disabled={!canRun}>
          <HugeiconsIcon icon={MinusSignIcon} size={14} />
          Subtract
        </Button>
      </div>
    </div>
  )
}

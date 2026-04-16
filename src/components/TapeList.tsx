import { useProjectStore } from "@/state/useProjectStore"
import { TapeRow } from "./TapeRow"

interface Props {
  knifeActive: boolean
}

export function TapeList({ knifeActive }: Props) {
  const tapes = useProjectStore((s) => s.tapes)
  return (
    <div className="flex-1 overflow-y-auto px-4 pb-4">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 py-4">
        {tapes.map((t) => (
          <TapeRow
            key={t.id}
            tape={t}
            knifeActive={knifeActive && t.kind === "source"}
          />
        ))}
      </div>
    </div>
  )
}

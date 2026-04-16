import { useEffect, useRef, useState } from "react"
import WaveSurfer, { type WaveSurferOptions } from "wavesurfer.js"

type Options = Omit<WaveSurferOptions, "container" | "url">

// StrictMode-safe wrapper: cleans up the instance on unmount and re-creates
// on the next mount. Re-creates the instance when waveColor/height/etc. change
// is intentionally NOT supported to avoid thrashing — pass stable options.
export function useWaveSurfer(
  containerRef: React.RefObject<HTMLDivElement | null>,
  options: Options,
  url: string | null,
): { ws: WaveSurfer | null; ready: boolean } {
  const optionsRef = useRef(options)
  const [ws, setWs] = useState<WaveSurfer | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    optionsRef.current = options
  })

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const instance = WaveSurfer.create({
      container: el,
      ...optionsRef.current,
    })
    const onReady = () => setReady(true)
    const onLoading = () => setReady(false)
    const onDestroy = () => setReady(false)
    instance.on("ready", onReady)
    instance.on("loading", onLoading)
    instance.on("destroy", onDestroy)
    setWs(instance)
    return () => {
      instance.un("ready", onReady)
      instance.un("loading", onLoading)
      instance.un("destroy", onDestroy)
      instance.destroy()
      setWs(null)
    }
  }, [containerRef])

  useEffect(() => {
    if (!ws || !url) return
    ws.load(url).catch((err) => {
      // Swallow AbortError on unmount or URL swap.
      if (!(err instanceof Error) || err.name !== "AbortError") {
        console.error("wavesurfer load failed", err)
      }
    })
  }, [ws, url])

  return { ws, ready }
}

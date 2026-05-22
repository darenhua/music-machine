#!/usr/bin/env python
"""External worker for the DJ-set helper pipeline.

Run this in a separate terminal alongside the marimo notebook:

    uv run python experiments/small/scripts/worker.py

It polls a JSON job queue at `experiments/small/jobs.json`, dispatches
QUEUED jobs to the backend modules in `scripts/library/`, and writes
status / errors / results back into the same file. The marimo notebook
is the producer; this worker is the only consumer.

All queue I/O goes through `jobs_store` (sibling module) — single source
of truth for the `fcntl.flock` + atomic-rename pattern. Multiple workers
in parallel terminals are supported because every claim acquires the
exclusive lock.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Locate scripts/ + scripts/library/ relative to this file and add to
# sys.path so the backend modules import the same way they do from the
# notebook, and so `jobs_store` (sibling of this file) is importable.
_HERE = Path(__file__).resolve().parent
_LIB = _HERE / "library"
for _p in (_HERE, _LIB):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import audio_analyzer  # noqa: E402
import jobs_store  # noqa: E402
import metadata_store  # noqa: E402
import stem_splitter  # noqa: E402
import youtube_fetcher  # noqa: E402

DEFAULT_JOBS_FILE = _HERE.parent / "jobs.json"
DEFAULT_DOWNLOADS = _HERE.parent / "downloads"
DEFAULT_METADATA_FILE = _HERE.parent / "metadata.json"

_shutdown = False  # flipped by SIGINT/SIGTERM


# ---------------------------------------------------------------------------
# Dispatch — call into the backend modules.
# ---------------------------------------------------------------------------


def dispatch(
    kind: str,
    target_id: str,
    downloads_dir: Path,
    metadata_file: Path,
):
    """Resolve the on-disk filename for download/split via metadata.json
    so renamed files (or new title-named downloads) still work. Falls
    back to `{video_id}.mp3` when no metadata entry exists, preserving
    behavior for legacy un-renamed files."""
    if kind == "download":
        filename = metadata_store.resolve_filename(metadata_file, target_id)
        return youtube_fetcher.download_mp3(
            target_id, downloads_dir, filename=filename
        )
    if kind == "split":
        filename = metadata_store.resolve_filename(metadata_file, target_id)
        mp3 = downloads_dir / filename
        return stem_splitter.split_stems(mp3, downloads_dir)
    if kind == "analyze":
        # target_id is the absolute file path as a string.
        return audio_analyzer.analyze(Path(target_id))
    raise ValueError(f"unknown job kind: {kind!r}")


# ---------------------------------------------------------------------------
# Logging + signal handling.
# ---------------------------------------------------------------------------


def _iso(ts: float | None = None) -> str:
    if ts is None:
        ts = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _log(msg: str, *, quiet: bool, pid: int) -> None:
    if quiet:
        return
    print(f"[{_iso()}] [pid {pid}] {msg}", flush=True)


def _handle_signal(signum, frame):  # noqa: ARG001
    global _shutdown
    _shutdown = True


# ---------------------------------------------------------------------------
# Heartbeat daemon — keeps the worker visibly alive in jobs.json during
# long-running dispatch calls. A 60-90 s stem split would otherwise leave
# the main loop unable to write its heartbeat, and the notebook's 5 s
# staleness threshold would falsely report the worker as dead.
# ---------------------------------------------------------------------------


def _heartbeat_loop(
    jobs_file: Path,
    pid: int,
    started_at: float,
    stop_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        try:
            jobs_store.mutate(
                jobs_file,
                lambda s: jobs_store.heartbeat_in(s, pid, started_at),
            )
        except OSError:
            # Disk full, lock file unwritable, etc. — keep trying.
            pass
        # `wait(timeout)` is interruptible by stop_event.set().
        stop_event.wait(1.0)


# ---------------------------------------------------------------------------
# Main loop.
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "External worker for the DJ-set helper pipeline. Polls the "
            "JSON job queue at experiments/small/jobs.json and dispatches "
            "download / split / analyze jobs. Run in a separate terminal "
            "from the marimo notebook."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_JOBS_FILE,
        help=(
            "Path to the JSON jobs file (default: "
            f"{DEFAULT_JOBS_FILE}). Flag kept as --db for backwards "
            "compatibility; accepts any path."
        ),
    )
    parser.add_argument(
        "--downloads",
        type=Path,
        default=DEFAULT_DOWNLOADS,
        help=(
            "Path to the downloads dir where MP3s + stems live "
            f"(default: {DEFAULT_DOWNLOADS})."
        ),
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=DEFAULT_METADATA_FILE,
        help=(
            "Path to metadata.json — used to resolve video_id → current "
            f"on-disk filename for download/split (default: "
            f"{DEFAULT_METADATA_FILE})."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds to sleep between empty-queue polls (default: 1.0).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-job stdout logs (heartbeat still runs).",
    )
    args = parser.parse_args()

    jobs_file: Path = args.db
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    args.downloads.mkdir(parents=True, exist_ok=True)

    pid = os.getpid()
    started_at = time.time()
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Bootstrap an empty state file if missing, so the notebook's first
    # lock-free read doesn't return an empty dict and then race with us
    # creating the file.
    jobs_store.mutate(jobs_file, lambda s: None)

    # Start the heartbeat daemon so long-running dispatch calls don't
    # starve the workers-table updates.
    stop_event = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(jobs_file, pid, started_at, stop_event),
        daemon=True,
        name=f"hb-{pid}",
    )
    hb_thread.start()

    _log(
        f"worker online · jobs_file={jobs_file} "
        f"downloads={args.downloads} interval={args.interval}s",
        quiet=args.quiet,
        pid=pid,
    )

    try:
        while not _shutdown:
            try:
                claim = jobs_store.mutate(
                    jobs_file, lambda s: jobs_store.claim_one_in(s, pid)
                )
            except OSError as exc:
                _log(
                    f"queue read failed: {exc!r}; sleeping",
                    quiet=args.quiet,
                    pid=pid,
                )
                time.sleep(args.interval)
                continue

            if claim is None:
                time.sleep(args.interval)
                continue

            job_id = claim["job_id"]
            kind = claim["kind"]
            target_id = claim["target_id"]
            _log(
                f"picked job {job_id} kind={kind} target={target_id}",
                quiet=args.quiet,
                pid=pid,
            )
            t0 = time.time()
            try:
                result = dispatch(
                    kind, target_id, args.downloads, args.metadata
                )
                # Derive cascades from the *raw* result (Path objects
                # intact for `download`/`split`) before we serialize for
                # storage. download_mp3 returns the final Path, so the
                # analyse cascade uses the actual filename — works
                # whether the file is `{video_id}.mp3` or the title.
                cascades = jobs_store.derive_cascades(
                    kind, target_id, result, args.downloads
                )
                serialized = jobs_store.serialize_result(result)
                jobs_store.mutate(
                    jobs_file,
                    lambda s, _id=job_id, _r=serialized, _c=cascades: jobs_store.finalize_done_in(
                        s, _id, _r, _c
                    ),
                )
                dt = time.time() - t0
                cascade_msg = (
                    f" → cascaded {len(cascades)} job(s)" if cascades else ""
                )
                _log(
                    f"  done in {dt:.1f}s{cascade_msg}",
                    quiet=args.quiet,
                    pid=pid,
                )
            except Exception as exc:  # noqa: BLE001 — surface backend errors
                err_text = f"{type(exc).__name__}: {exc}"
                try:
                    jobs_store.mutate(
                        jobs_file,
                        lambda s, _id=job_id, _e=err_text: jobs_store.finalize_error_in(
                            s, _id, _e
                        ),
                    )
                except OSError:
                    # Last-ditch: if we can't even write the error, the
                    # job stays RUNNING. The next worker / force-retry
                    # will reconcile.
                    pass
                _log(f"  error: {err_text}", quiet=args.quiet, pid=pid)
    finally:
        stop_event.set()
        try:
            hb_thread.join(timeout=2.0)
        except Exception:
            pass
        try:
            jobs_store.mutate(
                jobs_file, lambda s: jobs_store.remove_worker_in(s, pid)
            )
        except OSError:
            pass
        _log("worker exiting", quiet=args.quiet, pid=pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())

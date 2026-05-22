"""Shared JSON-file job queue primitives.

Both the marimo notebook (producer + reader) and `scripts/worker.py`
(consumer) import this module. It's the single source of truth for the
`fcntl.flock` + atomic-rename pattern that serializes mutations across
the two processes — without it, the lock/rename code would be duplicated
in two places and could drift out of sync.

Storage shape (`experiments/small/jobs.json`):

    {
      "jobs": {
        "<uuid>": {
          "kind": "download"|"split"|"analyze",
          "target_id": "<video_id-or-absolute-path>",
          "status": "QUEUED"|"RUNNING"|"DONE"|"ERROR",
          "queued_at": <epoch>,
          "started_at": <epoch | null>,
          "finished_at": <epoch | null>,
          "error": "<string | null>",
          "result": <JSON-serializable | null>,
          "worker_pid": <int | null>
        }
      },
      "workers": {
        "<pid>": {"started_at": <epoch>, "last_heartbeat": <epoch>}
      }
    }

Concurrency contract:

- Writes call `mutate(jobs_file, fn)` which acquires `fcntl.flock(LOCK_EX)`
  on a sibling `.lock` sentinel file (the lock is held on a stable inode,
  so the atomic-rename of the data file doesn't invalidate it), loads the
  current state, hands it to `fn` for in-place mutation, writes a fresh
  `.tmp`, `fsync`s, then `os.replace`s over the live file. The atomic
  rename guarantees any lock-free reader sees either the pre-write or
  post-write state — never a torn write.

- Reads call `load(jobs_file)` — no lock. Eventually consistent within
  the notebook's 1 s polling cadence; fine for the UI.

- The first `mutate` from any process creates `jobs.json` (and its
  parent dir) if missing. Two simultaneously-starting workers will both
  acquire the lock in turn and both write the empty state; result is the
  same regardless of order. No `open(path, "x")` race-window needed.

- A corrupt/empty `jobs.json` (e.g. from a power loss before atomic
  rename was wired up) is handled by `load`: `json.load` raises, we
  catch, return `empty_state()`. The next write replaces the bad file
  with a valid one.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

STEM_NAMES: tuple[str, ...] = ("vocals", "drums", "bass", "other")

JobsState = dict[str, Any]


# ---------------------------------------------------------------------------
# Lock + atomic-rename primitives.
# ---------------------------------------------------------------------------


def empty_state() -> JobsState:
    return {"jobs": {}, "workers": {}}


def load(jobs_file: Path) -> JobsState:
    """Lock-free read. Returns `empty_state()` if the file is missing or
    unparseable — callers needing transactional consistency must use
    `mutate` instead."""
    if not jobs_file.exists():
        return empty_state()
    try:
        with jobs_file.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    data.setdefault("jobs", {})
    data.setdefault("workers", {})
    return data


def mutate(jobs_file: Path, fn: Callable[[JobsState], Any]) -> Any:
    """Acquire `LOCK_EX` on `<jobs_file>.lock`, load the current state,
    pass it to `fn` for in-place mutation, write a temp file, fsync it,
    atomic-rename over the data file, release the lock. Returns whatever
    `fn` returned, so callers can read back e.g. a claimed job id."""
    jobs_file = Path(jobs_file)
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = jobs_file.with_suffix(jobs_file.suffix + ".lock")
    tmp_path = jobs_file.with_suffix(jobs_file.suffix + ".tmp")

    # `a+` creates the lock file if missing and doesn't truncate it if
    # present. The lock file is a stable sentinel — we never rename or
    # delete it, so holding flock on its fd is safe across the
    # `os.replace` of the data file.
    with lock_path.open("a+") as lockfp:
        fcntl.flock(lockfp.fileno(), fcntl.LOCK_EX)
        try:
            state = load(jobs_file)
            result = fn(state)
            with tmp_path.open("w", encoding="utf-8") as fp:
                json.dump(state, fp, indent=2, sort_keys=True)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_path, jobs_file)
            return result
        finally:
            fcntl.flock(lockfp.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# In-place mutators. These are pure-ish: they take a state dict and
# mutate it in place. Callers run them inside `mutate(jobs_file, fn)` so
# the I/O + locking is handled centrally.
# ---------------------------------------------------------------------------


def _latest_job_for(
    state: JobsState, kind: str, target_id: str
) -> tuple[str, dict] | None:
    """Return `(job_id, job_dict)` for the most-recently-queued job
    matching `(kind, target_id)`, or `None`."""
    candidates = [
        (jid, j)
        for jid, j in state["jobs"].items()
        if j.get("kind") == kind and j.get("target_id") == target_id
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda kv: kv[1].get("queued_at") or 0.0, reverse=True
    )
    return candidates[0]


def _done_skip_ok(
    kind: str, target_id: str, downloads_dir: Path | None
) -> bool:
    """Return True if a `DONE` job for this `(kind, target_id)` should
    short-circuit the enqueue (i.e. the on-disk artifact still exists).

    For `download` / `split` we check both `{video_id}.mp3` and any
    matching title-named file (any non-stems mp3 in `downloads/` whose
    sibling stems dir contains the 4 stems). False-negatives here are
    harmless — they just cause one extra worker round trip, where the
    backend modules will then idempotent-skip on their own.
    """
    if downloads_dir is None:
        # Caller didn't pass a downloads_dir → fall back to DONE-means-skip.
        # Safe for analyze; download/split callers always pass it.
        return True
    if kind == "download":
        # Conservative: skip only if the canonical {video_id}.mp3 exists.
        # Renamed / title-named files are an explicit user action; we
        # leave the skip to the worker side via the file-exists check
        # inside `youtube_fetcher.download_mp3`.
        return (downloads_dir / f"{target_id}.mp3").exists()
    if kind == "split":
        # Stems folder is named after the source mp3's stem, which may
        # be the video_id (legacy) or the YouTube title (new). Probe
        # the video_id path first as the cheap path.
        legacy_dir = downloads_dir / target_id
        if all(
            (legacy_dir / f"{n}.mp3").exists() for n in STEM_NAMES
        ):
            return True
        return False
    # analyze: DONE alone is sufficient (result lives in jobs.result).
    return True


def enqueue_job_in(
    state: JobsState,
    kind: str,
    target_id: str,
    force: bool = False,
    downloads_dir: Path | None = None,
) -> dict:
    """Idempotent enqueue. Inserts a `QUEUED` job into `state["jobs"]`
    unless an existing job for `(kind, target_id)` short-circuits us:

    - `QUEUED` / `RUNNING` → skip, return existing summary
      (`action == "skip-active"`)
    - `DONE` + on-disk artifact still present → skip
      (`action == "skip-done"`)
    - `force=True` bypasses both

    Returns a dict the caller can hand back to the UI:
        {job_id, kind, target_id, status, action}

    where `action` is one of `"inserted"`, `"skip-active"`,
    `"skip-done"`.
    """
    if not force:
        existing = _latest_job_for(state, kind, target_id)
        if existing is not None:
            exist_jid, exist_job = existing
            exist_status = exist_job.get("status")
            if exist_status in ("QUEUED", "RUNNING"):
                return {
                    "job_id": exist_jid,
                    "kind": kind,
                    "target_id": target_id,
                    "status": exist_status,
                    "action": "skip-active",
                }
            if exist_status == "DONE" and _done_skip_ok(
                kind, target_id, downloads_dir
            ):
                return {
                    "job_id": exist_jid,
                    "kind": kind,
                    "target_id": target_id,
                    "status": "DONE",
                    "action": "skip-done",
                }

    new_id = uuid.uuid4().hex
    state["jobs"][new_id] = {
        "kind": kind,
        "target_id": target_id,
        "status": "QUEUED",
        "queued_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
        "worker_pid": None,
    }
    return {
        "job_id": new_id,
        "kind": kind,
        "target_id": target_id,
        "status": "QUEUED",
        "action": "inserted",
    }


_KIND_PRIORITY: dict[str, int] = {"download": 0, "split": 0, "analyze": 1}


def claim_one_in(state: JobsState, pid: int) -> dict | None:
    """Atomically claim the next `QUEUED` job for worker `pid`. Flips
    its status to `RUNNING`, stamps `started_at` + `worker_pid`. Returns
    a dict with `{job_id, kind, target_id, started_at, worker_pid}` or
    `None` if the queue is empty.

    Selection order: `download`/`split` jobs are claimed before `analyze`
    jobs (priority 0 vs 1); within a priority tier, oldest `queued_at`
    wins. This keeps the user-blocking fetch+split pipeline draining
    even when a backlog of analyze jobs (cheap, runs on already-local
    files) has been cascade-enqueued ahead of newer downloads.
    """
    queued = [
        (jid, j)
        for jid, j in state["jobs"].items()
        if j.get("status") == "QUEUED"
    ]
    if not queued:
        return None

    # Dedupe: for any (kind, target_id) with multiple QUEUED rows, only
    # the newest is eligible — the rest are marked DONE in this same
    # transaction with no dispatch and no cascade. This prevents the
    # queue from getting stuck draining redundant work (e.g. dozens of
    # duplicate `split` jobs that all short-circuit because the stems
    # already exist on disk, each one re-cascading 4 analyze jobs).
    latest: dict[tuple[str, str], tuple[str, dict]] = {}
    superseded: list[tuple[str, dict, str]] = []  # (loser_jid, loser_job, winner_jid)
    for jid, j in queued:
        key = (j.get("kind"), j.get("target_id"))
        existing = latest.get(key)
        if existing is None:
            latest[key] = (jid, j)
            continue
        exist_jid, exist_job = existing
        if (j.get("queued_at") or 0.0) > (exist_job.get("queued_at") or 0.0):
            superseded.append((exist_jid, exist_job, jid))
            latest[key] = (jid, j)
        else:
            superseded.append((jid, j, exist_jid))
    now = time.time()
    for _loser_jid, loser_job, winner_jid in superseded:
        loser_job["status"] = "DONE"
        loser_job["finished_at"] = now
        loser_job["result"] = {"superseded_by": winner_jid}

    eligible = list(latest.values())
    eligible.sort(
        key=lambda kv: (
            _KIND_PRIORITY.get(kv[1].get("kind"), 2),
            kv[1].get("queued_at") or 0.0,
        )
    )
    job_id, job = eligible[0]
    job["status"] = "RUNNING"
    job["started_at"] = time.time()
    job["worker_pid"] = pid
    return {
        "job_id": job_id,
        "kind": job["kind"],
        "target_id": job["target_id"],
        "started_at": job["started_at"],
        "worker_pid": pid,
    }


def finalize_done_in(
    state: JobsState,
    job_id: str,
    result: Any,
    cascades: list[tuple[str, str]],
) -> None:
    """Mark `job_id` as DONE with the (already-serialized) `result`, and
    insert each cascade `(kind, target_id)` as a new QUEUED job."""
    job = state["jobs"].get(job_id)
    if job is None:
        return
    job["status"] = "DONE"
    job["finished_at"] = time.time()
    job["result"] = result  # caller pre-serialized via serialize_result()
    now = time.time()
    for c_kind, c_target in cascades:
        new_id = uuid.uuid4().hex
        state["jobs"][new_id] = {
            "kind": c_kind,
            "target_id": c_target,
            "status": "QUEUED",
            "queued_at": now,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "result": None,
            "worker_pid": None,
        }


def finalize_error_in(state: JobsState, job_id: str, err_text: str) -> None:
    job = state["jobs"].get(job_id)
    if job is None:
        return
    job["status"] = "ERROR"
    job["finished_at"] = time.time()
    job["error"] = err_text


def heartbeat_in(state: JobsState, pid: int, started_at: float) -> None:
    state["workers"][str(pid)] = {
        "started_at": started_at,
        "last_heartbeat": time.time(),
    }


def remove_worker_in(state: JobsState, pid: int) -> None:
    state["workers"].pop(str(pid), None)


# ---------------------------------------------------------------------------
# Read-side helpers (lock-free) — same shapes the Library cell consumes.
# ---------------------------------------------------------------------------


def snapshot_jobs(jobs_file: Path) -> dict[tuple[str, str], dict]:
    """`{(kind, target_id): {status, queued_at, …, result}}`. Latest job
    per `(kind, target_id)` wins so force-retries reflect immediately."""
    state = load(jobs_file)
    items = sorted(
        state["jobs"].items(),
        key=lambda kv: kv[1].get("queued_at") or 0.0,
    )
    out: dict[tuple[str, str], dict] = {}
    for job_id, job in items:
        kind = job.get("kind")
        target_id = job.get("target_id")
        if kind is None or target_id is None:
            continue
        out[(kind, target_id)] = {
            "job_id": job_id,
            "kind": kind,
            "target_id": target_id,
            "status": job.get("status"),
            "queued_at": job.get("queued_at"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "error": job.get("error"),
            "result": job.get("result"),
            "worker_pid": job.get("worker_pid"),
        }
    return out


def snapshot_analysis(jobs_file: Path) -> dict[str, dict]:
    """`{file_path_str: {bpm, key, camelot}}` from `DONE` analyze jobs.
    Latest by `finished_at` wins."""
    state = load(jobs_file)
    analyze_jobs = [
        j
        for j in state["jobs"].values()
        if j.get("kind") == "analyze"
        and j.get("status") == "DONE"
        and isinstance(j.get("result"), dict)
    ]
    analyze_jobs.sort(key=lambda j: j.get("finished_at") or 0.0)
    out: dict[str, dict] = {}
    for j in analyze_jobs:
        target_id = j.get("target_id")
        if target_id is None:
            continue
        out[target_id] = j["result"]
    return out


def live_workers(jobs_file: Path, *, stale_after_s: float = 5.0) -> list[dict]:
    """Lock-free read of the `workers` table. Returns workers whose
    `last_heartbeat` is fresher than `now - stale_after_s`, sorted by
    `started_at` ASC."""
    state = load(jobs_file)
    now = time.time()
    live: list[dict] = []
    for pid_str, info in state.get("workers", {}).items():
        if not isinstance(info, dict):
            continue
        last_hb = info.get("last_heartbeat") or 0.0
        if last_hb > now - stale_after_s:
            live.append(
                {
                    "pid": pid_str,
                    "started_at": info.get("started_at") or now,
                    "last_heartbeat": last_hb,
                }
            )
    live.sort(key=lambda r: r["started_at"])
    return live


# ---------------------------------------------------------------------------
# Result serialization + cascade derivation (worker.py helpers, but
# exposed here so callers can preview cascade behavior in tests).
# ---------------------------------------------------------------------------


def serialize_result(result: Any) -> Any:
    """Convert a backend return value into a JSON-serializable shape.
    Path → str; dict-of-paths → dict-of-strs; analyze's plain dict
    passes through (its values are already JSON-friendly scalars)."""
    if isinstance(result, Path):
        return str(result)
    if isinstance(result, dict):
        out: dict = {}
        for k, v in result.items():
            if isinstance(v, Path):
                out[k] = str(v)
            elif isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
            else:
                out[k] = str(v)
        return out
    return result


def derive_cascades(
    kind: str, target_id: str, result: Any, downloads_dir: Path
) -> list[tuple[str, str]]:
    """Inspect a just-completed job's result and return the
    `(kind, target_id)` tuples that should be inserted as follow-up
    QUEUED rows. Cascade rules: `download` → `split` + `analyze`(source
    mp3); `split` → `analyze` for each stem path; `analyze` → nothing.

    IMPORTANT: callers must pass the *raw* result here (with `Path`
    objects intact for `download` and `split`), then serialize
    separately for storage. The `download` cascade uses `result` (the
    returned Path) instead of `{video_id}.mp3` so it resolves correctly
    when the file was downloaded under its YouTube title.
    """
    cascades: list[tuple[str, str]] = []
    if kind == "download":
        cascades.append(("split", target_id))
        src_path = (
            result
            if isinstance(result, Path)
            else (downloads_dir / f"{target_id}.mp3")
        )
        cascades.append(("analyze", str(src_path)))
    elif kind == "split" and isinstance(result, dict):
        for stem_path in result.values():
            cascades.append(("analyze", str(stem_path)))
    return cascades

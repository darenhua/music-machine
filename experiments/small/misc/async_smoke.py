"""Smoke test for the Cycle 4 Part B async-pipeline design.

Validates two patterns the real notebook will rely on, in 0.23.6:

1. **Worker-thread dict mutation under a lock**, observed from the UI via
   `mo.ui.refresh` polling. Worker threads NEVER call `mo.state` setters.

2. **Main-thread `mo.state` setter** for the instant "queued" feedback when
   a job is enqueued — sub-second response without waiting for the next tick.

**Naming convention note** (caught by this smoke test on first compile):
marimo treats underscore-prefixed names as **cell-local** and silently strips
them from a cell's return tuple. Singletons that need to be referenced from
downstream cells (executor, jobs_dict, jobs_lock) MUST be named without a
leading underscore, otherwise marimo will refuse to export them and the
worker / status cells will hit NameError. This is a real design constraint
for the Part B implementation in `notebook.py`.

Run via:

    uv run marimo edit experiments/small/misc/async_smoke.py

What you should see:

- Press "Enqueue fake job" → "QUEUED" row appears immediately (no lag).
- Within 1 s the row flips to "RUNNING" (worker thread set this).
- After the fake `time.sleep` finishes, the row flips to "DONE".
- All without `mo.state` ever being called from the worker thread.
- Tick at the top of the page advances every 1 s; the table re-renders
  on every tick so worker-thread mutations propagate to the UI.
"""

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import polars as pl
    import threading
    import time
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    return ThreadPoolExecutor, mo, pl, threading, time, uuid


@app.cell
def _(mo):
    mo.md("""
    # Async pipeline smoke test

    Goal: empirically prove the **polling-tick + cross-thread dict mutation
    under a lock** pattern works in marimo 0.23.6 before we use it to
    rearchitect `notebook.py`.

    Specifically: a worker thread mutates a Python dict (under a
    `threading.Lock`) and **never** calls any `mo.state` setter. The Library
    cell subscribes to `mo.ui.refresh` and re-reads the dict on each tick.

    Press **Enqueue fake job** below and watch the status table:

    1. `QUEUED` should appear ~instantly (main-thread `set_jobs_version` bump).
    2. Within 1 s, the row should flip to `RUNNING` (worker-thread mutation,
       picked up by the next polling tick).
    3. After the fake 3 s sleep, it should flip to `DONE`.
    """)
    return


@app.cell
def _(ThreadPoolExecutor, mo, threading):
    # Singletons — NO leading underscore (marimo would treat _name as
    # cell-local and silently strip from the return tuple).
    executor = ThreadPoolExecutor(max_workers=2)
    jobs_dict = {}
    jobs_lock = threading.Lock()

    # State the main thread bumps to force an *immediate* re-render whenever
    # a job is enqueued — avoids the 1 s polling wait for "QUEUED" to flash.
    get_jobs_version, set_jobs_version = mo.state(0)
    return executor, get_jobs_version, jobs_dict, jobs_lock, set_jobs_version


@app.cell
def _(mo):
    # Polling tick. The status cell subscribes to .value so it re-renders
    # every interval — that's how worker-thread mutations become visible.
    tick = mo.ui.refresh(
        options=["500ms", "1s", "2s", "5s"],
        default_interval="1s",
    )
    tick
    return (tick,)


@app.cell
def _(mo):
    enqueue_btn = mo.ui.run_button(
        label="Enqueue fake job (3 s)", kind="success"
    )
    enqueue_btn
    return (enqueue_btn,)


@app.cell
def _(
    enqueue_btn,
    executor,
    jobs_dict,
    jobs_lock,
    mo,
    set_jobs_version,
    threading,
    time,
    uuid,
):
    # This cell fires every time enqueue_btn.value flips (i.e. on each press).
    # If False (initial render), do nothing.
    if enqueue_btn.value:

        def _worker(job_id):
            """Background thread. NEVER calls mo.state setters."""
            with jobs_lock:
                jobs_dict[job_id]["status"] = "RUNNING"
                jobs_dict[job_id]["started_at"] = time.time()
                jobs_dict[job_id]["thread"] = threading.current_thread().name

            time.sleep(3.0)  # pretend this is download/split/analyze

            with jobs_lock:
                jobs_dict[job_id]["status"] = "DONE"
                jobs_dict[job_id]["finished_at"] = time.time()

        _job_id = uuid.uuid4().hex[:8]
        with jobs_lock:
            jobs_dict[_job_id] = {
                "id": _job_id,
                "status": "QUEUED",
                "queued_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "thread": None,
            }
        executor.submit(_worker, _job_id)
        # Main-thread state bump → downstream cells re-run instantly so the
        # user sees QUEUED before the next polling tick.
        set_jobs_version(time.time_ns())

    mo.md(
        "_Each press of the button submits one fake job to the worker pool. "
        "The status table below polls every 1 s via the **tick** refresh "
        "widget above; the main-thread state bump triggers an extra "
        "re-render on every enqueue so QUEUED appears instantly._"
    )
    return


@app.cell
def _(get_jobs_version, jobs_dict, jobs_lock, mo, pl, tick, time):
    # Subscribe to BOTH signals so we re-render on either trigger:
    # - tick.value advances on polling (catches worker-thread mutations)
    # - get_jobs_version() bumps on every enqueue (instant initial render)
    _tick_val = tick.value
    _version = get_jobs_version()

    # Snapshot the jobs dict under the lock. Worker threads may mutate at
    # any moment; the snapshot gives us a consistent view for this render.
    with jobs_lock:
        _snapshot = [dict(job) for job in jobs_dict.values()]

    if not _snapshot:
        _output = mo.md(
            "_No jobs yet. Press **Enqueue fake job** above._\n\n"
            f"_(tick={_tick_val} · version={_version})_"
        )
    else:
        _now = time.time()
        _rows = []
        for job in sorted(
            _snapshot,
            key=lambda j: j.get("queued_at") or 0,
            reverse=True,
        ):
            _qa = job.get("queued_at") or 0
            _sa = job.get("started_at")
            _fa = job.get("finished_at")
            _rows.append(
                {
                    "id": job["id"],
                    "status": job["status"],
                    "queued (s ago)": f"{_now - _qa:.1f}",
                    "wait → run (s)": f"{(_sa - _qa):.2f}" if _sa else "—",
                    "run → done (s)": (
                        f"{(_fa - _sa):.2f}" if _sa and _fa else "—"
                    ),
                    "thread": job.get("thread") or "—",
                }
            )

        _df = pl.DataFrame(_rows)
        _output = mo.vstack(
            [
                mo.md(
                    f"### Jobs ({len(_snapshot)} total · "
                    f"tick={_tick_val} · version={_version})"
                ),
                mo.ui.table(_df, selection=None, page_size=20),
            ]
        )

    _output
    return


@app.cell
def _(mo):
    mo.md("""
    ### What success looks like

    - **Latency < 1 s for QUEUED**: pressing the button updates the table
      within the same tick (because of the `set_jobs_version` bump). If
      QUEUED took >1 s, the main-thread bump isn't working.
    - **RUNNING appears within 1 polling interval** of QUEUED. The worker
      thread set the status; we observed it via the next tick. If RUNNING
      never appears (table stays QUEUED for 3+ s, then jumps to DONE),
      polling isn't triggering re-renders correctly.
    - **DONE appears within 1 polling interval** of the 3 s sleep ending.
    - **`thread` column** shows a `ThreadPoolExecutor-N_0/1` name — proves
      the worker actually ran off-main-thread.

    ### What failure looks like (and what we'd do)

    - **QUEUED never appears, only DONE**: main-thread state bump fails
      to trigger re-render → fall back to "wait for polling tick" UX.
    - **Status stays QUEUED forever**: worker thread can't write to the
      shared dict (lock issue, GIL surprise, or marimo isolates threads)
      → unlikely but would force a different IPC mechanism (file, pipe).
    - **Cell errors with "cannot call setter from background thread"**:
      proves the design's main assumption right but means we wrote the
      bump in the wrong cell. The setter is only called from the enqueue
      cell which runs on the main thread, so this shouldn't happen.

    ### Try this too

    - Press the button several times quickly → multiple QUEUED rows; up to
      2 RUNNING concurrently (max_workers=2); the rest stay QUEUED.
    - Change the **tick interval** dropdown to `500ms` → status updates
      get snappier; to `5s` → noticeably laggy.

    ---

    ## Part 2 — `queue.Queue` cascade hand-off

    The real pipeline needs to chain `download → split → analyze`. The worker
    thread can't safely call `enqueue_job(...)` (which writes to `mo.state`).
    Instead each `done_callback` pushes a `(next_kind, target_id)` tuple onto
    a `queue.Queue`. A separate tick-gated cell drains the queue from the
    main thread and calls `enqueue_job` from there.

    Press **Start cascade** below. You should see three jobs appear in
    sequence: `stage-1` runs ~1.5 s, on done it pushes `stage-2` onto the
    queue; the next polling tick drains it and enqueues `stage-2`, which
    runs ~1.5 s and chains to `stage-3`. Total: 3 jobs end-to-end in
    ~4–5 s.
    """)
    return


@app.cell
def _():
    import queue

    # Queue for cross-thread cascade signalling. Worker callbacks push
    # ("next_kind", "parent_id") tuples; the main-thread drainer pops them.
    cascade_queue = queue.Queue()
    return (cascade_queue,)


@app.cell
def _(mo):
    cascade_btn = mo.ui.run_button(
        label="Start cascade (3 chained jobs)", kind="warn"
    )
    cascade_btn
    return (cascade_btn,)


@app.cell
def _(
    cascade_btn,
    cascade_queue,
    executor,
    jobs_dict,
    jobs_lock,
    mo,
    set_jobs_version,
    threading,
    time,
    uuid,
):
    def _make_stage_worker(kind):
        def _worker(job_id):
            with jobs_lock:
                jobs_dict[job_id]["status"] = "RUNNING"
                jobs_dict[job_id]["started_at"] = time.time()
                jobs_dict[job_id]["thread"] = threading.current_thread().name
            time.sleep(1.5)
            with jobs_lock:
                jobs_dict[job_id]["status"] = "DONE"
                jobs_dict[job_id]["finished_at"] = time.time()
            # Cascade hand-off: push the next stage onto the queue.
            next_kind = {
                "stage-1": "stage-2",
                "stage-2": "stage-3",
                "stage-3": None,
            }[kind]
            if next_kind is not None:
                cascade_queue.put((next_kind, job_id))

        return _worker

    def _enqueue_cascade_job(kind):
        job_id = uuid.uuid4().hex[:6]
        with jobs_lock:
            jobs_dict[job_id] = {
                "id": job_id,
                "status": "QUEUED",
                "queued_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "thread": None,
                "kind": kind,
            }
        executor.submit(_make_stage_worker(kind), job_id)
        set_jobs_version(time.time_ns())

    if cascade_btn.value:
        _enqueue_cascade_job("stage-1")

    mo.md(
        "_Pressing **Start cascade** enqueues `stage-1` only. The worker's "
        "done-callback pushes `stage-2` onto `cascade_queue`; the drainer "
        "cell below picks it up on the next polling tick and submits it._"
    )
    return


@app.cell
def _(cascade_queue, mo, tick):
    # Drainer cell — subscribes to tick.value so it runs every polling
    # interval. Drains all available items from the cascade queue and
    # enqueues the next stage for each.
    _tick_val = tick.value
    _drained = []
    while not cascade_queue.empty():
        try:
            _next_kind, _parent_id = cascade_queue.get_nowait()
        except Exception:  # noqa: BLE001 — queue.Empty races
            break
        _enqueue_cascade_job(_next_kind)
        _drained.append((_next_kind, _parent_id))

    if _drained:
        _msg = f"_Drained {len(_drained)} cascade item(s) at tick={_tick_val}: " + ", ".join(
            f"`{k}` ← `{pid}`" for k, pid in _drained
        )
        _output = mo.callout(mo.md(_msg + "_"), kind="info")
    else:
        _output = mo.md(
            f"_Cascade drainer idle (tick={_tick_val}, queue empty)._"
        )
    _output
    return


@app.cell
def _(mo):
    mo.md("""
    ---

    ## Part 3 — `set_jobs(...)` from a cell that ALSO subscribes to `get_jobs()` doesn't self-loop

    Marimo's documented rule: a `mo.state` setter called from cell X does
    NOT re-trigger cell X (unless `allow_self_loops=True`). It only triggers
    OTHER cells that subscribe to the same getter. The cell below
    deliberately reads via the getter AND writes via the setter on every
    button press, then displays a render counter. If self-looping were
    happening, the counter would increment uncontrollably and the cell
    would freeze. Instead, each press should bump the counter by exactly 1.
    """)
    return


@app.cell
def _(mo):
    selfloop_get, selfloop_set = mo.state(0)
    selfloop_btn = mo.ui.run_button(
        label="Bump counter (test no self-loop)", kind="neutral"
    )
    selfloop_btn
    return selfloop_btn, selfloop_get, selfloop_set


@app.cell
def _(mo, selfloop_btn, selfloop_get, selfloop_set):
    # This cell intentionally reads via the getter AND writes via the setter
    # in the same body. Marimo's default behavior (no self-loops) should
    # mean: setter call doesn't re-trigger THIS cell; the next *user* press
    # triggers it because selfloop_btn.value changes.
    _n = selfloop_get()
    if selfloop_btn.value:
        selfloop_set(_n + 1)

    mo.md(
        f"**Self-loop test counter**: `{_n}`. "
        "Press the button → the counter shown should bump by exactly 1 "
        "between renders. If you see runaway numbers / the page freezes, "
        "marimo's default no-self-loops rule isn't working as documented."
    )
    return


if __name__ == "__main__":
    app.run()

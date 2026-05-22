import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys
    from pathlib import Path

    # Two scripts/ subpaths on sys.path:
    # - `scripts/library/` for the backend modules (youtube_fetcher etc.)
    # - `scripts/` for the shared `jobs_store` + `metadata_store` modules
    #   — single source of truth for the fcntl+atomic-rename JSON
    #   primitives, used by both this notebook and `scripts/worker.py`.
    _scripts = Path(__file__).resolve().parent.parent / "scripts"
    _lib = _scripts / "library"
    for _p in (_scripts, _lib):
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))

    import marimo as mo
    import asyncio  # noqa: F401 — harmless; kept for future async-friendly cells
    import os
    import time
    import polars as pl

    # Backend modules. The notebook itself only calls `youtube_fetcher`
    # (the synchronous search). `audio_analyzer` and `stem_splitter` are
    # imported by the external `scripts/worker.py` process — they live in
    # the import list here so the notebook can be a quick debug surface
    # (e.g. drop a scratch cell that calls `audio_analyzer.analyze(...)`
    # without having to wire sys.path again).
    import youtube_fetcher
    import stem_splitter
    import audio_analyzer

    # Shared job-queue + metadata primitives — both are also imported by
    # `scripts/worker.py`, so the lock/atomic-rename code is single-source.
    import jobs_store
    import metadata_store

    return Path, jobs_store, metadata_store, mo, pl, time, youtube_fetcher


@app.cell
def _(mo):
    mo.md("""
    # 🎙️ DJ Set Helper
    """)
    return


@app.cell
def _(Path, mo):
    downloads_dir = Path(__file__).resolve().parent.parent / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    # `lib_token` bump = "refresh the Library now". The Delete handler
    # bumps this so the table re-renders before the next polling tick.
    get_lib_token, set_lib_token = mo.state(0)
    # The Library table's selection (= path str of the chosen row), kept
    # in mo.state so it survives the 1 s polling re-renders. The table's
    # on_change writes here; on next render the Library cell restores
    # selection via `initial_selection`. Consumers (audio player,
    # Split+Detect, Delete) read from here — not from `library_table.value`,
    # which would be empty for a beat after each re-render.
    get_selected_path, set_selected_path = mo.state(None)
    return (
        downloads_dir,
        get_lib_token,
        get_selected_path,
        set_lib_token,
        set_selected_path,
    )


@app.cell
def _(downloads_dir, metadata_store):
    # Persistent metadata.json — maps `video_id` → `{title, filename}`.
    # `title` is the YouTube title captured at download time (shown in
    # the Library's title column). `filename` is the current on-disk
    # filename, which the external worker reads to find the file when
    # dispatching split / analyse jobs — that's what keeps the worker
    # honest across rename / title-named-download flows.
    #
    # Lock + atomic-rename plumbing lives in `scripts/metadata_store.py`
    # — the same module is imported by `scripts/worker.py`, so the
    # notebook (writer) and worker (reader) share one implementation.
    METADATA_FILE = downloads_dir.parent / "metadata.json"

    # Bootstrap so the first lock-free read returns a real (empty) doc.
    metadata_store.mutate(METADATA_FILE, lambda _state: None)

    def read_metadata():
        return metadata_store.load(METADATA_FILE)

    def upsert_video_meta(video_id, title):
        """Persist the YouTube title + derived filename for `video_id`.
        Returns the chosen filename so callers can echo it to the user.
        Called from the download handler BEFORE enqueueing the job so
        the worker reads the filename when it picks the job up."""

        def _update(state):
            filename = metadata_store.derive_filename(
                title, video_id, state
            )
            metadata_store.upsert_video(
                state, video_id, title=title, filename=filename
            )
            return filename

        return metadata_store.mutate(METADATA_FILE, _update)

    def delete_video_meta(video_id):
        metadata_store.mutate(
            METADATA_FILE,
            lambda s: metadata_store.delete_video(s, video_id),
        )

    return delete_video_meta, read_metadata, upsert_video_meta


@app.cell
def _(downloads_dir, jobs_store, mo):
    # Cycle 4 Part B — external-worker IPC. Jobs persist in a JSON file at
    # `experiments/small/jobs.json`. The external `scripts/worker.py`
    # process is the only consumer; this notebook is a producer
    # (enqueue_job) plus a reader (snapshot_jobs / snapshot_analysis).
    # No threads in-process — the notebook loads light and the worker
    # survives marimo restarts.
    #
    # The lock + atomic-rename plumbing lives in `scripts/jobs_store.py`
    # so worker.py and this notebook share one implementation. Reads are
    # unlocked; writes go through `jobs_store.mutate(JOBS_FILE, fn)`.
    JOBS_FILE = downloads_dir.parent / "jobs.json"

    # Bootstrap an empty state file on startup so the first lock-free
    # read returns a real (empty) document instead of falling through to
    # the in-memory default. Safe under concurrent worker boots — the
    # lock serializes the two writes; both write the same empty state.
    jobs_store.mutate(JOBS_FILE, lambda _state: None)

    # Main-thread state bumped on every enqueue → downstream cells re-run
    # instantly so the user sees QUEUED before the next polling tick fires.
    get_jobs_version, set_jobs_version = mo.state(0)
    return JOBS_FILE, get_jobs_version, set_jobs_version


@app.cell
def _(JOBS_FILE, downloads_dir, jobs_store, set_jobs_version, time):
    # Pipeline helpers — thin wrappers around `jobs_store`. `enqueue_job`
    # acquires the exclusive lock, runs the idempotent-insert decision in
    # `jobs_store.enqueue_job_in`, then bumps `set_jobs_version` so the
    # Library cell re-renders within the same tick (no waiting for the
    # next polling cycle). `snapshot_jobs` / `snapshot_analysis` are
    # lock-free reads with the exact dict shapes the Library expects.

    def enqueue_job(kind, target_id, force=False):
        """Idempotent enqueue. Returns a dict:
            {job_id, kind, target_id, status, action}
        where `action` is `"inserted"`, `"skip-active"`, or `"skip-done"`.
        See `jobs_store.enqueue_job_in` for the rules."""
        result = jobs_store.mutate(
            JOBS_FILE,
            lambda state: jobs_store.enqueue_job_in(
                state,
                kind,
                target_id,
                force=force,
                downloads_dir=downloads_dir,
            ),
        )
        # Main-thread state bump → instant re-render so the new (or
        # short-circuited) row shows up before the next polling tick.
        set_jobs_version(time.time_ns())
        return result

    def snapshot_jobs():
        """Returns `{(kind, target_id): {status, queued_at, …, result}}`
        — the same dict shape the Library cell already consumes. Latest
        job per `(kind, target_id)` wins so force-retries reflect
        immediately."""
        return jobs_store.snapshot_jobs(JOBS_FILE)

    def snapshot_analysis():
        """Returns `{file_path_str: {bpm, key, camelot}}` from DONE
        analyze jobs. Same shape the Library cell merges into rows."""
        return jobs_store.snapshot_analysis(JOBS_FILE)

    return enqueue_job, snapshot_analysis, snapshot_jobs


@app.cell
def _():
    def format_duration(seconds):
        if seconds is None:
            return "—"
        try:
            s = int(seconds)
        except (TypeError, ValueError):
            return "—"
        m, s = divmod(max(0, s), 60)
        return f"{m}:{s:02d}"

    def format_size(num_bytes):
        if num_bytes < 1024:
            return f"{num_bytes} B"
        if num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        return f"{num_bytes / (1024 * 1024):.2f} MB"

    return format_duration, format_size


@app.cell
def _(mo):
    query = mo.ui.text(value="daft punk", label="Song search", full_width=True)
    search_btn = mo.ui.run_button(label="Search YouTube", kind="success")
    mo.hstack([query, search_btn])
    return query, search_btn


@app.cell
def _(format_duration, mo, pl, query, search_btn, youtube_fetcher):
    mo.stop(
        not search_btn.value or not query.value.strip(),
        mo.md("_Type a query and press **Search YouTube** to see results._"),
    )

    _search_error = None
    try:
        search_results = youtube_fetcher.search_youtube(query.value, n=10)
    except (RuntimeError, ValueError) as _e:
        _search_error = str(_e)
        search_results = []

    _display_rows = [
        {
            "id": r["id"],
            "title": r["title"],
            "channel": r["channel"],
            "duration": format_duration(r.get("duration_seconds")),
        }
        for r in search_results
    ]

    if _display_rows:
        _df = pl.DataFrame(_display_rows)
    else:
        _df = pl.DataFrame(
            schema={
                "id": pl.Utf8,
                "title": pl.Utf8,
                "channel": pl.Utf8,
                "duration": pl.Utf8,
            }
        )
    results_table = mo.ui.table(_df, selection="single", page_size=10)

    if _search_error:
        _output = mo.vstack(
            [
                mo.callout(
                    mo.md(f"**Search failed**: `{_search_error}`"),
                    kind="danger",
                ),
                results_table,
            ]
        )
    elif not search_results:
        _output = mo.vstack(
            [
                mo.md("_No results — try a different query._"),
                results_table,
            ]
        )
    else:
        _output = results_table

    _output
    return results_table, search_results


@app.cell
def _(format_duration, mo, results_table, search_results):
    _sel = results_table.value
    mo.stop(
        len(_sel) == 0 or not search_results,
        mo.md("_Pick a row in the results table to arm the download._"),
    )

    selected_id = _sel["id"][0]
    _picked = next((r for r in search_results if r["id"] == selected_id), None)
    mo.stop(
        _picked is None,
        mo.md("_Selection is out of sync with the current results — re-search._"),
    )

    selected_title = _picked["title"]
    _channel = _picked["channel"]
    _duration = format_duration(_picked.get("duration_seconds"))
    _thumb = _picked.get("thumbnail_url") or ""

    mo.Html(
        f"""
        <div style="display:flex;gap:14px;align-items:center;
                    padding:10px;border:1px solid #ddd;border-radius:8px;
                    max-width:560px;margin:8px 0;">
            <img src="{_thumb}" alt="thumbnail"
                 style="width:120px;height:68px;border-radius:4px;
                        object-fit:cover;flex-shrink:0;background:#eee;"/>
            <div style="display:flex;flex-direction:column;gap:4px;
                        min-width:0;">
                <div style="font-weight:600;
                            overflow:hidden;text-overflow:ellipsis;
                            white-space:nowrap;">{selected_title}</div>
                <div style="font-size:0.9em;color:#666;">
                    {_channel} · {_duration}
                </div>
                <div style="font-size:0.8em;color:#999;font-family:monospace;">
                    id: {selected_id}
                </div>
            </div>
        </div>
        """
    )
    return selected_id, selected_title


@app.cell
def _(mo):
    download_btn = mo.ui.run_button(label="Download MP3", kind="success")
    download_btn
    return (download_btn,)


@app.cell
def _(
    download_btn,
    enqueue_job,
    mo,
    selected_id,
    selected_title,
    upsert_video_meta,
):
    # We write `{video_id: {title, filename}}` to metadata.json BEFORE
    # enqueueing the job. The worker reads metadata.json when it picks
    # the job up, so the MP3 lands on disk under the YouTube title (not
    # the bare video_id) the first time around — no rename step needed.
    if download_btn.value:
        _filename = upsert_video_meta(selected_id, selected_title)
        _job = enqueue_job("download", selected_id)
        _output = mo.callout(
            mo.md(
                f"**Enqueued** `download` for `{selected_title}` "
                f"(`{selected_id}`) → will save as `{_filename}`. "
                f"Current status: `{_job['status']}`.\n\n"
                "_The Library row below shows live status as it runs._ "
                "_Feel free to search for the next track while this works._"
            ),
            kind="info",
        )
    else:
        _output = mo.md(
            "_Press **Download MP3** to enqueue a non-blocking download. "
            "The UI stays interactive while jobs run — search for the "
            "next track or play audio while you wait._"
        )

    _output
    return


@app.cell
def _(mo):
    mo.image(src="./camelot.webp")
    return


@app.cell
def _(mo):
    mo.md("""
    | dial | major (B side) | minor (A side) |
    |:----:|:---------------|:---------------|
    | 1    | B major (`1B`)  | G#m (`1A`)      |
    | 2    | F# major (`2B`) | D#m (`2A`)      |
    | 3    | Db major (`3B`) | Bbm (`3A`)      |
    | 4    | Ab major (`4B`) | Fm (`4A`)       |
    | 5    | Eb major (`5B`) | Cm (`5A`)       |
    | 6    | Bb major (`6B`) | Gm (`6A`)       |
    | 7    | F major (`7B`)  | Dm (`7A`)       |
    | 8    | C major (`8B`)  | Am (`8A`)       |
    | 9    | G major (`9B`)  | Em (`9A`)       |
    | 10   | D major (`10B`) | Bm (`10A`)      |
    | 11   | A major (`11B`) | F#m (`11A`)     |
    | 12   | E major (`12B`) | C#m (`12A`)     |
    """)
    return


@app.cell
def _(mo):
    # Polling tick. Cells that need to observe worker-thread state changes
    # subscribe to tick.value; marimo re-renders them on each tick.
    tick = mo.ui.refresh(
        options=["500ms", "1s", "2s", "5s"],
        default_interval="1s",
    )
    tick
    return (tick,)


@app.cell
def _(JOBS_FILE, get_jobs_version, jobs_store, mo, tick, time):
    # Worker-health badge — sits just above the Library so the user
    # always sees whether the external worker is alive before they wonder
    # why their jobs are stuck. Subscribes to `tick.value` so it refreshes
    # every second, plus `get_jobs_version()` so it also refreshes on
    # each enqueue (useful immediately after pressing a button).
    #
    # A daemon heartbeat thread inside `scripts/worker.py` writes
    # `state["workers"][str(pid)].last_heartbeat = time.time()` every ~1 s
    # even while a long dispatch (e.g. 60-90 s stem split) is in flight,
    # so the 5 s staleness rule below stays honest.
    _tick_val = tick.value
    _ = get_jobs_version()

    _now = time.time()
    _live = jobs_store.live_workers(JOBS_FILE, stale_after_s=5.0)

    if _live:
        _pids = ", ".join(r["pid"] for r in _live)
        _ages = ", ".join(f"{_now - r['started_at']:.0f}s" for r in _live)
        _health = mo.callout(
            mo.md(
                f"**✓ {len(_live)} worker(s) running** "
                f"(pid {_pids} · uptime {_ages}). "
                "Jobs will be picked up within ~1 s."
            ),
            kind="success",
        )
    else:
        _health = mo.callout(
            mo.md(
                "**⚠️ No worker is running.** Jobs you enqueue will stay "
                "`QUEUED` until you start one.\n\n"
                "Open a second terminal and run:\n\n"
                "```bash\n"
                "uv run python experiments/small/scripts/worker.py\n"
                "```\n\n"
                "_The worker is independent from this notebook — stop / "
                "restart it without losing queued jobs, and run two in "
                "separate terminals for parallelism._"
            ),
            kind="warn",
        )
    _health
    return


@app.cell
def _(
    downloads_dir,
    format_size,
    get_jobs_version,
    get_lib_token,
    get_selected_path,
    mo,
    pl,
    read_metadata,
    set_selected_path,
    snapshot_analysis,
    snapshot_jobs,
    tick,
):
    # Subscribe to all four signals so this cell re-renders when:
    # - lib_token bumps (after a delete; instant refresh)
    # - get_jobs_version() bumps (instant feedback on each enqueue)
    # - tick.value advances (polls every 1 s — picks up worker mutations)
    # - get_selected_path() changes (so `initial_selection` reflects the
    #   user's pick on the very next render — keeps the table's
    #   highlighted row visually persistent across ticks)
    _ = get_lib_token()
    _version = get_jobs_version()
    _tick_val = tick.value
    _sel_path = get_selected_path()
    _cache = snapshot_analysis()
    _jobs = snapshot_jobs()
    _meta = read_metadata()

    _vm = _meta.get("video_metadata") or {}
    # Reverse lookup: current filename → immutable video_id. Lets us
    # keep jobs.json (target_id = video_id) and Library rows aligned for
    # files saved under the YouTube title. Fallback: treat the file
    # stem as the video_id (legacy / pre-title downloads).
    _filename_to_vid = {
        (m.get("filename") or f"{vid}.mp3"): vid for vid, m in _vm.items()
    }

    _stem_names = ("vocals", "drums", "bass", "other")

    def _row_status(kind, file_path, video_id):
        """Per-row status (priority: ERROR → RUNNING → QUEUED → disk)."""
        if kind == "source":
            keys = [
                ("download", video_id),
                ("split", video_id),
                ("analyze", str(file_path)),
            ]
        else:
            keys = [
                ("split", video_id),
                ("analyze", str(file_path)),
            ]
        for k in keys:
            j = _jobs.get(k)
            if j and j["status"] == "ERROR":
                return f"⚠️ {k[0]} error"
        for k in reversed(keys):
            j = _jobs.get(k)
            if j and j["status"] == "RUNNING":
                if k[0] == "split":
                    return "✂️ splitting…"
                if k[0] == "download":
                    return "⬇️ downloading…"
                return "🎚️ analyzing…"
        for k in reversed(keys):
            j = _jobs.get(k)
            if j and j["status"] == "QUEUED":
                return f"⏸ {k[0]} queued"
        # Disk fallback.
        if kind == "source":
            stem_dir = downloads_dir / file_path.stem
            stems_ok = all(
                (stem_dir / f"{n}.mp3").exists() for n in _stem_names
            )
            if file_path.exists():
                return "✓ split" if stems_ok else "✓ downloaded"
            return "—"
        return "✓ split" if file_path.exists() else "—"

    def _fmt_analysis(kind, stem_name, file_path):
        """Returns (bpm_str, key_str, camelot_str) per row-type rules."""
        info = _cache.get(str(file_path))
        if info is None:
            return "…", "…", "…"
        bpm = info.get("bpm", 0.0) or 0.0
        key = info.get("key", "unknown")
        cam = info.get("camelot", "?")
        bpm_s = f"{bpm:.0f}" if bpm else "—"
        if kind == "source":
            return bpm_s, key, cam
        if stem_name == "drums":
            return bpm_s, "—", "—"
        if stem_name == "bass":
            key_s = key if key == "unknown" else f"{key} (?)"
            cam_s = cam if cam == "?" else f"{cam} (?)"
            return bpm_s, key_s, cam_s
        # vocals / other
        return bpm_s, key, cam

    # Collect source video_ids: union of on-disk MP3s + any in-flight
    # download jobs (so a QUEUED/RUNNING download appears in Library
    # before its file lands on disk).
    _on_disk = sorted(
        downloads_dir.glob("*.mp3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    _vids_seen = []
    _src_for_vid = {}
    for _p in _on_disk:
        _vid = _filename_to_vid.get(_p.name, _p.stem)
        if _vid not in _src_for_vid:
            _vids_seen.append(_vid)
            _src_for_vid[_vid] = _p
    for (_k, _tid), _j in _jobs.items():
        if _k == "download" and _tid not in _src_for_vid:
            _meta_entry = _vm.get(_tid) or {}
            _fname = _meta_entry.get("filename") or f"{_tid}.mp3"
            _vids_seen.append(_tid)
            _src_for_vid[_tid] = downloads_dir / _fname

    _rows = []
    _row_paths = []
    _row_kinds = []
    _row_vids = []
    _split_count = 0
    for _vid in _vids_seen:
        _src = _src_for_vid[_vid]
        _title = (_vm.get(_vid) or {}).get("title") or "—"
        _stem_dir = downloads_dir / _src.stem
        _stem_paths = [(n, _stem_dir / f"{n}.mp3") for n in _stem_names]
        _present = [
            (n, p) for n, p in _stem_paths
            if p.exists() and p.stat().st_size > 0
        ]
        _is_split = len(_present) == len(_stem_names)
        if _is_split:
            _split_count += 1
        _bpm, _key, _cam = _fmt_analysis("source", None, _src)
        _src_size = (
            format_size(_src.stat().st_size) if _src.exists() else "—"
        )
        _rows.append(
            {
                "title": _title,
                "name": _src.name,
                "kind": "source",
                "status": _row_status("source", _src, _vid),
                "size": _src_size,
                "bpm": _bpm,
                "key": _key,
                "camelot": _cam,
                "path": str(_src),
            }
        )
        _row_paths.append(_src)
        _row_kinds.append("source")
        _row_vids.append(_vid)
        _split_job = _jobs.get(("split", _vid))
        _split_in_flight = (
            _split_job is not None
            and _split_job["status"] in ("QUEUED", "RUNNING")
        )
        _rows_to_emit = _stem_paths if _split_in_flight else _present
        for _n, _p in _rows_to_emit:
            _bpm, _key, _cam = _fmt_analysis("stem", _n, _p)
            _rows.append(
                {
                    "title": "",
                    "name": f"  └ {_n}",
                    "kind": "stem",
                    "status": _row_status("stem", _p, _vid),
                    "size": (
                        format_size(_p.stat().st_size)
                        if _p.exists() and _p.stat().st_size > 0
                        else "—"
                    ),
                    "bpm": _bpm,
                    "key": _key,
                    "camelot": _cam,
                    "path": str(_p),
                }
            )
            _row_paths.append(_p)
            _row_kinds.append("stem")
            _row_vids.append(_vid)

    library_row_paths = list(_row_paths)
    library_row_kinds = list(_row_kinds)
    library_row_vids = list(_row_vids)

    if _rows:
        _df = pl.DataFrame(_rows)
    else:
        _df = pl.DataFrame(
            schema={
                "title": pl.Utf8,
                "name": pl.Utf8,
                "kind": pl.Utf8,
                "status": pl.Utf8,
                "size": pl.Utf8,
                "bpm": pl.Utf8,
                "key": pl.Utf8,
                "camelot": pl.Utf8,
                "path": pl.Utf8,
            }
        )

    # Resolve `get_selected_path()` (a path string) to a row index in
    # the just-built `_rows`, so we can hand `mo.ui.table` an
    # `initial_selection` that mirrors what the user last clicked. If
    # the previously-selected path is no longer present (row was
    # deleted, source was renamed, etc.), the table starts unselected.
    _initial_selection = None
    if _sel_path is not None:
        for _i, _p in enumerate(_row_paths):
            if str(_p) == _sel_path:
                _initial_selection = [_i]
                break

    def _on_table_select(new_value):
        """Capture the selection into `mo.state` so it survives the
        Library cell's 1 s polling re-render. Without this, the
        user's pick blinks out every tick and the audio player /
        action buttons see no selection."""
        # marimo passes a polars DataFrame for DataFrame inputs (or a
        # list of dicts for list inputs); accept both defensively.
        if new_value is None:
            set_selected_path(None)
            return
        if hasattr(new_value, "to_dicts"):
            rows = new_value.to_dicts()
        elif isinstance(new_value, list):
            rows = new_value
        else:
            return
        if not rows:
            set_selected_path(None)
            return
        path = rows[0].get("path")
        set_selected_path(str(path) if path is not None else None)

    library_table = mo.ui.table(
        _df,
        selection="single",
        initial_selection=_initial_selection,
        page_size=50,
        show_column_summaries=False,
        show_data_types=False,
        wrapped_columns=["title", "name"],
        on_change=_on_table_select,
    )

    _src_count = len(_vids_seen)
    _analyzed_files = sum(1 for r in _rows if r["bpm"] not in ("…", "—"))

    if not _vids_seen:
        _output = mo.vstack(
            [
                mo.md("## Library"),
                mo.md(
                    "_No downloads yet. Search for a track above and press "
                    "**Download MP3**._"
                ),
            ]
        )
    else:
        _output = mo.vstack(
            [
                mo.md(
                    f"## Library ({_src_count} source"
                    f"{'s' if _src_count != 1 else ''}, "
                    f"{_split_count} fully split, "
                    f"{_analyzed_files}/{len(_rows)} rows analyzed)\n\n"
                    "Pick a row, then use the **🚀 Split + Detect** or "
                    "**🗑️ Delete** buttons below to act on it. The audio "
                    "player at the bottom plays whichever row is selected."
                ),
                library_table,
            ]
        )

    _output
    return library_row_kinds, library_row_paths, library_row_vids


@app.cell
def _(mo):
    # Action buttons — operate on whatever row is selected in the Library
    # table above. `mo.ui.table(selection="single")` is the source of
    # truth, replacing the old "Now playing" dropdown.
    split_detect_btn = mo.ui.run_button(
        label="Run",
        kind="warn",
    )
    delete_btn = mo.ui.run_button(
        label="Delete",
        kind="danger",
    )
    confirm_delete = True
    mo.hstack(
        [split_detect_btn, delete_btn],
        justify="start",
        gap=1,
    )
    return delete_btn, split_detect_btn


@app.cell
def _(
    Path,
    enqueue_job,
    get_selected_path,
    library_row_kinds,
    library_row_paths,
    library_row_vids,
    mo,
    split_detect_btn,
):
    # Reads `get_selected_path()` (mo.state, written by the table's
    # on_change) rather than `library_table.value`. That way the
    # selection survives the Library cell's 1 s polling re-render.
    if not split_detect_btn.value:
        _output = mo.md(
            "_Pick a row in the Library above, then press "
            "**🚀 Split + Detect** to force a fresh split + analyse pass "
            "on it. Works for any MP3 in `downloads/` — re-downloads, "
            "manually dropped files, etc._"
        )
    else:
        _sel_path = get_selected_path()
        if not _sel_path:
            _output = mo.callout(
                mo.md("**No row selected.** Pick one in the Library first."),
                kind="warn",
            )
        else:
            _path = Path(_sel_path)
            _vid = _path.stem
            _kind = "source"
            _found = False
            for _p, _k, _v in zip(
                library_row_paths, library_row_kinds, library_row_vids
            ):
                if str(_p) == _sel_path:
                    _kind = _k
                    _vid = _v
                    _found = True
                    break

            if not _found:
                _output = mo.callout(
                    mo.md(
                        "**Selected row is no longer in the Library** "
                        "(was it deleted?). Pick another one."
                    ),
                    kind="warn",
                )
            else:
                _enqueued = []
                if _kind == "source":
                    _split_job = enqueue_job("split", _vid, force=True)
                    _enqueued.append(
                        f"`split` for `{_vid}` → `{_split_job['status']}`"
                    )
                    _src_job = enqueue_job(
                        "analyze", str(_path), force=True
                    )
                    _enqueued.append(
                        f"`analyze` for `{_path.name}` → "
                        f"`{_src_job['status']}`"
                    )
                else:
                    _ana_job = enqueue_job(
                        "analyze", str(_path), force=True
                    )
                    _enqueued.append(
                        f"`analyze` for stem "
                        f"`{_path.parent.name}/{_path.name}` → "
                        f"`{_ana_job['status']}`"
                    )

                _items = "\n".join(f"- {e}" for e in _enqueued)
                _output = mo.callout(
                    mo.md(
                        f"**Enqueued {len(_enqueued)} job(s)** for "
                        f"`{_path.name}`:\n{_items}\n\n"
                        "_Watch the Library status column above for "
                        "live updates._"
                    ),
                    kind="info",
                )

    _output
    return


@app.cell
def _(
    Path,
    delete_btn,
    delete_video_meta,
    downloads_dir,
    get_selected_path,
    library_row_kinds,
    library_row_paths,
    library_row_vids,
    mo,
    set_lib_token,
    set_selected_path,
    time,
):
    # Reads `get_selected_path()` (mo.state) instead of
    # `library_table.value` so the selection survives the 1 s polling
    # re-render. After a successful delete we also clear the selection
    # state so the row that's now gone isn't restored next tick.
    import shutil as _shutil

    if not delete_btn.value:
        _output = mo.md("")
    else:
        _sel_path = get_selected_path()
        if not _sel_path:
            _output = mo.callout(
                mo.md("**No row selected.** Pick one in the Library first."),
                kind="warn",
            )
        else:
            _path = Path(_sel_path)
            _vid = _path.stem
            _kind = "source"
            for _p, _k, _v in zip(
                library_row_paths, library_row_kinds, library_row_vids
            ):
                if str(_p) == _sel_path:
                    _kind = _k
                    _vid = _v
                    break

            _deleted, _errors = [], []
            if _kind == "source":
                if _path.exists():
                    try:
                        _path.unlink()
                        _deleted.append(f"MP3 `{_path.name}`")
                    except OSError as exc:
                        _errors.append(f"MP3 `{_path.name}`: {exc}")
                _stem_dir = downloads_dir / _path.stem
                if _stem_dir.exists() and _stem_dir.is_dir():
                    try:
                        _shutil.rmtree(_stem_dir)
                        _deleted.append(f"stems folder `{_stem_dir.name}/`")
                    except OSError as exc:
                        _errors.append(
                            f"stems folder `{_stem_dir.name}/`: {exc}"
                        )
                try:
                    delete_video_meta(_vid)
                    _deleted.append("metadata.json entry")
                except OSError as exc:
                    _errors.append(f"metadata.json entry: {exc}")
            else:
                if _path.exists():
                    try:
                        _path.unlink()
                        _deleted.append(
                            f"stem `{_path.parent.name}/{_path.name}`"
                        )
                    except OSError as exc:
                        _errors.append(f"stem `{_path.name}`: {exc}")

            # Clear the selection state — the row is gone, so we
            # shouldn't restore it on next render.
            set_selected_path(None)
            # Bump lib_token so the Library re-renders without waiting
            # for the next polling tick. Also acts as a confirmation cue.
            set_lib_token(time.time_ns())

            if _errors:
                _output = mo.callout(
                    mo.md(
                        f"**Deleted**: {', '.join(_deleted) or 'nothing'}."
                        f"\n\n**Errors**: {'; '.join(_errors)}"
                    ),
                    kind="danger",
                )
            else:
                _output = mo.callout(
                    mo.md(
                        f"**Deleted** {', '.join(_deleted)}. "
                        "The Library has been refreshed."
                    ),
                    kind="success",
                )

    _output
    return


@app.cell
def _(Path, get_selected_path, library_row_kinds, library_row_paths, mo):
    # Reads the selection from `mo.state` (set by the Library table's
    # on_change) — not `library_table.value`, which gets reset for a
    # beat every 1 s polling tick and made the player flicker out.
    _sel_path = get_selected_path()
    mo.stop(
        not _sel_path,
        mo.md(
            "_Select a row in the Library above to play its audio here._"
        ),
    )

    _path = Path(_sel_path)

    _kind = "source"
    for _p, _k in zip(library_row_paths, library_row_kinds):
        if str(_p) == _sel_path:
            _kind = _k
            break

    mo.stop(
        not _path.exists() or _path.stat().st_size == 0,
        mo.callout(
            mo.md(
                f"**Audio file not found on disk:** `{_path}`\n\n"
                "Was it deleted between the Library snapshot and this pick?"
            ),
            kind="danger",
        ),
    )

    if _kind == "source":
        _label = f"source — `{_path.name}`"
    else:
        _label = f"stem — `{_path.parent.name}/{_path.name}`"

    mo.vstack(
        [
            mo.md(f"### ▶ Now playing — {_label}"),
            mo.audio(str(_path)),
        ]
    )
    return


if __name__ == "__main__":
    app.run()

import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import asyncio
    import random
    import polars as pl

    return asyncio, mo, pl, random


@app.cell
def _(mo):
    mo.md("""
    # DJ-set helper — prototype (mocks only)

    ## Design doc

    **Chosen reactivity pattern**: _dependency-chain reactivity_ for cross-stage
    cascading, plus a single `async def` cell with `mo.output.replace` for
    in-cell live progress. No `mo.state`, no `mo.ui.refresh` polling, no
    `asyncio.create_task`.

    - **Cascading between stages**: each pipeline stage lives in its own cell
      and consumes the previous stage's output as a normal Python variable
      (`query` → `search_results` → `selected_id` → pipeline cell). Marimo's
      DAG re-runs downstream cells automatically whenever an upstream var
      changes — that's "auto-cascade" for free.
    - **Live progress inside one stage**: the long-running stage is an
      `async def _()` cell that mutates a local `list[dict]` and calls
      `mo.output.replace(...)` after each tick. The cell's displayed output
      updates in place while it awaits. During the long stem-split phase the
      output is `mo.vstack([latency_banner, table])` so the user understands
      30–90 s is normal.
    - **Pipeline state**: a single mutable `list[dict]` is the source of truth
      inside the async cell. It's converted to `polars.DataFrame` only for
      rendering. No cross-cell mutation, no sidecar JSON, no `mo.state`. If
      the user reselects a row mid-run the async cell simply restarts — that's
      the desired behavior.
    - **Why not `mo.state`?** Docs explicitly recommend against it unless you
      need cycles or bidirectional UI sync. We have neither here.
    - **Why not `mo.ui.refresh`?** Polling races the cascade and burns CPU.
      The async cell already drives updates at the natural rate.
    - **Why not `asyncio.create_task`?** An orphaned task can't talk back to
      marimo's renderer cleanly — `mo.output.replace` only redraws the
      *currently executing* cell.
    - **Cell-body control flow**: marimo cells are top-level code, not normal
      functions — early-exit via `mo.stop(...)` (or branch with if/else),
      never with a bare `return`.

    ### Grouped / expandable table rows in marimo 0.23.x

    `mo.ui.table` has **no native parent-child grouping** primitive in this
    version. We use the spec'd **flat-with-indent fallback**:

    | column   | meaning                                            |
    |----------|----------------------------------------------------|
    | `kind`   | `source` or `stem`                                 |
    | `parent` | source title (empty for the source row)            |
    | `name`   | indented (`  └ vocals`) for stem rows              |
    | `bpm`    | from `audio_analyzer.analyze(...)`                  |
    | `key`    | from `audio_analyzer.analyze(...)`                  |
    | `camelot`| from `audio_analyzer.analyze(...)`                  |
    | `status` | unicode chip (`⏳ queued`, `✓ done`, `⚠️ error`, ...)|

    Status states: `queued | downloading | splitting | analyzing | error | done`.
    Camelot chips are rendered as `mo.Html` badges in a legend OUTSIDE the
    table. Inside table cells we keep plain text — table cells don't reliably
    render arbitrary HTML across marimo versions.

    ### Module wiring plan (real signatures, after backends landed)

    | module            | function                                              | called from                                       |
    |-------------------|-------------------------------------------------------|---------------------------------------------------|
    | `youtube_fetcher` | `search_youtube(query, n=10) -> list[dict]`           | search cell, sync (~1–3 s)                        |
    | `youtube_fetcher` | `download_mp3(video_id, out_dir) -> Path`             | async cascade via `await asyncio.to_thread(...)`  |
    | `stem_splitter`   | `split_stems(mp3_path, out_dir) -> dict[str, Path]`   | async cascade via `to_thread` (~30–90 s warm)     |
    | `audio_analyzer`  | `analyze(audio_path) -> {bpm, key, camelot}`          | async cascade, one call per file (sync)           |

    Real `search_youtube` rows have keys `id, title, channel, duration_seconds,
    url, thumbnail_url`. `duration_seconds` may be `None` for live streams.

    ### Error handling

    - `stem_splitter` raises `StemSplitterError` (subclass of `RuntimeError`).
      Common: missing `REPLICATE_API_TOKEN`, transient 5xx/rate-limit,
      unexpected output shape. Transient ones deserve a per-row retry button.
    - `youtube_fetcher` raises plain `RuntimeError` (age-restricted /
      region-locked / network / missing ffmpeg). Surface the message; do not
      auto-retry.
    - `audio_analyzer.analyze` is permissive — returns `bpm=0.0`, `key='unknown'`,
      `camelot='?'` instead of raising on bad input.

    The prototype demonstrates the **error visual** (`⚠️ error` chip + banner
    + per-row retry affordance) via a checkbox toggle. Real retry wiring will
    use a `mo.ui.array` of per-row run buttons keyed by stem name — not
    prototyped here.

    `REPLICATE_API_TOKEN` is auto-loaded by marimo from `.env` via
    `[tool.marimo.runtime] dotenv = [".env"]`.
    """)
    return


@app.cell
def _(mo):
    _CAMELOT_KEYS = {
        "Abm": "1A", "B":   "1B",
        "Ebm": "2A", "F#":  "2B",
        "Bbm": "3A", "Db":  "3B",
        "Fm":  "4A", "Ab":  "4B",
        "Cm":  "5A", "Eb":  "5B",
        "Gm":  "6A", "Bb":  "6B",
        "Dm":  "7A", "F":   "7B",
        "Am":  "8A", "C":   "8B",
        "Em":  "9A", "G":   "9B",
        "Bm":  "10A", "D":   "10B",
        "F#m": "11A", "A":   "11B",
        "C#m": "12A", "E":   "12B",
    }

    def camelot_for(key_label):
        return _CAMELOT_KEYS.get(key_label, "?")

    def _camelot_color(code):
        if not code or code == "?":
            return "#888"
        try:
            spoke = int(code.rstrip("ABab"))
        except ValueError:
            return "#888"
        hue = (spoke - 1) * 30
        return f"hsl({hue}, 65%, 50%)"

    def camelot_badge(code):
        bg = _camelot_color(code)
        return mo.Html(
            f'<span style="background:{bg};color:white;padding:2px 10px;'
            f'border-radius:10px;font-weight:600;font-family:monospace;'
            f'display:inline-block;">{code}</span>'
        )

    def status_chip(status):
        chips = {
            "queued":      "⏳ queued",
            "downloading": "⬇️ downloading",
            "splitting":   "✂️ splitting",
            "analyzing":   "🎚️ analyzing",
            "error":       "⚠️ error — retry?",
            "done":        "✓ done",
        }
        return chips.get(status, status)

    def format_duration(seconds):
        if seconds is None:
            return "—"
        try:
            s = int(seconds)
        except (TypeError, ValueError):
            return "—"
        m, s = divmod(max(0, s), 60)
        return f"{m}:{s:02d}"

    return camelot_badge, camelot_for, format_duration, status_chip


@app.cell
def _(camelot_badge, mo):
    mo.vstack(
        [
            mo.md("### Camelot badge legend (DJ harmonic-mixing wheel)"),
            mo.hstack([camelot_badge(f"{i}A") for i in range(1, 13)]),
            mo.hstack([camelot_badge(f"{i}B") for i in range(1, 13)]),
        ]
    )
    return


@app.cell
def _(mo):
    query = mo.ui.text(value="daft punk", label="Song search", full_width=True)
    search_btn = mo.ui.run_button(label="Search YouTube", kind="success")
    mo.hstack([query, search_btn])
    return query, search_btn


@app.cell
def _(format_duration, mo, pl, query, random, search_btn):
    mo.stop(
        not search_btn.value,
        mo.md("_Type a query and press **Search YouTube** to see mock results._"),
    )

    random.seed(query.value)

    def _yt_id():
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        return "".join(random.choice(alphabet) for _ in range(11))

    _channels = ["OfficialChannel", "BestRemixes", "DJ Sets HQ", "Various Artists"]
    _raw_results = []
    for i in range(1, 6):
        _vid = _yt_id()
        _dur = None if i == 4 else random.randint(120, 360)
        _raw_results.append(
            {
                "id": _vid,
                "title": f"{query.value.title()} — mock track {i}",
                "channel": random.choice(_channels),
                "duration_seconds": _dur,
                "url": f"https://www.youtube.com/watch?v={_vid}",
                "thumbnail_url": (
                    f"https://placehold.co/120x68/"
                    f"{random.choice(['1a1a2e','16213e','0f3460','533483'])}/eee"
                    f"?text={query.value.replace(' ', '+')}+{i}"
                ),
            }
        )

    search_results = _raw_results
    _display_rows = [
        {
            "id": r["id"],
            "title": r["title"],
            "channel": r["channel"],
            "duration": format_duration(r["duration_seconds"]),
        }
        for r in search_results
    ]
    results_table = mo.ui.table(
        pl.DataFrame(_display_rows), selection="single", page_size=10
    )
    results_table
    return results_table, search_results


@app.cell
def _(format_duration, mo, results_table, search_results):
    _sel = results_table.value
    mo.stop(
        len(_sel) == 0,
        mo.md("_Pick a row in the table above to arm the pipeline._"),
    )

    selected_id = _sel["id"][0]
    _picked = next(r for r in search_results if r["id"] == selected_id)
    selected_title = _picked["title"]
    _selected_channel = _picked["channel"]
    _selected_duration = format_duration(_picked["duration_seconds"])
    _selected_thumbnail = _picked["thumbnail_url"]

    _preview = mo.Html(
        f"""
        <div style="display:flex;gap:14px;align-items:center;
                    padding:10px;border:1px solid #ddd;border-radius:8px;
                    max-width:560px;">
            <img src="{_selected_thumbnail}" alt="thumbnail"
                 style="width:120px;height:68px;border-radius:4px;
                        object-fit:cover;flex-shrink:0;"/>
            <div style="display:flex;flex-direction:column;gap:4px;">
                <div style="font-weight:600;">{selected_title}</div>
                <div style="font-size:0.9em;color:#666;">
                    {_selected_channel} · {_selected_duration}
                </div>
                <div style="font-size:0.8em;color:#999;font-family:monospace;">
                    id: {selected_id}
                </div>
            </div>
        </div>
        """
    )

    mo.vstack(
        [
            _preview,
            mo.md(
                f"_Would call_ `youtube_fetcher.download_mp3({selected_id!r}, out_dir)` "
                "_on pipeline run._"
            ),
        ]
    )
    return selected_id, selected_title


@app.cell
def _(mo):
    demo_error = mo.ui.checkbox(
        value=False,
        label="Demo error path during stem split (forces ⚠️ error state on stems)",
    )
    run_pipeline_btn = mo.ui.run_button(
        label="Run pipeline (mock)", kind="success"
    )
    mo.hstack([run_pipeline_btn, demo_error])
    return demo_error, run_pipeline_btn


@app.cell
async def _(
    asyncio,
    camelot_for,
    demo_error,
    mo,
    pl,
    run_pipeline_btn,
    selected_id,
    selected_title,
    status_chip,
):
    mo.stop(
        not run_pipeline_btn.value,
        mo.md("_Press **Run pipeline (mock)** to start the cascade._"),
    )

    _stem_names = ["vocals", "drums", "bass", "other"]
    _rows = [
        {
            "kind": "source",
            "parent": "",
            "name": selected_title,
            "id": selected_id,
            "bpm": "—",
            "key": "—",
            "camelot": "—",
            "status": status_chip("queued"),
        }
    ] + [
        {
            "kind": "stem",
            "parent": selected_title,
            "name": f"  └ {s}",
            "id": selected_id,
            "bpm": "—",
            "key": "—",
            "camelot": "—",
            "status": status_chip("queued"),
        }
        for s in _stem_names
    ]

    def _update(rows, predicate, **fields):
        for r in rows:
            if predicate(r):
                r.update(fields)
        return rows

    def _table(rows):
        return mo.ui.table(pl.DataFrame(rows), selection=None, page_size=10)

    def _render(rows, banner=None):
        body = _table(rows)
        if banner is None:
            return body
        return mo.vstack([banner, body])

    _split_banner = mo.callout(
        mo.md(
            "**Splitting stems via Replicate Demucs…** typically **~30–90 s** "
            "(can be longer on cold start). This is normal — don't reload."
        ),
        kind="info",
    )

    # Stage 0: render initial queued state.
    mo.output.replace(_render(_rows))
    await asyncio.sleep(0.4)

    # Stage 1: download.
    _update(_rows, lambda r: r["kind"] == "source", status=status_chip("downloading"))
    mo.output.replace(_render(_rows))
    await asyncio.sleep(0.9)

    # Stage 2: split — long phase, show latency banner.
    _update(_rows, lambda r: r["kind"] == "source", status=status_chip("splitting"))
    mo.output.replace(_render(_rows, banner=_split_banner))
    await asyncio.sleep(1.2)

    if demo_error.value:
        # Error branch: stem split failed. Show error chips on stems +
        # callout. (Real retry button lives in a separate cell — see design.)
        _update(
            _rows,
            lambda r: r["kind"] == "stem",
            status=status_chip("error"),
        )
        _error_banner = mo.callout(
            mo.md(
                "**Stem split failed** — `StemSplitterError: Replicate 503 "
                "(rate-limited)`. Each stem row would expose a per-row retry "
                "button in production (wired via `mo.ui.array`). Toggle the "
                "checkbox off and re-run to see the success path."
            ),
            kind="danger",
        )
        mo.output.replace(_render(_rows, banner=_error_banner))
    else:
        # Success branch: analyze source, then each stem.
        _update(_rows, lambda r: r["kind"] == "source", status=status_chip("analyzing"))
        mo.output.replace(_render(_rows))
        await asyncio.sleep(0.6)

        _src_key, _src_bpm = "Am", 128
        _update(
            _rows,
            lambda r: r["kind"] == "source",
            bpm=str(_src_bpm),
            key=_src_key,
            camelot=camelot_for(_src_key),
            status=status_chip("done"),
        )
        mo.output.replace(_render(_rows))
        await asyncio.sleep(0.3)

        _stem_meta = [
            ("vocals", "Am", 128),
            ("drums",  "—",  128),
            ("bass",   "Em", 128),
            ("other",  "C",  128),
        ]
        for _sn, _k, _bpm in _stem_meta:
            _update(
                _rows,
                lambda r, sn=_sn: r["kind"] == "stem" and r["name"].endswith(sn),
                status=status_chip("analyzing"),
            )
            mo.output.replace(_render(_rows))
            await asyncio.sleep(0.5)
            _update(
                _rows,
                lambda r, sn=_sn: r["kind"] == "stem" and r["name"].endswith(sn),
                bpm=str(_bpm),
                key=_k,
                camelot=camelot_for(_k) if _k != "—" else "—",
                status=status_chip("done"),
            )
            mo.output.replace(_render(_rows))

        mo.output.replace(
            mo.vstack(
                [
                    mo.md(
                        f"### ✓ Pipeline complete — **{selected_title}** "
                        f"(`{selected_id}`)"
                    ),
                    _table(_rows),
                ]
            )
        )
    return


if __name__ == "__main__":
    app.run()

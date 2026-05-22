import marimo

__generated_with = "0.23.4"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md("""
    # DJ Set Helper — Review Guide

    You stepped away after Cycle 1; while you were gone the team shipped
    Cycle 2 (stem split) and Cycle 3 (BPM / key / Camelot), then a mid-flight
    bug fix in `stem_splitter.py` after you flagged the Replicate URL issue,
    and finally a filesystem reorganization into `scripts/`, `reports/`,
    and `misc/`.

    This notebook is your **single point of entry**: it tells you what to
    verify, where to look, what went sideways, and what's nice-to-have.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Where to start

    Run this from `experiments/small/`:

    ```bash
    uv run marimo edit misc/notebook.py
    ```

    That's the **one ultimate notebook** — all 3 cycles live in it, growing
    downward. Every cycle has its own `### How to verify` markdown block
    with numbered steps you can tick through inside the notebook.

    Come back to *this* guide if anything looks off, or as an index.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## What you're verifying

    A reactive DJ-set helper. The full pipeline, all auto-cascading:

    1. Type a fuzzy song name into a search bar
    2. Pick a YouTube result from the list
    3. yt-dlp downloads the MP3 (320 kbps)
    4. Replicate Demucs splits it into `vocals / drums / bass / other`
    5. librosa analyzes BPM + musical key for the source AND each stem
    6. Each key gets a Camelot-wheel notation (DJ harmonic-mixing standard)

    Everything renders into a single hierarchical Library table at the
    bottom of `misc/notebook.py` — source row + 4 indented stem rows per
    downloaded song.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Notebook map

    Only `misc/notebook.py` matters for verifying the product. Everything
    else is a reference you can open *if* something misbehaves.

    | Path                                | Purpose                                                                | When to open                                 |
    |-------------------------------------|------------------------------------------------------------------------|----------------------------------------------|
    | `misc/notebook.py`                  | **THE master app — all 3 cycles**                                      | Always — primary verification surface         |
    | `reports/review.py` (this file)     | This guide                                                             | First, to orient                              |
    | `reports/verify_youtube_fetcher.py` | Isolation tester for `youtube_fetcher`                                 | Only if Cycle 1 misbehaves in the master      |
    | `reports/verify_stem_splitter.py`   | Isolation tester for `stem_splitter` + **URL-bug regression detector** | Only if Cycle 2 misbehaves                    |
    | `reports/verify_audio_analyzer.py`  | Isolation tester + synth-tone self-check + Camelot wheel reference     | Only if Cycle 3 misbehaves                    |
    | `misc/prototype.py`                 | Early mock-data sandbox (no live backends)                             | Optional — reference for reactive patterns    |
    | `misc/async_button_demo.py`         | Original starter (untouched)                                           | Skip                                          |
    | `scripts/library/*.py`              | The 3 backend modules imported by the notebooks                        | Open only if debugging a backend              |
    | `scripts/main.py`                   | Boilerplate, unused                                                    | Skip                                          |
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Verification flow

    Each cycle has its own `### How to verify` block in `misc/notebook.py`
    with explicit, numbered steps. Below is a summary so you know what to
    expect before you open it.

    ### Cycle 1 — Search & Download

    - Search "daft punk" → ≥10 results render (title, channel, `mm:ss` duration)
    - Click a row → thumbnail preview card appears
    - Press **Download MP3** → spinner → green callout + new Library row
    - Press **Download MP3** again on same row → **idempotent** (Library count unchanged)
    - Bad query / region-locked → red callout, **no stack trace**

    ### Cycle 2 — Stem Split (auto-cascades after Cycle 1)

    - After a successful download, the "Splitting stems…" spinner appears
      with a `~30–90 s` subtitle. **This is normal — don't reload.**
    - On completion: 4 stem files land in `downloads/{video_id}/{vocals,drums,bass,other}.mp3`
    - Library now hierarchical: 1 source row + 4 indented stem rows
    - **CRITICAL** (post-bugfix): stem files are real MP3 audio. Each is >10 KB
      and starts with `ID3` or MPEG sync bytes. If `reports/verify_stem_splitter.py`
      ever flashes "REGRESSION DETECTED", the URL-download bug is back.
    - Re-download same track → both stages no-op (idempotent)
    - Missing `REPLICATE_API_TOKEN` → friendly yellow callout, no crash

    ### Cycle 3 — BPM, Key & Camelot (auto-cascades after Cycle 2)

    - First analysis call in a session pays **~40 s for numba JIT**.
      Subsequent files are ~1 s each. Spinner subtitle warns you.
    - Library gains 3 new columns: `bpm | key | camelot`
    - **Source** and **vocals / other** stem rows show all three
    - **Bass** stem: key & camelot suffixed with ` (?)` (low confidence —
      isolated bass often flips major/minor due to missing third)
    - **Drums** stem: bpm only; key & camelot show `—` (no harmonic content)
    - Cross-check the Camelot legend in the notebook: source `key="C major"`
      must give `camelot="8B"`; `A minor` → `8A`; `G major` → `9B`; `F#m` → `11A`
    - Re-run any prior step → analysis is cached, instant, "0 new"
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Challenges encountered

    A running log of the non-obvious surprises, in order of pain.

    ### 1. Replicate output shape — the big one

    `stem_splitter` was initially built assuming `replicate.run` returns
    `FileOutput` objects you stream chunk-by-chunk. **Wrong** — for
    `ryan5453/demucs` on `replicate==1.0.7`, the SDK returns a `dict[str, str]`
    of raw `https://replicate.delivery/.../file` URLs. The old code's
    `for chunk in value` fallback silently iterated the URL string
    character-by-character into 0-byte files, then "rescued" via an httpx
    codepath that mostly worked but left corruption around.

    **Fix**: explicit type dispatch (`str` → urllib download; `FileOutput`
    → `.read()`; iter-of-bytes last), 10 KiB minimum size + MP3 magic-byte
    validation (`ID3` or MPEG sync), corrupt cached files get re-downloaded
    automatically. Regression-prevention banner now lives in
    `reports/verify_stem_splitter.py` — it'll yell "REGRESSION DETECTED"
    if a stem ever fails either check.

    ### 2. `mo.ui.table` has no native grouped/expandable rows in 0.23.x

    Fallback: flat rows with a `parent` column + indented `name` like
    `  └ vocals`. Inside table cells we keep plain unicode for status
    (`⏳`, `✓`); the Camelot wheel is a separate markdown table OUTSIDE
    the data table (HTML in table cells is unreliable across versions).

    ### 3. Cell-body control flow

    Marimo cells aren't normal Python functions — `marimo check` rejects
    bare `return` inside cell bodies. Use `mo.stop(...)` for early exit or
    branch with if/else.

    ### 4. Underscore-stripping

    Names with a leading `_` are cell-local — marimo strips them from the
    cell return tuple, so they can't be used in other cells. Workaround:
    alias on import (e.g. `from audio_analyzer import _CAMELOT_MAJOR as CAMELOT_MAJOR`).

    ### 5. Reactivity model

    Settled on **dependency-chain reactivity** for cross-stage cascading
    + a single `async def` cell with `mo.output.replace` for in-cell
    live progress. Two `mo.state` slots: `lib_token` (cascade trigger) and
    `analysis_cache` (memoization). No `mo.ui.refresh` polling; no
    `asyncio.create_task` (orphaned tasks can't drive `mo.output.replace`
    because that only redraws the *currently executing* cell).

    ### 6. Audio-analysis accuracy

    Key detection on isolated stems is famously unreliable. Drums have no
    harmonic content (key is meaningless). Bass frequently flips major/minor
    due to absent third. Vocals and "other" are usually closest to the
    track key. The UI accommodates this per stem — see the rendering rules
    table in Cycle 3.

    ### 7. Cold-start latency

    - `audio_analyzer`: ~40 s on the first call per kernel for numba JIT.
    - `stem_splitter`: 30–90 s warm + 10–30 s cold-start per song on Replicate.

    Both surfaced in spinner subtitles so the user doesn't reload mid-call.

    ### 8. Mid-flight file reorganization

    After the work was largely done, we moved scripts into
    `scripts/library/`, notebooks into `misc/` and `reports/`. Required a
    sys.path tweak at the top of every notebook's imports cell to find the
    library modules:

    ```python
    import sys
    from pathlib import Path
    _lib = Path(__file__).resolve().parent.parent / "scripts" / "library"
    if str(_lib) not in sys.path:
        sys.path.insert(0, str(_lib))
    ```

    Now baked in everywhere that needs it.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Your manual to-do list

    Things worth doing while you have the notebook open, ordered by
    information value:

    - [ ] **Run the happy path end-to-end.** Search "daft punk" → pick →
      download → wait through the ~1 min stem split → wait through the
      ~40 s analysis cold-start. Tick the verification boxes per cycle
      in the notebook.
    - [ ] **Spot-check Camelot against a song you know.** Disclosure –
      Latch is reportedly Bb minor / 122 BPM (`3A` Camelot). Daft Punk –
      Around the World is reportedly D minor / 121 BPM (`7A`). Run one
      through and see if `audio_analyzer` agrees within reason.
    - [ ] **Confirm the stem-splitter regression detector.** Open
      `reports/verify_stem_splitter.py`, point its path input at any
      MP3 in `downloads/`, run split. Every stem row should show ✅.
      If you see "REGRESSION DETECTED", the URL bug came back.
    - [ ] **Try the synth self-check.** Open
      `reports/verify_audio_analyzer.py`, press "Run synth self-check".
      Should report PASS with `A major / 11B / ~120 BPM`.
    - [ ] **Force the error path.** Comment out `REPLICATE_API_TOKEN`
      in `.env`, restart the marimo edit session, attempt a download
      on a new video. Cycle 2 should yellow-callout, not crash, and
      Cycle 1 should still work.
    - [ ] **Test idempotency.** Re-search → re-pick → re-download a
      previously-downloaded track. No new files, no Replicate call,
      analysis stays cached.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Known limitations / polish candidates

    - **Multi-download cascade**: the auto-split and auto-analyze cells
      always pick the most-recent MP3 in `downloads/`. Rapid back-to-back
      downloads only auto-process the latest. Older un-split songs would
      render in the Library as `downloaded` but never get stems. For a
      linear DJ-prep workflow this is fine; a batch mode would need a
      queue. (Manual workaround: re-download an older song to bump its
      mtime; the cascade will pick it up.)
    - **Bass-stem key**: shown with ` (?)` suffix. Could be hidden
      entirely like the drum stem if you find it more confusing than
      helpful — one-line change in `_fmt_analysis`.
    - **Camelot color chips**: the legend is a plain markdown table.
      Could be promoted to colored chips via `mo.Html` if you want
      visual polish — the prototype has an example of this pattern.
    - **`misc/notebook.py` placement**: by your filesystem rule
      (non-report → misc), the master app lives in `misc/`. If that
      feels semantically wrong, easy to move to e.g. `app/` or back to
      the root — only the `## How to run` cell would need a path update.
    - **`scripts/main.py`**: unused boilerplate from `uv init`. Safe to
      delete.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Glossary — what's where

    - **`scripts/library/youtube_fetcher.py`** — yt-dlp search + 320 kbps
      MP3 download. Sync, idempotent, ~1–3 s search + a few seconds per
      download. Requires `ffmpeg` on `$PATH`.
    - **`scripts/library/stem_splitter.py`** — Replicate Demucs split.
      Sync, idempotent, ~30–90 s warm. Reads `REPLICATE_API_TOKEN` from
      env. Validates every stem file (size + MP3 magic bytes).
    - **`scripts/library/audio_analyzer.py`** — librosa BPM +
      Krumhansl–Schmuckler key + Camelot lookup. Sync, ~1 s warm,
      ~40 s first-call JIT. Never raises — returns
      `bpm=0.0, key='unknown', camelot='?'` on bad/silent input.
    - **`.env`** — must contain `REPLICATE_API_TOKEN=...` for Cycle 2
      to fire. Marimo auto-loads via the `[tool.marimo.runtime] dotenv`
      setting in `pyproject.toml`.
    - **`downloads/`** — source MP3s at the top level; stem MP3s in
      `{video_id}/` subfolders. Recursive scan drives the Library table.

    Stem-splitter's bug fix means the contents of `downloads/{video_id}/`
    are now guaranteed real audio (or the call fails loudly). That
    guarantee is the foundation Cycle 3 builds on.
    """)
    return


if __name__ == "__main__":
    app.run()

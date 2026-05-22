import marimo

__generated_with = "0.23.6"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys
    from pathlib import Path

    _lib = Path(__file__).resolve().parent.parent / "scripts" / "library"
    if str(_lib) not in sys.path:
        sys.path.insert(0, str(_lib))

    import marimo as mo
    import tempfile

    from youtube_fetcher import search_youtube, download_mp3

    return Path, download_mp3, mo, search_youtube, tempfile


@app.cell
def _(mo):
    mo.md("""
    # Verify `youtube_fetcher.py`

    This module wraps **yt-dlp** to give the DJ-set pipeline two sync helpers:
    a YouTube search returning rich result dicts, and an idempotent 320 kbps
    MP3 download keyed by video ID.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## How to verify

    1. Enter a query in the text box below (try `Disclosure Latch` for a known-good test).
    2. Press **Run search** — confirm 5-10 results render with title, channel, duration, and a thumbnail.
    3. Press **Download top hit** — confirm a path is printed and the file size is in the few-MB range.
    4. Press **Download top hit** again — confirm the second call is instant (idempotency check, file already on disk).
    5. (Optional) Try a fuzzy/typo query and a foreign-language query to gauge search relevance.
    """)
    return


@app.cell
def _(mo):
    query_input = mo.ui.text(
        value="Disclosure Latch",
        label="Search query",
        full_width=True,
    )
    search_btn = mo.ui.run_button(label="Run search", kind="success")
    mo.vstack([query_input, search_btn])
    return query_input, search_btn


@app.cell
def _(mo, query_input, search_btn, search_youtube):
    mo.stop(
        not search_btn.value,
        mo.md("_Press **Run search** to fetch results._"),
    )

    try:
        results = search_youtube(query_input.value, n=8)
        search_error = None
    except Exception as exc:
        results = []
        search_error = str(exc)
    return results, search_error


@app.cell
def _(mo, results, search_error):
    mo.stop(
        search_error is not None,
        mo.md(f"**Search failed:** `{search_error}`"),
    )
    mo.stop(
        not results,
        mo.md("_No results returned._"),
    )

    rows = []
    for i, r in enumerate(results):
        dur = r["duration_seconds"]
        dur_str = f"{dur // 60}:{dur % 60:02d}" if dur is not None else "—"
        thumb = r["thumbnail_url"] or ""
        rows.append(
            f"| {i} | <img src='{thumb}' width='120'/> | **{r['title']}** | {r['channel']} | {dur_str} | `{r['id']}` |"
        )

    table_md = (
        "| # | thumb | title | channel | dur | id |\n"
        "|---|-------|-------|---------|-----|----|\n"
        + "\n".join(rows)
    )
    mo.md(table_md)
    return


@app.cell
def _(Path, mo, tempfile):
    download_dir = Path(tempfile.gettempdir()) / "music_machine_verify"
    download_dir.mkdir(parents=True, exist_ok=True)

    download_btn = mo.ui.run_button(label="Download top hit", kind="success")
    mo.vstack(
        [
            mo.md(f"**Download target:** `{download_dir}`"),
            download_btn,
        ]
    )
    return download_btn, download_dir


@app.cell
def _(download_btn, download_dir, download_mp3, mo, results, search_error):
    mo.stop(
        not download_btn.value,
        mo.md("_Press **Download top hit** after a search succeeds._"),
    )
    mo.stop(
        search_error is not None or not results,
        mo.md("**Need a successful search before downloading.**"),
    )

    top = results[0]
    with mo.status.spinner(
        title=f"Downloading {top['title']!r}...",
        subtitle=f"id={top['id']}",
    ):
        try:
            mp3_path = download_mp3(top["id"], download_dir)
            download_error = None
        except Exception as exc:
            mp3_path = None
            download_error = str(exc)

    if download_error:
        result_md = f"**Download failed:** `{download_error}`"
    else:
        size_mb = mp3_path.stat().st_size / 1024 / 1024
        result_md = (
            f"### Download OK\n\n"
            f"- **Path:** `{mp3_path}`\n"
            f"- **Size:** {size_mb:.2f} MB\n"
            f"- **Video ID:** `{top['id']}`\n"
            f"- **Title:** {top['title']}\n\n"
            f"_Press the button again — the second call should return instantly (idempotency)._"
        )
    mo.md(result_md)
    return


@app.cell
def _(mo):
    mo.md("""
    ---

    ## Function contracts

    ```python
    def search_youtube(query: str, n: int = 10) -> list[dict]:
        "\""Returns up to `n` result dicts with keys:
            id, title, channel, duration_seconds, url, thumbnail_url.
        duration_seconds may be None for live streams / unknown durations.
        Raises ValueError on empty query / non-positive n.
        Raises RuntimeError if yt-dlp's extraction fails."\""

    def download_mp3(video_id: str, out_dir: Path) -> Path:
        "\""Downloads `video_id` as 320 kbps MP3 to `out_dir/{video_id}.mp3`
        and returns the path. Idempotent: returns immediately if the file
        already exists and is non-empty.
        Requires `ffmpeg` on PATH (raises RuntimeError otherwise).
        Raises RuntimeError on download/conversion failure."\""
    ```

    **System deps:** `ffmpeg` (macOS: `brew install ffmpeg`).
    **Python deps:** `yt-dlp` (managed via `uv add`).
    """)
    return


if __name__ == "__main__":
    app.run()

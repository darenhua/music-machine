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
    import os

    import stem_splitter
    from stem_splitter import (
        STEM_NAMES,
        StemSplitterError,
        looks_like_mp3,
        split_stems,
    )

    return (
        Path,
        STEM_NAMES,
        StemSplitterError,
        looks_like_mp3,
        mo,
        os,
        split_stems,
    )


@app.cell
def _(mo):
    mo.md("""
    # `stem_splitter.py` verification

    `stem_splitter.split_stems(mp3_path, out_dir)` splits a local MP3 into
    four stems (vocals / drums / bass / other) by calling Replicate's
    `ryan5453/demucs` model. It requires `REPLICATE_API_TOKEN` in the
    environment (auto-loaded from `.env` by marimo) and a single song
    typically takes **~30–90 s warm**, plus an extra ~10–30 s cold-start
    the first time per session.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## How to verify

    1. Confirm `REPLICATE_API_TOKEN` is set in `.env` (token-status panel below).
    2. Type the absolute path to a local MP3 in the text box (drag-and-drop is fine on macOS — drop the file into Terminal to get the path).
    3. Press **Split stems**. A spinner will run for ~30–90 s.
    4. Inspect the resulting four `Path` objects and their file sizes — each stem MP3 should be > 0 bytes.
    5. Re-press **Split stems** with the same path: it should return instantly (idempotent disk cache).
    6. Trigger the **error-case** buttons at the bottom and confirm each raises a clean `StemSplitterError`.
    """)
    return


@app.cell
def _(mo, os):
    token_present = bool(os.environ.get("REPLICATE_API_TOKEN"))
    mo.md(
        f"**Token status:** `REPLICATE_API_TOKEN` is "
        f"{'✅ set' if token_present else '❌ NOT set — Replicate calls will fail'}."
    )
    return


@app.cell
def _(mo):
    mp3_path_input = mo.ui.text(
        value="",
        label="Local MP3 path",
        full_width=True,
    )
    out_dir_input = mo.ui.text(
        value="stems_out",
        label="Output directory (relative or absolute)",
        full_width=True,
    )
    run_button = mo.ui.run_button(label="Split stems", kind="success")
    return mp3_path_input, out_dir_input, run_button


@app.cell
def _(mo, mp3_path_input, out_dir_input, run_button):
    mo.vstack([mp3_path_input, out_dir_input, run_button])
    return


@app.cell
def _(
    Path,
    StemSplitterError,
    mo,
    mp3_path_input,
    out_dir_input,
    run_button,
    split_stems,
):
    mo.stop(
        not run_button.value,
        mo.md("_Press **Split stems** above to run the splitter._"),
    )
    mo.stop(
        not mp3_path_input.value.strip(),
        mo.md(
            "**Upload an MP3 first.** Paste an absolute path to a local MP3 "
            "into the text box above."
        ),
    )

    _mp3 = Path(mp3_path_input.value).expanduser()
    _out = Path(out_dir_input.value).expanduser()

    mo.stop(
        not _mp3.exists(),
        mo.md(f"**Path does not exist:** `{_mp3}`. Fix the path and re-press the button."),
    )

    try:
        with mo.status.spinner(
            title="Calling Replicate Demucs…",
            subtitle="~30–90s warm, longer on cold-start",
        ):
            stem_paths = split_stems(_mp3, _out)
        run_error: Exception | None = None
    except StemSplitterError as e:
        stem_paths = {}
        run_error = e
    except Exception as e:
        stem_paths = {}
        run_error = e
    return run_error, stem_paths


@app.cell
def _(STEM_NAMES, looks_like_mp3, mo, run_error: Exception | None, stem_paths):
    _MIN_BYTES = 10 * 1024

    if run_error is not None:
        mo.output.replace(
            mo.md(
                f"### ❌ Split failed\n\n"
                f"`{type(run_error).__name__}`: {run_error}"
            )
        )
    else:
        _rows = []
        _regressions: list[str] = []
        for _name in STEM_NAMES:
            _p = stem_paths[_name]
            _size = _p.stat().st_size
            _size_mb = _size / 1_048_576
            with _p.open("rb") as _fh:
                _head = _fh.read(4)
            _size_ok = _size >= _MIN_BYTES
            _magic_ok = looks_like_mp3(_head)
            _flag = "✅" if (_size_ok and _magic_ok) else "❌"
            if not _size_ok:
                _regressions.append(f"`{_name}` is only {_size} bytes (need ≥ {_MIN_BYTES})")
            if not _magic_ok:
                _regressions.append(f"`{_name}` head {_head!r} is not MP3 magic")
            _rows.append(
                f"| {_flag} | **{_name}** | `{_p}` | {_size_mb:.2f} MB | `{_head!r}` |"
            )
        _table = "\n".join(
            [
                "| ok | stem | path | size | head bytes |",
                "|----|------|------|------|------------|",
                *_rows,
            ]
        )
        if _regressions:
            _banner = (
                "### ⚠️ REGRESSION DETECTED — stems on disk are not valid MP3 audio\n\n"
                + "\n".join(f"- {_r}" for _r in _regressions)
                + "\n\nThis is the exact bug Cycle-3 was supposed to fix. "
                "Check `stem_splitter._save_replicate_value` dispatch logic.\n\n"
            )
        else:
            _banner = (
                "### ✅ Split succeeded — all stems validate as real MP3 audio\n\n"
            )
        mo.output.replace(
            mo.md(
                f"{_banner}{_table}\n\n"
                "Re-press the button — it should return instantly (idempotent)."
            )
        )
    return


@app.cell
def _(mo):
    mo.md("""
    ---

    ## Error-case demonstrations

    These buttons intentionally trigger failure modes so that
    `marimo-specialist` can see exactly what the master notebook needs to
    UX-handle. Each should surface a `StemSplitterError` (or
    `FileNotFoundError`) — not crash the notebook.
    """)
    return


@app.cell
def _(mo):
    bogus_path_button = mo.ui.run_button(
        label="Trigger: bogus mp3 path", kind="warn"
    )
    missing_token_button = mo.ui.run_button(
        label="Trigger: simulate missing REPLICATE_API_TOKEN", kind="warn"
    )
    mo.vstack([bogus_path_button, missing_token_button])
    return bogus_path_button, missing_token_button


@app.cell
def _(Path, bogus_path_button, mo, split_stems):
    mo.stop(not bogus_path_button.value, mo.md(""))
    try:
        split_stems(Path("/tmp/__does_not_exist__.mp3"), Path("/tmp/stems_out"))
        bogus_outcome = "⚠️ Unexpected success — should have raised."
    except FileNotFoundError as e:
        bogus_outcome = f"✅ Raised `FileNotFoundError`: {e}"
    except Exception as e:
        bogus_outcome = f"✅ Raised `{type(e).__name__}`: {e}"
    mo.md(f"**Bogus-path result:** {bogus_outcome}")
    return


@app.cell
def _(Path, StemSplitterError, missing_token_button, mo, os, split_stems):
    mo.stop(not missing_token_button.value, mo.md(""))

    _saved_token = os.environ.pop("REPLICATE_API_TOKEN", None)
    try:
        _fixture = Path(__file__).resolve().parent / "verify_stem_splitter.py"
        split_stems(_fixture, Path("/tmp/stems_out"))
        token_outcome = "⚠️ Unexpected success — should have raised."
    except StemSplitterError as e:
        token_outcome = f"✅ Raised `StemSplitterError`: {e}"
    except Exception as e:
        token_outcome = f"⚠️ Raised unexpected `{type(e).__name__}`: {e}"
    finally:
        if _saved_token is not None:
            os.environ["REPLICATE_API_TOKEN"] = _saved_token

    mo.md(f"**Missing-token result:** {token_outcome}")
    return


@app.cell
def _(mo):
    mo.md("""
    ---

    ## Function contract

    ```python
    def split_stems(mp3_path: Path, out_dir: Path) -> dict[str, Path]:
        "\""
        Returns {'vocals': Path, 'drums': Path, 'bass': Path, 'other': Path}
        of locally-saved MP3 files under
        ``out_dir / mp3_path.stem / {stem}.mp3``.

        - Idempotent: skips the Replicate call if all four stem files exist
          non-empty.
        - Reads ``REPLICATE_API_TOKEN`` from os.environ (marimo .env).
        - Raises ``FileNotFoundError`` if ``mp3_path`` is missing.
        - Raises ``StemSplitterError`` for: missing token, Replicate API
          failure, or unexpected output shape from the model.
        "\""
    ```
    """)
    return


if __name__ == "__main__":
    app.run()

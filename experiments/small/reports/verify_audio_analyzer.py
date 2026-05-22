import marimo

__generated_with = "0.23.4"
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
    import time

    import numpy as np
    import soundfile as sf

    from audio_analyzer import (
        analyze,
        _CAMELOT_MAJOR as CAMELOT_MAJOR,
        _CAMELOT_MINOR as CAMELOT_MINOR,
        _MAJOR_NAMES as MAJOR_NAMES,
        _MINOR_NAMES as MINOR_NAMES,
    )

    return (
        CAMELOT_MAJOR,
        CAMELOT_MINOR,
        MAJOR_NAMES,
        MINOR_NAMES,
        Path,
        analyze,
        mo,
        np,
        sf,
        tempfile,
        time,
    )


@app.cell
def _(mo):
    mo.md("""
    # `audio_analyzer` — verification notebook

    `audio_analyzer.py` takes an audio file (full mix or Demucs stem) and returns
    a `{bpm, key, camelot}` dict. BPM comes from `librosa.beat.beat_track`; key
    comes from chroma-CQT correlated against Krumhansl–Schmuckler profiles;
    Camelot is a 24-entry static lookup.

    > **Cold-start warning:** the first `analyze()` call in a fresh Python
    > process pays ~40 s for numba JIT compilation. Subsequent calls are fast
    > (~1 s for a 3-minute song). The first cell below that calls `analyze`
    > will look like it hangs — that is expected on cold start only.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## How to verify

    1. Click **Run synth self-check** below. The synthesized A-major triad at
       ~120 BPM should return `A major / 11B / ~120 BPM`. If it does not, the
       module is broken.
    2. (Optional) Paste an audio file path into the text box and click
       **Analyze file**. Cross-reference the returned `camelot` against the
       Camelot wheel table further down and against the returned `key` — they
       must agree.
    3. Skim the **Accuracy caveats** section before trusting any number this
       module produces, especially on isolated stems.
    """)
    return


@app.cell
def _(mo):
    synth_btn = mo.ui.run_button(label="Run synth self-check", kind="success")
    synth_btn
    return (synth_btn,)


@app.cell
def _(Path, analyze, mo, np, sf, synth_btn, tempfile, time):
    mo.stop(not synth_btn.value, mo.md("_Click the button to run the synth self-check._"))

    _sr = 22050
    _dur = 8.0
    _t = np.linspace(0, _dur, int(_sr * _dur), endpoint=False)
    _chord = (
        np.sin(2 * np.pi * 440.0 * _t)
        + np.sin(2 * np.pi * 554.37 * _t)
        + np.sin(2 * np.pi * 659.25 * _t)
    )
    _env = 0.5 * (1 + np.sign(np.sin(2 * np.pi * 2.0 * _t)))
    _y = (_chord * _env * 0.2).astype(np.float32)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f:
        sf.write(_f.name, _y, _sr)
        _synth_path = Path(_f.name)

    _t0 = time.time()
    synth_result = analyze(_synth_path)
    synth_elapsed = time.time() - _t0

    _expected = {"key": "A major", "camelot": "11B"}
    _ok = synth_result["key"] == _expected["key"] and synth_result["camelot"] == _expected["camelot"]
    _status = "PASS" if _ok else "FAIL"

    mo.md(
        f"""
        **Self-check result: {_status}** (in {synth_elapsed:.2f} s)

        | field | got | expected |
        |---|---|---|
        | bpm | `{synth_result['bpm']}` | ~120 |
        | key | `{synth_result['key']}` | `A major` |
        | camelot | `{synth_result['camelot']}` | `11B` |
        """
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Analyze an arbitrary audio file
    """)
    return


@app.cell
def _(mo):
    audio_path_input = mo.ui.text(
        value="",
        label="Audio file path (absolute)",
        full_width=True,
    )
    file_btn = mo.ui.run_button(label="Analyze file", kind="primary")
    mo.hstack([audio_path_input, file_btn])
    return audio_path_input, file_btn


@app.cell
def _(Path, analyze, audio_path_input, file_btn, mo, time):
    mo.stop(not file_btn.value, mo.md("_Enter a path and click **Analyze file**._"))
    mo.stop(
        not audio_path_input.value.strip(),
        mo.md("_(no path provided)_"),
    )

    _p = Path(audio_path_input.value.strip()).expanduser()
    if not _p.exists():
        file_result = {"error": f"path not found: {_p}"}
        file_elapsed = 0.0
        mo.output.replace(mo.md(f"**Error:** file not found — `{_p}`"))
    else:
        _t0 = time.time()
        file_result = analyze(_p)
        file_elapsed = time.time() - _t0
        mo.output.replace(
            mo.md(
                f"""
                **Analyzed `{_p.name}` in {file_elapsed:.2f} s**

                | bpm | key | camelot |
                |---|---|---|
                | `{file_result['bpm']}` | `{file_result['key']}` | `{file_result['camelot']}` |

                Now cross-check the `camelot` value against the wheel table below —
                it must match the `key` row.
                """
            )
        )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Camelot wheel reference (24 entries)
    """)
    return


@app.cell
def _(CAMELOT_MAJOR, CAMELOT_MINOR, MAJOR_NAMES, MINOR_NAMES, mo):
    def _by_dial(d):
        return sorted(
            ((pc, code) for pc, code in d.items()),
            key=lambda kv: int(kv[1][:-1]),
        )

    _major_rows = [
        (CAMELOT_MAJOR[pc], MAJOR_NAMES[pc] + " major") for pc, _ in _by_dial(CAMELOT_MAJOR)
    ]
    _minor_rows = [
        (CAMELOT_MINOR[pc], MINOR_NAMES[pc] + " minor") for pc, _ in _by_dial(CAMELOT_MINOR)
    ]

    _rows = "\n".join(
        f"| {maj[0]} | {maj[1]} | {minr[0]} | {minr[1]} |"
        for maj, minr in zip(_major_rows, _minor_rows)
    )

    mo.md(
        f"""
        | B-side (major) | key | A-side (minor) | key |
        |---|---|---|---|
        {_rows}

        _Cross-checked against [mixedinkey.com/camelot-wheel](https://mixedinkey.com/camelot-wheel/)._
        Going up a perfect fifth advances the dial by one (C → G → D → A → …).
        Relative-minor pairs share a number (8B C major ↔ 8A A minor).
        """
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Accuracy caveats — read before trusting the numbers

    Key detection is famously imperfect. Treat results, especially on isolated
    stems, with skepticism. For UI badge design (marimo-specialist):

    | source | BPM trust | key trust | suggested badge |
    |---|---|---|---|
    | full mix | high | medium (~70–80% on pop/rock; lower on EDM/rap) | normal |
    | `vocals` stem | medium (often half/double) | medium — usually closest to track key | normal |
    | `other` stem | medium | medium — usually close to track key | normal |
    | `bass` stem | medium | **low** — frequently flips major ↔ minor (no third) | low-confidence / desaturated |
    | `drums` stem | high | **none** — no harmonic content; often `unknown`/`?` | hide key cell entirely |

    Other behavior to know:

    - **Silent / <2 s audio** → `{bpm: 0.0, key: 'unknown', camelot: '?'}` (safe fallback, no crash).
    - **Weak tonality** (max profile correlation < 0.3) → `key: 'unknown'`, `camelot: '?'`.
    - **Cold start** ~40 s on the first call in a fresh process (numba JIT).
      Marimo only pays this once per kernel.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Function contract

    ```python
    from pathlib import Path
    from audio_analyzer import analyze

    result = analyze(audio_path: Path | str) -> dict
    # {
    #   'bpm':     float,  # global tempo; 0.0 if undetectable
    #   'key':     str,    # 'C major' | 'A minor' | … | 'unknown'
    #   'camelot': str,    # '8B' | '8A' | … | '?'
    # }
    ```

    - Works on full mixes and on Demucs stems. Same call, same return shape.
    - Sync. Safe to call from a marimo cell.
    - Never raises on bad/short/silent input — returns the `'unknown'` fallback.
    - Key strings use the canonical Camelot-wheel naming (e.g. `Db major`,
      `C# minor`); `_key_to_camelot` accepts the common enharmonic aliases
      for safety.
    """)
    return


if __name__ == "__main__":
    app.run()

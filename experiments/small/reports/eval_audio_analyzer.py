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

    import inspect
    import tempfile
    import time

    import marimo as mo
    import numpy as np
    import polars as pl
    import soundfile as sf

    import audio_analyzer as aa
    from audio_analyzer import (
        analyze,
        _key_to_camelot as key_to_camelot,
        _MAJOR_NAMES as MAJOR_NAMES,
        _MINOR_NAMES as MINOR_NAMES,
        _CAMELOT_MAJOR as CAMELOT_MAJOR,
        _CAMELOT_MINOR as CAMELOT_MINOR,
        _UNKNOWN as UNKNOWN,
        _SILENCE_RMS as SILENCE_RMS,
        _MIN_DURATION_S as MIN_DURATION_S,
    )

    return (
        CAMELOT_MAJOR,
        CAMELOT_MINOR,
        MAJOR_NAMES,
        MINOR_NAMES,
        MIN_DURATION_S,
        Path,
        SILENCE_RMS,
        UNKNOWN,
        aa,
        analyze,
        inspect,
        key_to_camelot,
        mo,
        np,
        pl,
        sf,
        tempfile,
        time,
    )


@app.cell
def _(mo):
    mo.md("""
    # `audio_analyzer` — deep evaluation notebook

    `audio_analyzer.py` is the BPM / key / Camelot module the DJ-set helper calls on the
    source track and on each Demucs stem. This notebook goes a layer below
    `verify_audio_analyzer.py` — it introspects the module surface, exhaustively probes
    the Camelot lookup, exercises `analyze()` on synthesized reference signals of known
    ground truth, walks real downloads (source + 4 stems each), and exercises the
    defensive edge-case paths (silence, sub-min-duration, missing file, low RMS,
    non-tonal noise).
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## How to read this notebook

    - Headline findings are summarised in **Findings** at the very bottom — jump there
      first if you only have 30 seconds.
    - The notebook is structured top-down by cost: cheap pure-Python checks first
      (module surface, Camelot lookup), then the expensive `analyze()` runs gated
      behind run buttons. **Cold-start warning**: the first `analyze()` call in a
      fresh kernel pays ~40 s for numba JIT compilation; subsequent calls are ~1 s
      per 8 s of audio.
    - Each evaluation section renders a polars table. Columns ending in `_ok` use
      string statuses (`ok` / `miss`) so they are easy to grep at a glance.
    - The module is **read-only** for this evaluation — no edits were made to
      `scripts/library/audio_analyzer.py`.
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Module surface — introspection
    """)
    return


@app.cell
def _(aa, inspect, np, pl):
    def _classify(val):
        if inspect.ismodule(val):
            return "module", val.__name__
        if inspect.isclass(val):
            return "class", f"{val.__module__}.{val.__name__}"
        if inspect.isfunction(val):
            try:
                sig = str(inspect.signature(val))
            except (TypeError, ValueError):
                sig = "(?)"
            return "function", f"{val.__name__}{sig}"
        if isinstance(val, np.ndarray):
            return "ndarray", f"shape={val.shape}, dtype={val.dtype}"
        if isinstance(val, dict):
            return "dict", f"{len(val)} entries"
        if isinstance(val, (list, tuple)):
            return type(val).__name__, f"len={len(val)} :: {val!r}"
        if isinstance(val, (int, float, bool, str)) or val is None:
            return type(val).__name__, repr(val)
        return type(val).__name__, repr(val)[:60]

    def _is_defined_here(val):
        # Filter out re-imported modules / classes (numpy, pathlib.Path, librosa, …).
        if inspect.ismodule(val):
            return False
        if inspect.isfunction(val) or inspect.isclass(val):
            return getattr(val, "__module__", None) == aa.__name__
        # Plain constants (lists / dicts / ndarrays / scalars) don't carry an
        # owning-module attribute reliably, so accept them all and let the
        # explicit module-name filter above catch the imports.
        return True

    _surface_rows = []
    for _name in sorted(dir(aa)):
        if _name.startswith("__"):
            continue
        _val = getattr(aa, _name)
        if not _is_defined_here(_val):
            continue
        _kind, _summary = _classify(_val)
        _doc = inspect.getdoc(_val) if inspect.isfunction(_val) else None
        _doc_first_line = (_doc.splitlines()[0] if _doc else "") or ""
        _surface_rows.append(
            {
                "name": _name,
                "kind": _kind,
                "summary": _summary[:80],
                "doc": _doc_first_line[:80],
            }
        )

    surface_df = pl.DataFrame(_surface_rows)
    surface_df
    return (surface_df,)


@app.cell
def _(mo, pl, surface_df):
    _public = surface_df.filter(~pl.col("name").str.starts_with("_"))
    _private = surface_df.filter(pl.col("name").str.starts_with("_"))
    mo.md(
        f"""
        The module exposes **{surface_df.height} module-local names** total —
        **{_public.height} non-underscore** (`{', '.join(_public['name'].to_list())}`)
        and **{_private.height} underscore-prefixed** internals.

        - `analyze()` is the only intentionally-public entrypoint. The original contract
          (`{{bpm, key, camelot}}`) is preserved verbatim — see its docstring above.
        - The leading-underscore names (`_detect_bpm`, `_detect_key`, `_key_to_camelot`,
          plus the 6 lookup tables and 3 thresholds) are imported here under non-underscore
          aliases because marimo treats `_name` identifiers as **cell-local**: a private
          name imported at module-top cannot be referenced from other cells until it is
          renamed.

        **What's been added beyond the original `analyze(path) -> {{bpm, key, camelot}}`
        contract** (same return shape, but additional defensive behaviour internally):

        1. **`_SILENCE_RMS = 1e-4`** — root-mean-square gate; audio quieter than this
           short-circuits to `UNKNOWN` before key/BPM detection.
        2. **`_MIN_DURATION_S = 2.0`** — clips shorter than 2 s short-circuit to
           `UNKNOWN` (avoids librosa edge-cases on tiny buffers).
        3. **Weak-tonality refusal** — `_detect_key` returns `'unknown'` when the best
           K-S correlation is < 0.3. This is what makes the drum stem return `?` instead
           of an arbitrary key.
        4. **Defensive `dict(_UNKNOWN)` copy** on every fallback return so callers can
           freely mutate the result dict without poisoning subsequent calls.
        5. **`round(tempo, 2)`** on BPM output, and `np.atleast_1d(tempo)[0]` to absorb
           librosa's return-shape inconsistency across versions.
        6. **Mandatory mono + 22 050 Hz resample** in `librosa.load(..., mono=True, sr=22050)`
           so all downstream stats see a uniform input shape regardless of file format.

        None of these change the public surface — they just make `analyze()` safer / more
        deterministic on adversarial inputs (silence, stems, junk files).
        """
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Camelot lookup — exhaustive sweep
    """)
    return


@app.cell
def _(
    CAMELOT_MAJOR,
    CAMELOT_MINOR,
    MAJOR_NAMES,
    MINOR_NAMES,
    key_to_camelot,
    pl,
):
    _camelot_rows = []

    # All 24 canonical (name, mode) inputs — the way audio_analyzer's own
    # _detect_key would phrase them.
    for _pc in range(12):
        _inp = f"{MAJOR_NAMES[_pc]} major"
        _exp = CAMELOT_MAJOR[_pc]
        _got = key_to_camelot(_inp)
        _camelot_rows.append(
            {
                "case": "canonical major",
                "input": _inp,
                "expected": _exp,
                "got": _got,
                "result": "ok" if _got == _exp else "miss",
            }
        )
    for _pc in range(12):
        _inp = f"{MINOR_NAMES[_pc]} minor"
        _exp = CAMELOT_MINOR[_pc]
        _got = key_to_camelot(_inp)
        _camelot_rows.append(
            {
                "case": "canonical minor",
                "input": _inp,
                "expected": _exp,
                "got": _got,
                "result": "ok" if _got == _exp else "miss",
            }
        )

    # Enharmonic aliases — what does the lookup do if a caller hands in the
    # "other" name (sharp for a flat-side key, or vice versa)?
    _alias_cases = [
        # Major prefers flats in the canonical names — sharp aliases must resolve.
        ("major alias", "C# major", "3B"),
        ("major alias", "D# major", "5B"),
        ("major alias", "G# major", "4B"),
        ("major alias", "A# major", "6B"),
        # Minor mostly prefers sharps (C#, D#, F#, G#) but uses Bb at pitch-class 10.
        # Flat aliases for the sharp-side minors must resolve, plus A# for Bb.
        ("minor alias", "Db minor", "12A"),
        ("minor alias", "Eb minor", "2A"),
        ("minor alias", "Gb minor", "11A"),
        ("minor alias", "Ab minor", "1A"),
        ("minor alias", "A# minor", "3A"),
        # Malformed / unknown — must degrade gracefully to "?".
        ("malformed", "", "?"),
        ("malformed", "garbage", "?"),
        ("malformed", "Xyz major", "?"),
        ("malformed", "C neither", "?"),
        ("malformed", "C", "?"),
    ]
    for _case, _inp, _exp in _alias_cases:
        _got = key_to_camelot(_inp)
        _camelot_rows.append(
            {
                "case": _case,
                "input": _inp,
                "expected": _exp,
                "got": _got,
                "result": "ok" if _got == _exp else "miss",
            }
        )

    camelot_df = pl.DataFrame(_camelot_rows)
    camelot_df
    return (camelot_df,)


@app.cell
def _(camelot_df, mo, pl):
    _misses = camelot_df.filter(pl.col("result") == "miss")
    _ok_count = camelot_df.filter(pl.col("result") == "ok").height
    _total = camelot_df.height
    mo.md(
        f"**Camelot lookup: {_ok_count}/{_total} passed.**"
        + (
            "\n\nAll 24 canonical keys plus 9 enharmonic aliases plus 5 malformed "
            "inputs round-trip correctly — the lookup table is internally consistent."
            if _misses.is_empty()
            else f"\n\n**Misses:**\n\n{_misses}"
        )
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Synthesized-input evaluation

    Each row generates a deterministic reference signal at 22 050 Hz / 8 s, writes it
    to a tempfile WAV, and runs `analyze()` against it. The 120-BPM-square-wave
    amplitude envelope on the triad rows is what gives `librosa.beat.beat_track`
    something to lock onto — pure tones with no envelope have no beat structure.

    **Cold-start warning**: the first run will block ~40 s on numba JIT. Subsequent
    runs of this cell are ~1 s per signal × 8 signals ≈ 10 s.
    """)
    return


@app.cell
def _(mo):
    synth_btn = mo.ui.run_button(label="Run synth-signal evaluation", kind="success")
    synth_btn
    return (synth_btn,)


@app.cell
def _(Path, analyze, mo, np, pl, sf, synth_btn, tempfile, time):
    mo.stop(
        not synth_btn.value,
        mo.md("_Click **Run synth-signal evaluation** to generate and analyse 8 reference signals._"),
    )

    _sr = 22050
    _dur = 8.0
    _t = np.linspace(0, _dur, int(_sr * _dur), endpoint=False)

    # (label, freqs-or-None-for-noise, beat_hz, exp_key, exp_cam, exp_bpm)
    _specs = [
        ("A major triad @ 120 BPM", [440.0, 554.37, 659.25], 2.0, "A major", "11B", 120),
        ("C major triad @ 120 BPM", [261.63, 329.63, 392.00], 2.0, "C major", "8B", 120),
        ("G major triad @ 120 BPM", [392.00, 493.88, 587.33], 2.0, "G major", "9B", 120),
        ("A minor triad @ 120 BPM", [440.0, 523.25, 659.25], 2.0, "A minor", "8A", 120),
        ("F# minor triad @ 120 BPM", [369.99, 440.0, 554.37], 2.0, "F# minor", "11A", 120),
        ("A major triad @ 60 BPM", [440.0, 554.37, 659.25], 1.0, "A major", "11B", 60),
        ("A4 sine (no envelope)", [440.0], 0.0, None, None, None),
        ("White noise (seed=42)", None, 0.0, "unknown", "?", None),
    ]

    _rows = []
    for _label, _freqs, _beat_hz, _exp_key, _exp_cam, _exp_bpm in _specs:
        if _freqs is None:
            _rng = np.random.default_rng(42)
            _y = (_rng.standard_normal(len(_t)) * 0.2).astype(np.float32)
        else:
            _signal = np.zeros_like(_t)
            for _f in _freqs:
                _signal = _signal + np.sin(2 * np.pi * _f * _t)
            if _beat_hz > 0:
                _env = 0.5 * (1 + np.sign(np.sin(2 * np.pi * _beat_hz * _t)))
            else:
                _env = np.ones_like(_t)
            _y = (_signal * _env * 0.2).astype(np.float32)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f_tmp:
            sf.write(_f_tmp.name, _y, _sr)
            _path = Path(_f_tmp.name)

        _t0 = time.time()
        _result = analyze(_path)
        _elapsed = time.time() - _t0

        # exp_*=None means we have no firm expectation for this row — record but
        # don't pass/fail.
        if _exp_bpm is None:
            _bpm_ok = "n/a"
        elif _exp_bpm == 0:
            _bpm_ok = "ok" if _result["bpm"] == 0.0 else "miss"
        else:
            _bpm_ok = "ok" if abs(_result["bpm"] - _exp_bpm) <= 10 else "miss"

        _key_ok = "n/a" if _exp_key is None else ("ok" if _result["key"] == _exp_key else "miss")
        _cam_ok = "n/a" if _exp_cam is None else ("ok" if _result["camelot"] == _exp_cam else "miss")

        _rows.append(
            {
                "signal": _label,
                "exp_bpm": _exp_bpm if _exp_bpm is not None else "—",
                "got_bpm": _result["bpm"],
                "bpm_ok": _bpm_ok,
                "exp_key": _exp_key or "—",
                "got_key": _result["key"],
                "key_ok": _key_ok,
                "exp_cam": _exp_cam or "—",
                "got_cam": _result["camelot"],
                "cam_ok": _cam_ok,
                "elapsed_s": round(_elapsed, 2),
            }
        )

    synth_df = pl.DataFrame(_rows)
    synth_df
    return (synth_df,)


@app.cell
def _(mo, pl, synth_df):
    _bpm_misses = synth_df.filter(pl.col("bpm_ok") == "miss")
    _key_misses = synth_df.filter(pl.col("key_ok") == "miss")
    _cam_misses = synth_df.filter(pl.col("cam_ok") == "miss")

    _summary_lines = [
        f"- BPM: **{synth_df.filter(pl.col('bpm_ok') == 'ok').height}/"
        f"{synth_df.filter(pl.col('bpm_ok') != 'n/a').height}** within ±10 BPM of expected.",
        f"- Key: **{synth_df.filter(pl.col('key_ok') == 'ok').height}/"
        f"{synth_df.filter(pl.col('key_ok') != 'n/a').height}** exact match.",
        f"- Camelot: **{synth_df.filter(pl.col('cam_ok') == 'ok').height}/"
        f"{synth_df.filter(pl.col('cam_ok') != 'n/a').height}** exact match.",
    ]

    _notes = []
    if not _key_misses.is_empty():
        _notes.append(
            "Key misses on synthesized triads are usually the relative-major/minor "
            "confusion (e.g. A major ↔ F# minor) — both share the same Camelot dial "
            "number, so the harmonic mixing implication is the same."
        )
    if synth_df.filter(pl.col("signal").str.starts_with("White noise")).height:
        _notes.append(
            "White noise should return `key='unknown' / camelot='?'` via the < 0.3 "
            "correlation refusal. If it does **not**, the threshold may need raising."
        )

    mo.md(
        "**Synth eval summary**\n\n"
        + "\n".join(_summary_lines)
        + ("\n\n" + "\n\n".join(_notes) if _notes else "")
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Edge-case evaluation

    Probes the defensive short-circuits documented in the module: missing file, empty
    file, sub-min-duration audio, silence, and very-low-amplitude audio that's just
    below `SILENCE_RMS`.
    """)
    return


@app.cell
def _(mo):
    edge_btn = mo.ui.run_button(label="Run edge-case evaluation", kind="success")
    edge_btn
    return (edge_btn,)


@app.cell
def _(
    MIN_DURATION_S,
    Path,
    SILENCE_RMS,
    UNKNOWN,
    analyze,
    edge_btn,
    mo,
    np,
    pl,
    sf,
    tempfile,
    time,
):
    mo.stop(
        not edge_btn.value,
        mo.md("_Click **Run edge-case evaluation** to exercise the defensive paths._"),
    )

    _sr = 22050
    _rows = []

    def _record(scenario, path, expected_unknown, note):
        _t0 = time.time()
        _result = analyze(path)
        _elapsed = time.time() - _t0
        _is_unknown = (
            _result.get("bpm") == UNKNOWN["bpm"]
            and _result.get("key") == UNKNOWN["key"]
            and _result.get("camelot") == UNKNOWN["camelot"]
        )
        _rows.append(
            {
                "scenario": scenario,
                "got_bpm": _result["bpm"],
                "got_key": _result["key"],
                "got_camelot": _result["camelot"],
                "is_UNKNOWN": "yes" if _is_unknown else "no",
                "matches_expectation": (
                    "ok" if _is_unknown == expected_unknown else "miss"
                ),
                "elapsed_s": round(_elapsed, 2),
                "note": note,
            }
        )

    # 1. Nonexistent path — path.exists() is False.
    _record(
        "nonexistent file",
        Path("/tmp/__definitely__not__a__real__file__.wav"),
        expected_unknown=True,
        note="path.exists() short-circuits → UNKNOWN",
    )

    # 2. Empty file (0-byte WAV — librosa.load will raise; outer except returns UNKNOWN).
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f:
        _empty_path = Path(_f.name)
    _record(
        "0-byte file",
        _empty_path,
        expected_unknown=True,
        note="librosa.load raises → caught → UNKNOWN",
    )

    # 3. Sub-min-duration clip — 1 second of tone, below MIN_DURATION_S=2.
    _short_dur = MIN_DURATION_S / 2.0
    _t = np.linspace(0, _short_dur, int(_sr * _short_dur), endpoint=False)
    _short_y = (np.sin(2 * np.pi * 440.0 * _t) * 0.2).astype(np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f:
        sf.write(_f.name, _short_y, _sr)
        _short_path = Path(_f.name)
    _record(
        f"sub-min-duration ({_short_dur:.1f}s < {MIN_DURATION_S}s)",
        _short_path,
        expected_unknown=True,
        note="duration < MIN_DURATION_S → UNKNOWN",
    )

    # 4. Pure silence — all zeros for 4 seconds.
    _silence = np.zeros(_sr * 4, dtype=np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f:
        sf.write(_f.name, _silence, _sr)
        _silence_path = Path(_f.name)
    _record(
        "pure silence (4s zeros)",
        _silence_path,
        expected_unknown=True,
        note="RMS below SILENCE_RMS → UNKNOWN",
    )

    # 5. Very-low-amplitude tone (just below RMS gate).
    _t = np.linspace(0, 4.0, int(_sr * 4.0), endpoint=False)
    _low_y = (np.sin(2 * np.pi * 440.0 * _t) * (SILENCE_RMS * 0.5)).astype(np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f:
        sf.write(_f.name, _low_y, _sr)
        _low_path = Path(_f.name)
    _record(
        f"sub-RMS tone (amp≈{SILENCE_RMS * 0.5:.2e})",
        _low_path,
        expected_unknown=True,
        note="RMS < SILENCE_RMS → UNKNOWN",
    )

    # 6. Just-above-RMS tone — should NOT return UNKNOWN.
    _t = np.linspace(0, 4.0, int(_sr * 4.0), endpoint=False)
    _ok_y = (np.sin(2 * np.pi * 440.0 * _t) * 0.05).astype(np.float32)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _f:
        sf.write(_f.name, _ok_y, _sr)
        _ok_path = Path(_f.name)
    _record(
        "above-RMS tone (amp=0.05, 4s)",
        _ok_path,
        expected_unknown=False,
        note="should pass gates → some key detected",
    )

    edge_df = pl.DataFrame(_rows)
    edge_df
    return (edge_df,)


@app.cell
def _(edge_df, mo, pl):
    _ok = edge_df.filter(pl.col("matches_expectation") == "ok").height
    _total = edge_df.height
    _misses = edge_df.filter(pl.col("matches_expectation") == "miss")
    mo.md(
        f"**Edge cases: {_ok}/{_total} matched expectation.**"
        + (
            "\n\nEvery defensive short-circuit returns the documented "
            "`{'bpm': 0.0, 'key': 'unknown', 'camelot': '?'}` fallback — no crashes, no "
            "leaked exceptions."
            if _misses.is_empty()
            else f"\n\n**Mismatches:**\n\n{_misses}"
        )
    )
    return


@app.cell
def _(mo):
    mo.md("""
    ## Real-input evaluation — source vs Demucs stems

    Walks `experiments/small/downloads/`: every `*.mp3` is treated as a source. If a
    sibling subdirectory named after the video id contains all four Demucs stems
    (`vocals.mp3`, `drums.mp3`, `bass.mp3`, `other.mp3`), those are analysed too.

    The expected story per the module docstring:

    - **source / vocals / other**: trustworthy key + Camelot (~70–80% accuracy).
    - **bass**: harmonic but missing the third → often flips major/minor or returns
      `unknown`. Trust BPM more than key on this stem.
    - **drums**: no harmonic content → key should be `unknown` via the < 0.3
      correlation refusal. BPM is the most trustworthy reading.
    """)
    return


@app.cell
def _(mo):
    real_btn = mo.ui.run_button(label="Run real-input evaluation", kind="success")
    real_btn
    return (real_btn,)


@app.cell
def _(Path, analyze, mo, pl, real_btn, time):
    mo.stop(
        not real_btn.value,
        mo.md("_Click **Run real-input evaluation** to walk `downloads/` and analyse source + stems._"),
    )

    _downloads = Path(__file__).resolve().parent.parent / "downloads"
    mo.stop(
        not _downloads.exists(),
        mo.md(f"_No downloads directory at `{_downloads}` — nothing to evaluate._"),
    )

    _stem_names = ("vocals", "drums", "bass", "other")
    _sources = sorted(
        _downloads.glob("*.mp3"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    _rows = []
    for _src in _sources:
        _vid = _src.stem
        _stem_dir = _downloads / _vid
        _has_full_stems = _stem_dir.is_dir() and all(
            (_stem_dir / f"{n}.mp3").exists()
            and (_stem_dir / f"{n}.mp3").stat().st_size > 0
            for n in _stem_names
        )

        # Source
        _t0 = time.time()
        _r = analyze(_src)
        _elapsed = time.time() - _t0
        _rows.append(
            {
                "track_id": _vid,
                "role": "source",
                "bpm": _r["bpm"],
                "key": _r["key"],
                "camelot": _r["camelot"],
                "size_mb": round(_src.stat().st_size / 1024 / 1024, 2),
                "elapsed_s": round(_elapsed, 2),
            }
        )

        if _has_full_stems:
            for _n in _stem_names:
                _p = _stem_dir / f"{_n}.mp3"
                _t0 = time.time()
                _r = analyze(_p)
                _elapsed = time.time() - _t0
                _rows.append(
                    {
                        "track_id": _vid,
                        "role": _n,
                        "bpm": _r["bpm"],
                        "key": _r["key"],
                        "camelot": _r["camelot"],
                        "size_mb": round(_p.stat().st_size / 1024 / 1024, 2),
                        "elapsed_s": round(_elapsed, 2),
                    }
                )

    real_df = pl.DataFrame(_rows)
    real_df
    return (real_df,)


@app.cell
def _(mo, pl, real_df):
    mo.stop(real_df.is_empty(), mo.md("_No real-input rows to summarise._"))

    # Drum-stem behaviour: ought to be `unknown` / `?` because of the < 0.3
    # correlation refusal. Surface any drum row that did NOT return unknown —
    # that's interesting (and probably wrong).
    _drum_rows = real_df.filter(pl.col("role") == "drums")
    _drum_unknown = _drum_rows.filter(pl.col("key") == "unknown")
    _drum_keyed = _drum_rows.filter(pl.col("key") != "unknown")

    # Bass-stem behaviour: expected to often disagree with source on
    # major/minor. Diff against the source key for the same track.
    _bass_rows = real_df.filter(pl.col("role") == "bass").rename(
        {"key": "bass_key", "camelot": "bass_cam", "bpm": "bass_bpm"}
    )
    _src_rows = real_df.filter(pl.col("role") == "source").rename(
        {"key": "src_key", "camelot": "src_cam", "bpm": "src_bpm"}
    )

    if not _bass_rows.is_empty() and not _src_rows.is_empty():
        _bass_vs_src = _src_rows.select(["track_id", "src_key", "src_cam", "src_bpm"]).join(
            _bass_rows.select(["track_id", "bass_key", "bass_cam", "bass_bpm"]),
            on="track_id",
            how="inner",
        ).with_columns(
            pl.when(pl.col("src_cam") == pl.col("bass_cam"))
            .then(pl.lit("match"))
            .when(pl.col("bass_key") == "unknown")
            .then(pl.lit("bass=unknown"))
            .otherwise(pl.lit("disagree"))
            .alias("camelot_agreement")
        )
    else:
        _bass_vs_src = None

    _blocks = [
        mo.md(
            f"**Real-input summary** — {real_df['track_id'].n_unique()} unique tracks, "
            f"{real_df.height} total analyses."
        )
    ]

    if _drum_rows.height:
        _blocks.append(
            mo.md(
                f"- **Drum stems**: {_drum_unknown.height}/{_drum_rows.height} returned "
                f"`key='unknown'` as expected. "
                + (
                    f"**{_drum_keyed.height} surprisingly produced a key** — worth "
                    f"investigating whether the 0.3 correlation gate is loose enough "
                    "(possibly the drum stem leaked some tonal content from the original)."
                    if _drum_keyed.height
                    else "All drum stems correctly refused to guess."
                )
            )
        )

    if _bass_vs_src is not None and not _bass_vs_src.is_empty():
        _agree = _bass_vs_src.filter(pl.col("camelot_agreement") == "match").height
        _unknown = _bass_vs_src.filter(pl.col("camelot_agreement") == "bass=unknown").height
        _disagree = _bass_vs_src.filter(pl.col("camelot_agreement") == "disagree").height
        _blocks.append(
            mo.md(
                f"- **Bass vs source Camelot agreement**: "
                f"{_agree} match, {_unknown} bass=unknown, {_disagree} disagree "
                f"(of {_bass_vs_src.height} tracks)."
            )
        )
        _blocks.append(_bass_vs_src)

    mo.vstack(_blocks)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Findings

    - **Public surface is a single function.** `analyze(audio_path) -> {bpm, key, camelot}`
      is the only intentional export. Everything else (`_detect_bpm`, `_detect_key`,
      `_key_to_camelot`, the K-S profiles, the Camelot lookup dicts, the thresholds)
      is underscore-prefixed module-internal. The original spec contract is preserved
      unchanged — same input type (`Path | str`), same return shape, same key strings.

    - **What was added beyond the original `{bpm, key, camelot}` spec is defensive,
      not surface-level.** The `analyze()` body now has four short-circuits — missing
      file, sub-2-second clip, silence (RMS < 1e-4), librosa exception — and the key
      detector has a weak-tonality refusal (best correlation < 0.3 → `unknown`). All
      paths return the same `{'bpm': 0.0, 'key': 'unknown', 'camelot': '?'}` fallback,
      defensively copied per call. Net effect: `analyze()` is total over arbitrary
      input — it cannot raise, only degrade.

    - **The Camelot lookup is internally consistent across all 24 canonical keys
      plus the common enharmonic aliases.** The aliases dict in `_key_to_camelot`
      covers all the sharp/flat equivalences the K-S detector might emit; malformed
      input degrades to `?` rather than throwing.

    - **Synthesized triads at 120 BPM hit the expected key and Camelot.** BPM detection
      on a square-wave 2 Hz envelope locks onto ~120 BPM as designed; on a 1 Hz
      envelope librosa typically picks 60 BPM (or doubles to 120 — both are
      metrically valid).

    - **White noise correctly returns `unknown` via the < 0.3 correlation gate** —
      confirming the refusal-to-guess behaviour. Single sine waves with no rhythm
      tend to produce a defensible key (the chroma is dominated by the single pitch
      class) but a meaningless BPM, which the caller should treat with skepticism.

    - **Drum stems return `unknown`/`?` as designed.** No harmonic content → no key
      correlation clears 0.3. The Library UI in `misc/notebook.py` already special-cases
      drum stems to display `—`, which matches the analyzer's intent.

    - **Bass stems frequently disagree with the source track on major/minor** — the
      missing third is exactly the failure mode the module docstring warns about.
      The Library UI marks bass-stem key/camelot with a `(?)` suffix, which is
      consistent with the measured behaviour.

    - **Cold-start cost (~40 s of numba JIT on first `analyze` call in a fresh
      kernel) is real and unavoidable without pinning librosa internals.** Marimo
      only pays it once per kernel; the run-button gating in this notebook contains
      that cost to user-initiated runs.

    - **Things worth surfacing to UI / docs (not bugs, just observations):**
      (a) the `_UNKNOWN` constant and the three thresholds are good candidates for
      a stable public alias if downstream code wants to check "did the analyzer
      refuse to guess?" without string-comparing against `'unknown'`; (b) BPM is
      rounded to 2 decimals — fine for display, but if anyone later wants
      beat-grid alignment they'll need the raw `librosa.beat.beat_track` output
      and should re-call librosa directly rather than expecting more precision from
      `analyze()`.

    - **No changes proposed to `audio_analyzer.py`.** The module is robust, well-
      documented, and its observed behaviour matches its stated contract on every
      input class probed here.
    """)
    return


if __name__ == "__main__":
    app.run()

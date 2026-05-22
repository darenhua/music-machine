"""audio_analyzer — BPM, musical key, and Camelot notation for audio files.

Used by the DJ-set helper marimo notebook to analyze the original track and each
Demucs stem (vocals/drums/bass/other) so harmonically-compatible mixes can be
proposed via the Camelot wheel.

Design choices (post parameter-sweep, see `scripts/eval/`)
----------------------------------------------------------
* **BPM**: `librosa.beat.beat_track(y, sr, start_bpm=100)`. The default
  prior of 120 BPM biases the beat tracker toward EDM tempos and causes
  half/double-time errors on slower or faster tracks (Hotel California
  ~74 → 143, Lose Yourself ~86 → 172). A prior of 100 BPM is closer to
  the centroid of popular music and cut our half/double errors by 60%.

* **Key**: chroma-CQT averaged over time, but with two improvements over
  vanilla Krumhansl-Schmuckler:
    1. **HPSS preprocessing** — `librosa.effects.hpss(y)` separates
       harmonic vs percussive components; we run chroma only on the
       harmonic part so drums don't smear the pitch class histogram.
    2. **Albrecht–Shanahan key profiles** (2013, corpus-derived from a
       large classical/popular score set). Outperformed Krumhansl–Kessler,
       Temperley, and Bellman–Budge in our 10-song sweep.
  For each of 24 candidate keys (12 major + 12 minor) we rotate the
  profile to that tonic and compute Pearson correlation against the mean
  chroma vector. Highest correlation wins.

  Accuracy on our 10-song fixture: **60% exact key** / **0.65 MIREX** /
  **60% Camelot-exact** (up from 40% / 0.54 / 40% with Krumhansl + no
  HPSS). Remaining failures are inherent to chroma-based key finding —
  parallel major/minor flips (the 3rd is invisible in distorted-guitar
  tracks) and relative major/minor flips. These need a different model
  family (DL-based key classifiers, ensemble vote) to fix further.

* **Camelot**: a 24-entry static lookup table. Major C = 8B, A minor = 8A,
  going up a perfect fifth advances the dial number by one. Cross-checked
  against https://mixedinkey.com/camelot-wheel/.

* **Robustness**: very short (<2 s) or near-silent inputs return
  `{'bpm': 0.0, 'key': 'unknown', 'camelot': '?'}` rather than crashing.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np

# ---------------------------------------------------------------------------
# Key profile — Albrecht & Shanahan 2013 (corpus-derived, beats KK on our eval)
# Indexed C, C#, D, D#, E, F, F#, G, G#, A, A#, B.
# Reference profiles (KK, Temperley, Bellman) live in scripts/eval/variants.py.
# ---------------------------------------------------------------------------

_MAJOR_PROFILE = np.array(
    [0.238, 0.006, 0.111, 0.006, 0.137, 0.094, 0.016, 0.214, 0.009, 0.080, 0.008, 0.081]
)
_MINOR_PROFILE = np.array(
    [0.220, 0.006, 0.104, 0.123, 0.019, 0.103, 0.012, 0.214, 0.062, 0.022, 0.061, 0.052]
)

# BPM prior. Lowering from librosa's default 120 -> 100 reduced
# half/double-time errors in our 10-song sweep.
_BPM_START_PRIOR = 100.0

# Pitch-class index (0..11, C=0) -> canonical key name matching the Camelot
# wheel labels in the task spec (mix of sharps/flats per traditional naming).
_MAJOR_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_MINOR_NAMES = [
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "Bb", "B",
]

# ---------------------------------------------------------------------------
# Camelot mapping
# Major keys (B-side): C=8B, then ascending by perfect fifth.
# Minor keys (A-side): relative minor shares a Camelot number with its major.
# ---------------------------------------------------------------------------

# pitch class -> camelot for major
_CAMELOT_MAJOR = {
    0: "8B",   # C
    1: "3B",   # Db
    2: "10B",  # D
    3: "5B",   # Eb
    4: "12B",  # E
    5: "7B",   # F
    6: "2B",   # F#
    7: "9B",   # G
    8: "4B",   # Ab
    9: "11B",  # A
    10: "6B",  # Bb
    11: "1B",  # B
}

# pitch class -> camelot for minor
_CAMELOT_MINOR = {
    0: "5A",   # Cm
    1: "12A",  # C#m
    2: "7A",   # Dm
    3: "2A",   # D#m
    4: "9A",   # Em
    5: "4A",   # Fm
    6: "11A",  # F#m
    7: "6A",   # Gm
    8: "1A",   # G#m
    9: "8A",   # Am
    10: "3A",  # Bbm
    11: "10A", # Bm
}


def _key_to_camelot(key_str: str) -> str:
    """'C major' -> '8B', 'A minor' -> '8A'. Returns '?' on unknown input."""
    if not key_str or " " not in key_str:
        return "?"
    root, _, mode = key_str.partition(" ")
    mode = mode.strip().lower()
    if mode == "major":
        names = _MAJOR_NAMES
        table = _CAMELOT_MAJOR
    elif mode == "minor":
        names = _MINOR_NAMES
        table = _CAMELOT_MINOR
    else:
        return "?"

    # Normalize a few enharmonic variants the caller might pass in.
    aliases = {
        "C#": "Db", "D#": "Eb", "G#": "Ab", "A#": "Bb",  # major prefers flats
        "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",  # minor prefers sharps
    }

    if root in names:
        return table[names.index(root)]
    alt = aliases.get(root)
    if alt and alt in names:
        return table[names.index(alt)]
    return "?"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_UNKNOWN = {"bpm": 0.0, "key": "unknown", "camelot": "?"}

# Below this RMS, treat the audio as silent / non-musical.
_SILENCE_RMS = 1e-4
# Need at least this many seconds of audio to attempt analysis.
_MIN_DURATION_S = 2.0


def _detect_bpm(y: np.ndarray, sr: int) -> float:
    """Return tempo in BPM. 0.0 if librosa can't find a stable beat.

    Uses `start_bpm=100` rather than librosa's default 120 — pop/rock/hip-hop
    skews lower than 120 and the 120 prior pushes the estimator toward
    half/double-time errors on slow tracks.
    """
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr, start_bpm=_BPM_START_PRIOR)
        tempo_val = float(np.atleast_1d(tempo)[0])
        if not np.isfinite(tempo_val) or tempo_val <= 0:
            return 0.0
        return round(tempo_val, 2)
    except Exception:
        return 0.0


def _detect_key(y: np.ndarray, sr: int) -> str:
    """Return key as 'C major' / 'A minor' / 'unknown'.

    Pipeline: HPSS to drop percussion → chroma-CQT on the harmonic component
    → Albrecht–Shanahan profile correlation.
    """
    try:
        # Strip the percussive component so drums/snares don't smear chroma.
        y_harm, _ = librosa.effects.hpss(y)
        chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr)
        if chroma.size == 0:
            return "unknown"
        chroma_mean = chroma.mean(axis=1)
        if not np.any(chroma_mean):
            return "unknown"

        best_corr = -np.inf
        best_label = "unknown"

        for tonic in range(12):
            major_rot = np.roll(_MAJOR_PROFILE, tonic)
            minor_rot = np.roll(_MINOR_PROFILE, tonic)
            # np.corrcoef returns 2x2 matrix; [0, 1] is the Pearson r.
            r_major = float(np.corrcoef(chroma_mean, major_rot)[0, 1])
            r_minor = float(np.corrcoef(chroma_mean, minor_rot)[0, 1])

            if r_major > best_corr:
                best_corr = r_major
                best_label = f"{_MAJOR_NAMES[tonic]} major"
            if r_minor > best_corr:
                best_corr = r_minor
                best_label = f"{_MINOR_NAMES[tonic]} minor"

        # If even the best correlation is weak, the audio probably lacks
        # tonal content (drum stem, noise) — refuse to guess.
        if not np.isfinite(best_corr) or best_corr < 0.3:
            return "unknown"
        return best_label
    except Exception:
        return "unknown"


def analyze(audio_path: Path | str) -> dict:
    """Analyze an audio file, returning BPM, key, and Camelot notation.

    Returns
    -------
    dict with keys:
        bpm     : float — global tempo, 0.0 if undetectable.
        key     : str   — e.g. 'C major', 'A minor', or 'unknown'.
        camelot : str   — e.g. '8B', '8A', or '?'.

    Works on both full mixes and isolated Demucs stems, but accuracy on
    isolated drum stems (key) and isolated harmonic stems (BPM) is low —
    see module docstring.
    """
    path = Path(audio_path)
    if not path.exists():
        return dict(_UNKNOWN)

    try:
        # mono=True simplifies analysis. sr=None preserves native rate; that's
        # fine since librosa internally resamples for chroma/beat as needed.
        y, sr = librosa.load(str(path), mono=True, sr=22050)
    except Exception:
        return dict(_UNKNOWN)

    if y.size == 0 or y.shape[0] / sr < _MIN_DURATION_S:
        return dict(_UNKNOWN)

    rms = float(np.sqrt(np.mean(y**2)))
    if rms < _SILENCE_RMS:
        return dict(_UNKNOWN)

    bpm = _detect_bpm(y, sr)
    key = _detect_key(y, sr)
    camelot = _key_to_camelot(key) if key != "unknown" else "?"

    return {"bpm": bpm, "key": key, "camelot": camelot}


# ---------------------------------------------------------------------------
# Smoke test — `python audio_analyzer.py`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import tempfile
    import time

    import soundfile as sf

    # 1) Sanity: Camelot lookup is internally consistent.
    assert _key_to_camelot("C major") == "8B"
    assert _key_to_camelot("A minor") == "8A"
    assert _key_to_camelot("G major") == "9B"
    assert _key_to_camelot("F# minor") == "11A"
    assert _key_to_camelot("Bb minor") == "3A"
    assert _key_to_camelot("C# minor") == "12A"  # enharmonic of Db minor
    assert _key_to_camelot("garbage") == "?"
    print("Camelot lookup: OK")

    # 2) Generate a synthetic A-major-ish signal: A4 + C#5 + E5 chord at 120 BPM.
    sr = 22050
    duration = 8.0  # seconds
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)

    a_chord = (
        np.sin(2 * np.pi * 440.0 * t)        # A4
        + np.sin(2 * np.pi * 554.37 * t)     # C#5
        + np.sin(2 * np.pi * 659.25 * t)     # E5
    )
    # Amplitude envelope at ~120 BPM (2 Hz) to give beat tracker something.
    env = 0.5 * (1 + np.sign(np.sin(2 * np.pi * 2.0 * t)))
    y_synth = (a_chord * env * 0.2).astype(np.float32)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, y_synth, sr)
        synth_path = Path(f.name)

    t0 = time.time()
    result = analyze(synth_path)
    elapsed = time.time() - t0
    print(f"Synthetic A-major chord @ ~120 BPM -> {result} ({elapsed:.2f}s)")
    # We expect A major (or possibly F# minor — its relative minor — which is
    # a common key-detection failure mode and itself instructive).

    # 3) Silence: must not crash.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, np.zeros(sr * 4, dtype=np.float32), sr)
        silence_path = Path(f.name)
    print(f"Silence -> {analyze(silence_path)}")

    # 4) Real file (optional): pass a path on the CLI.
    if len(sys.argv) > 1:
        real_path = Path(sys.argv[1])
        t0 = time.time()
        result = analyze(real_path)
        elapsed = time.time() - t0
        print(f"{real_path.name} -> {result} ({elapsed:.2f}s)")

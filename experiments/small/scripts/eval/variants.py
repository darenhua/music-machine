"""Parameter-sweep variants of `analyze()` for accuracy iteration.

Each variant has the same shape as `audio_analyzer.analyze(path) -> dict` but
tweaks one or more knobs:

  * Key profile        — Krumhansl-Kessler (baseline) / Temperley / Bellman-Budge / Albrecht-Shanahan
  * Chroma type        — chroma_cqt (baseline) / chroma_stft / chroma_cens
  * Preprocessing      — none (baseline) / HPSS-harmonic (drop percussion before chroma)
  * BPM range / octave — librosa default / range-constrained / post-hoc octave correction

Variants live here (not in `audio_analyzer.py`) so we can iterate freely
without breaking the production module. Once a variant wins the sweep, port
its config back to `audio_analyzer.py`.

Numerical profiles
------------------
Sources:
  Krumhansl-Kessler (1982)    : the de-facto baseline (same as the prod module)
  Temperley (1999)            : Bayesian-tuned for Western tonal music
  Bellman-Budge (1982)        : alternative perceptual study
  Albrecht-Shanahan (2013)    : derived from a large symbolic-music corpus,
                                widely reported as the best for symbolic data
"""

from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np

# Reuse the camelot lookup + name tables from the production module so
# eval/variant outputs are directly comparable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "library"))

from audio_analyzer import (  # noqa: E402
    _CAMELOT_MAJOR,
    _CAMELOT_MINOR,
    _MAJOR_NAMES,
    _MINOR_NAMES,
    _key_to_camelot,
)

# ---------------------------------------------------------------------------
# Key profiles (12 weights each, indexed C,C#,D,D#,E,F,F#,G,G#,A,A#,B)
# ---------------------------------------------------------------------------

KK_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
KK_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)

# Temperley 1999 ("KP" profile from Bayesian key-finding paper, normalised).
TEMP_MAJOR = np.array(
    [5.0, 2.0, 3.5, 2.0, 4.5, 4.0, 2.0, 4.5, 2.0, 3.5, 1.5, 4.0]
)
TEMP_MINOR = np.array(
    [5.0, 2.0, 3.5, 4.5, 2.0, 4.0, 2.0, 4.5, 3.5, 2.0, 1.5, 4.0]
)

# Bellman-Budge 1982.
BB_MAJOR = np.array(
    [16.80, 0.86, 12.95, 1.41, 13.49, 11.93, 1.25, 20.28, 1.80, 8.04, 0.62, 10.57]
)
BB_MINOR = np.array(
    [18.16, 0.69, 12.99, 13.34, 1.07, 11.15, 1.38, 21.07, 7.49, 1.53, 0.92, 10.21]
)

# Albrecht-Shanahan 2013 (derived from large corpus of classical scores).
AS_MAJOR = np.array(
    [0.238, 0.006, 0.111, 0.006, 0.137, 0.094, 0.016, 0.214, 0.009, 0.080, 0.008, 0.081]
)
AS_MINOR = np.array(
    [0.220, 0.006, 0.104, 0.123, 0.019, 0.103, 0.012, 0.214, 0.062, 0.022, 0.061, 0.052]
)

PROFILES: dict[str, tuple[np.ndarray, np.ndarray]] = {
    "krumhansl": (KK_MAJOR, KK_MINOR),
    "temperley": (TEMP_MAJOR, TEMP_MINOR),
    "bellman": (BB_MAJOR, BB_MINOR),
    "albrecht": (AS_MAJOR, AS_MINOR),
}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

_UNKNOWN = {"bpm": 0.0, "key": "unknown", "camelot": "?"}
_SILENCE_RMS = 1e-4
_MIN_DURATION_S = 2.0


def _load(audio_path: Path) -> tuple[np.ndarray | None, int]:
    p = Path(audio_path)
    if not p.exists():
        return None, 0
    try:
        y, sr = librosa.load(str(p), mono=True, sr=22050)
    except Exception:
        return None, 0
    if y.size == 0 or y.shape[0] / sr < _MIN_DURATION_S:
        return None, sr
    rms = float(np.sqrt(np.mean(y**2)))
    if rms < _SILENCE_RMS:
        return None, sr
    return y, sr


# ---------------------------------------------------------------------------
# BPM detection (variants)
# ---------------------------------------------------------------------------

def detect_bpm_default(y: np.ndarray, sr: int) -> float:
    """Baseline — `librosa.beat.beat_track`, no constraints."""
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        v = float(np.atleast_1d(tempo)[0])
        return round(v, 2) if np.isfinite(v) and v > 0 else 0.0
    except Exception:
        return 0.0


def detect_bpm_octave_corrected(
    y: np.ndarray, sr: int, low: float = 60.0, high: float = 180.0
) -> float:
    """beat_track + post-hoc octave correction.

    If the raw tempo is outside [low, high], try halving and doubling and
    pick the candidate that lands inside the window. Conservative — only
    corrects clearly-out-of-range values.
    """
    raw = detect_bpm_default(y, sr)
    if raw == 0.0:
        return 0.0
    if low <= raw <= high:
        return raw
    candidates = [raw, raw / 2, raw * 2, raw / 3, raw * 3]
    in_window = [c for c in candidates if low <= c <= high]
    if in_window:
        return round(min(in_window, key=lambda c: abs(c - 100)), 2)
    return raw


def detect_bpm_prefer_100(y: np.ndarray, sr: int) -> float:
    """Pick the candidate from {raw, raw/2, raw*2} closest to 100 BPM.

    Aggressive — fixes Hotel California (143.6 -> 71.8) and Lose Yourself
    (172.3 -> 86.15) at the cost of getting Blinding Lights wrong (true
    171 BPM, we'd report ~86). For DJ workflows the half/double choice is
    largely a matter of feel — both are mixable.
    """
    raw = detect_bpm_default(y, sr)
    if raw == 0.0:
        return 0.0
    candidates = [raw, raw / 2, raw * 2]
    return round(min(candidates, key=lambda c: abs(c - 100)), 2)


def detect_bpm_with_prior(
    y: np.ndarray, sr: int, start_bpm: float = 100.0
) -> float:
    """Re-run beat_track with an explicit BPM prior (default librosa uses 120)."""
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr, start_bpm=start_bpm)
        v = float(np.atleast_1d(tempo)[0])
        return round(v, 2) if np.isfinite(v) and v > 0 else 0.0
    except Exception:
        return 0.0


def detect_bpm_tempogram(y: np.ndarray, sr: int) -> float:
    """Use `librosa.feature.tempo` (a tempogram-based estimator).

    Different algorithm than beat_track — uses autocorrelation of the onset
    envelope. Sometimes more robust on tracks with sparse percussion.
    """
    try:
        tempo = librosa.feature.tempo(y=y, sr=sr, aggregate=np.median)
        v = float(np.atleast_1d(tempo)[0])
        return round(v, 2) if np.isfinite(v) and v > 0 else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Key detection (variants)
# ---------------------------------------------------------------------------

def _chroma(
    y: np.ndarray,
    sr: int,
    chroma_type: str = "cqt",
    use_hpss: bool = False,
) -> np.ndarray:
    """Return mean chroma vector (12,)."""
    sig = y
    if use_hpss:
        # Harmonic-percussive source separation; drop the percussive part so
        # drums/snares don't smear chroma. Cheap and well-tested in librosa.
        sig, _ = librosa.effects.hpss(y)

    if chroma_type == "cqt":
        c = librosa.feature.chroma_cqt(y=sig, sr=sr)
    elif chroma_type == "stft":
        c = librosa.feature.chroma_stft(y=sig, sr=sr)
    elif chroma_type == "cens":
        c = librosa.feature.chroma_cens(y=sig, sr=sr)
    else:
        raise ValueError(f"unknown chroma_type: {chroma_type}")
    return c.mean(axis=1)


def _best_key_from_chroma(
    chroma_mean: np.ndarray,
    profile_name: str = "krumhansl",
    min_corr: float = 0.3,
) -> str:
    if not np.any(chroma_mean):
        return "unknown"
    major_p, minor_p = PROFILES[profile_name]
    best_corr = -np.inf
    best_label = "unknown"
    for tonic in range(12):
        major_rot = np.roll(major_p, tonic)
        minor_rot = np.roll(minor_p, tonic)
        r_maj = float(np.corrcoef(chroma_mean, major_rot)[0, 1])
        r_min = float(np.corrcoef(chroma_mean, minor_rot)[0, 1])
        if r_maj > best_corr:
            best_corr = r_maj
            best_label = f"{_MAJOR_NAMES[tonic]} major"
        if r_min > best_corr:
            best_corr = r_min
            best_label = f"{_MINOR_NAMES[tonic]} minor"
    if not np.isfinite(best_corr) or best_corr < min_corr:
        return "unknown"
    return best_label


def detect_key(
    y: np.ndarray,
    sr: int,
    profile: str = "krumhansl",
    chroma_type: str = "cqt",
    use_hpss: bool = False,
    min_corr: float = 0.3,
) -> str:
    try:
        chroma_mean = _chroma(y, sr, chroma_type=chroma_type, use_hpss=use_hpss)
        return _best_key_from_chroma(chroma_mean, profile, min_corr=min_corr)
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Variant entrypoints — same shape as audio_analyzer.analyze()
# ---------------------------------------------------------------------------

def analyze_with_config(
    audio_path: Path,
    *,
    profile: str = "krumhansl",
    chroma_type: str = "cqt",
    use_hpss: bool = False,
    bpm_octave_correct: bool = False,
) -> dict:
    y, sr = _load(audio_path)
    if y is None:
        return dict(_UNKNOWN)
    if bpm_octave_correct:
        bpm = detect_bpm_octave_corrected(y, sr)
    else:
        bpm = detect_bpm_default(y, sr)
    key = detect_key(y, sr, profile=profile, chroma_type=chroma_type, use_hpss=use_hpss)
    camelot = _key_to_camelot(key) if key != "unknown" else "?"
    return {"bpm": bpm, "key": key, "camelot": camelot}


# ---------------------------------------------------------------------------
# Batch path — load audio once, run many variants over the cached arrays
# ---------------------------------------------------------------------------

def load_audio_bundle(audio_path: Path) -> dict | None:
    """Load + precompute everything that's variant-independent.

    Returns a dict with keys:
        y          : np.ndarray (mono, sr=22050)
        y_harm     : np.ndarray (HPSS-harmonic of y)  -- expensive, do once
        sr         : int
    or None if the file can't be loaded / is too short / is silent.
    """
    y, sr = _load(audio_path)
    if y is None:
        return None
    y_harm, _ = librosa.effects.hpss(y)
    return {"y": y, "y_harm": y_harm, "sr": sr}


def _chroma_from_signal(
    sig: np.ndarray, sr: int, chroma_type: str
) -> np.ndarray:
    if chroma_type == "cqt":
        c = librosa.feature.chroma_cqt(y=sig, sr=sr)
    elif chroma_type == "stft":
        c = librosa.feature.chroma_stft(y=sig, sr=sr)
    elif chroma_type == "cens":
        c = librosa.feature.chroma_cens(y=sig, sr=sr)
    else:
        raise ValueError(f"unknown chroma_type: {chroma_type}")
    return c.mean(axis=1)


_BPM_STRATEGIES = {
    "default":     detect_bpm_default,
    "octave":      detect_bpm_octave_corrected,
    "prefer-100":  detect_bpm_prefer_100,
    "prior-100":   lambda y, sr: detect_bpm_with_prior(y, sr, start_bpm=100.0),
    "prior-90":    lambda y, sr: detect_bpm_with_prior(y, sr, start_bpm=90.0),
    "tempogram":   detect_bpm_tempogram,
}


def analyze_bundle(
    bundle: dict | None,
    *,
    profile: str = "krumhansl",
    chroma_type: str = "cqt",
    use_hpss: bool = False,
    bpm_strategy: str = "default",
) -> dict:
    """Same return shape as analyze_with_config, but reuses preloaded arrays."""
    if bundle is None:
        return dict(_UNKNOWN)
    y, sr, y_harm = bundle["y"], bundle["sr"], bundle["y_harm"]
    sig = y_harm if use_hpss else y

    # BPM (run on raw y — HPSS-harmonic strips percussion which is what beat
    # tracking depends on).
    bpm = _BPM_STRATEGIES[bpm_strategy](y, sr)

    # Key
    try:
        chroma_mean = _chroma_from_signal(sig, sr, chroma_type)
        key = _best_key_from_chroma(chroma_mean, profile)
    except Exception:
        key = "unknown"

    camelot = _key_to_camelot(key) if key != "unknown" else "?"
    return {"bpm": bpm, "key": key, "camelot": camelot}


# Named variants we sweep over. Each is a `(label, kwargs_dict)` pair.
# Round 2: best-key (albrecht+hpss) held fixed, BPM strategies swept.
VARIANTS: list[tuple[str, dict]] = [
    # --- key sweep, default BPM ---
    ("baseline",            dict(profile="krumhansl", chroma_type="cqt",  use_hpss=False, bpm_strategy="default")),
    ("hpss",                dict(profile="krumhansl", chroma_type="cqt",  use_hpss=True,  bpm_strategy="default")),
    ("albrecht",            dict(profile="albrecht",  chroma_type="cqt",  use_hpss=False, bpm_strategy="default")),
    ("albrecht+hpss",       dict(profile="albrecht",  chroma_type="cqt",  use_hpss=True,  bpm_strategy="default")),
    ("bellman+hpss",        dict(profile="bellman",   chroma_type="cqt",  use_hpss=True,  bpm_strategy="default")),
    ("cens+albrecht+hpss",  dict(profile="albrecht",  chroma_type="cens", use_hpss=True,  bpm_strategy="default")),
    # --- BPM sweep, best key (albrecht+hpss) held fixed ---
    ("bpm-prefer-100",      dict(profile="albrecht",  chroma_type="cqt",  use_hpss=True,  bpm_strategy="prefer-100")),
    ("bpm-octave",          dict(profile="albrecht",  chroma_type="cqt",  use_hpss=True,  bpm_strategy="octave")),
    ("bpm-prior-100",       dict(profile="albrecht",  chroma_type="cqt",  use_hpss=True,  bpm_strategy="prior-100")),
    ("bpm-prior-90",        dict(profile="albrecht",  chroma_type="cqt",  use_hpss=True,  bpm_strategy="prior-90")),
    ("bpm-tempogram",       dict(profile="albrecht",  chroma_type="cqt",  use_hpss=True,  bpm_strategy="tempogram")),
]

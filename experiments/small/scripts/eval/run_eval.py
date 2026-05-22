"""Evaluation harness for `audio_analyzer.analyze()`.

Tests ONLY the BPM / key / Camelot solution. No YouTube, no yt-dlp, no network.
The MP3s must already be sitting in `downloads/{video_id}.mp3` — run
`scripts/eval/fetch_songs.py` first to populate them.

Grading
-------
Key — MIREX-style weighted score (the academic standard for key-detection
evaluation; see MIREX "Audio Key Detection" task):

    exact match              : 1.0
    perfect fifth (±7 st)    : 0.5
    relative major/minor     : 0.3
    parallel major/minor     : 0.2
    else                     : 0.0

BPM — two metrics:

    "tight"   : within ±3 BPM of reference
    "octave"  : tight, OR within ±3 BPM of half/double tempo (handles the
                common librosa half/double-time confusion)

Per-track results are printed to stdout and written to `results.json` so we
can diff after future algorithm changes.

Run
---
    uv run python scripts/eval/run_eval.py
    uv run python scripts/eval/run_eval.py --limit 3            # quick smoke
    uv run python scripts/eval/run_eval.py --songs path.json    # alt fixture
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Allow `python scripts/eval/run_eval.py` from repo root by adding the
# library dir to sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "library"))

from audio_analyzer import (  # noqa: E402
    _CAMELOT_MAJOR,
    _CAMELOT_MINOR,
    analyze,
)


# ---------------------------------------------------------------------------
# Key parsing & MIREX grading
# ---------------------------------------------------------------------------

# Canonical pitch class for every name we might encounter in the ground truth
# or in `analyze()` output.
_PC_BY_NAME: dict[str, int] = {
    "C": 0, "B#": 0,
    "C#": 1, "Db": 1,
    "D": 2,
    "D#": 3, "Eb": 3,
    "E": 4, "Fb": 4,
    "F": 5, "E#": 5,
    "F#": 6, "Gb": 6,
    "G": 7,
    "G#": 8, "Ab": 8,
    "A": 9,
    "A#": 10, "Bb": 10,
    "B": 11, "Cb": 11,
}


def parse_key(key_str: str) -> tuple[int, str] | None:
    """'F# minor' -> (6, 'minor'). Returns None if unparseable."""
    if not key_str or not isinstance(key_str, str):
        return None
    parts = key_str.strip().split()
    if len(parts) != 2:
        return None
    root, mode = parts[0], parts[1].lower()
    if mode not in ("major", "minor"):
        return None
    pc = _PC_BY_NAME.get(root)
    if pc is None:
        return None
    return pc, mode


def mirex_score(predicted: str, reference: str) -> float:
    """Score a key prediction against the reference (0.0..1.0)."""
    p = parse_key(predicted)
    r = parse_key(reference)
    if p is None or r is None:
        return 0.0
    p_pc, p_mode = p
    r_pc, r_mode = r

    if p_pc == r_pc and p_mode == r_mode:
        return 1.0

    # Perfect fifth: same mode, root differs by 7 semitones (either direction).
    if p_mode == r_mode and ((p_pc - r_pc) % 12 == 7 or (r_pc - p_pc) % 12 == 7):
        return 0.5

    # Relative major/minor: minor is 3 semitones below its relative major.
    # Cmaj <-> Am  (C=0, A=9; 0 - 9 = -9 ≡ 3 mod 12)
    if p_mode != r_mode:
        if p_mode == "minor" and r_mode == "major" and (r_pc - p_pc) % 12 == 3:
            return 0.3
        if p_mode == "major" and r_mode == "minor" and (p_pc - r_pc) % 12 == 3:
            return 0.3
        # Parallel: same root, different mode (C major <-> C minor).
        if p_pc == r_pc:
            return 0.2

    return 0.0


def key_to_camelot(key_str: str) -> str:
    """Reuse the same logic as audio_analyzer for displaying expected camelot."""
    p = parse_key(key_str)
    if p is None:
        return "?"
    pc, mode = p
    return _CAMELOT_MAJOR[pc] if mode == "major" else _CAMELOT_MINOR[pc]


# ---------------------------------------------------------------------------
# BPM grading
# ---------------------------------------------------------------------------

_BPM_TIGHT_TOL = 3.0  # ± BPM for "tight" match


def bpm_match(predicted: float, reference: float) -> dict:
    """Return tight/octave match flags and the absolute error."""
    err = abs(predicted - reference)
    tight = err <= _BPM_TIGHT_TOL
    octave = (
        tight
        or abs(predicted - reference * 2) <= _BPM_TIGHT_TOL
        or abs(predicted - reference / 2) <= _BPM_TIGHT_TOL
    )
    return {"tight": tight, "octave": octave, "abs_error_bpm": round(err, 2)}


# ---------------------------------------------------------------------------
# Pipeline: cached mp3 -> analyze
# ---------------------------------------------------------------------------

_DOWNLOAD_DIR = _REPO_ROOT / "downloads"


@dataclass
class TrackResult:
    title: str
    artist: str
    expected_key: str
    expected_bpm: float
    expected_camelot: str
    video_id: str = ""
    audio_path: str = ""
    predicted_key: str = "unknown"
    predicted_bpm: float = 0.0
    predicted_camelot: str = "?"
    mirex: float = 0.0
    bpm_tight: bool = False
    bpm_octave: bool = False
    bpm_abs_error: float = 0.0
    analyze_latency_s: float = 0.0
    error: str = ""
    notes: list[str] = field(default_factory=list)


def _resolve_audio_path(song: dict, download_dir: Path) -> Path:
    """Locate the cached MP3 for a song. Raises FileNotFoundError if missing."""
    if explicit := song.get("audio_path"):
        p = Path(explicit)
        if p.exists():
            return p
        raise FileNotFoundError(
            f"audio_path {p} declared in songs.json but missing on disk"
        )

    vid = song.get("video_id")
    if not vid:
        raise ValueError(
            f"song {song.get('title')!r} has no video_id and no audio_path. "
            "Run `scripts/eval/fetch_songs.py --resolve` to populate it."
        )
    p = download_dir / f"{vid}.mp3"
    if not p.exists():
        raise FileNotFoundError(
            f"missing {p} — run `uv run python scripts/eval/fetch_songs.py` "
            "to download the eval fixtures."
        )
    return p


def evaluate_song(song: dict, download_dir: Path) -> TrackResult:
    r = TrackResult(
        title=song["title"],
        artist=song["artist"],
        expected_key=song["expected_key"],
        expected_bpm=float(song["expected_bpm"]),
        expected_camelot=song.get("expected_camelot") or key_to_camelot(song["expected_key"]),
        video_id=song.get("video_id", ""),
    )
    try:
        mp3_path = _resolve_audio_path(song, download_dir)
        r.audio_path = str(mp3_path)

        t0 = time.time()
        result = analyze(mp3_path)
        r.analyze_latency_s = round(time.time() - t0, 2)

        r.predicted_key = result["key"]
        r.predicted_bpm = result["bpm"]
        r.predicted_camelot = result["camelot"]

        r.mirex = mirex_score(r.predicted_key, r.expected_key)
        bpm = bpm_match(r.predicted_bpm, r.expected_bpm)
        r.bpm_tight = bpm["tight"]
        r.bpm_octave = bpm["octave"]
        r.bpm_abs_error = bpm["abs_error_bpm"]
    except Exception as exc:  # noqa: BLE001 — surface any failure per-track
        r.error = f"{type(exc).__name__}: {exc}"
    return r


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _row(r: TrackResult) -> str:
    if r.error:
        return f"  [ERROR] {r.title} — {r.error}"
    mirex_tag = {1.0: "EXACT", 0.5: "5th", 0.3: "rel", 0.2: "par"}.get(r.mirex, "miss")
    bpm_tag = "tight" if r.bpm_tight else ("octave" if r.bpm_octave else "miss")
    return (
        f"  {r.title:<28} {r.artist:<22}"
        f"  key: {r.predicted_key:<10} vs {r.expected_key:<10} ({mirex_tag:>5} {r.mirex:.1f})"
        f"  bpm: {r.predicted_bpm:>6.1f} vs {r.expected_bpm:>5.1f} ({bpm_tag:>6}, Δ{r.bpm_abs_error:>5.1f})"
        f"  camelot: {r.predicted_camelot:>3} vs {r.expected_camelot:<3}"
    )


def _aggregate(results: list[TrackResult]) -> dict:
    ok = [r for r in results if not r.error]
    n = len(ok)
    if n == 0:
        return {"n": 0, "errors": len(results)}
    exact = sum(1 for r in ok if r.mirex == 1.0) / n
    mirex_total = sum(r.mirex for r in ok) / n
    bpm_tight = sum(1 for r in ok if r.bpm_tight) / n
    bpm_octave = sum(1 for r in ok if r.bpm_octave) / n
    camelot_exact = sum(
        1 for r in ok if r.predicted_camelot == r.expected_camelot
    ) / n
    return {
        "n": n,
        "errors": len(results) - n,
        "key_exact_pct": round(100 * exact, 1),
        "key_mirex_avg": round(mirex_total, 3),
        "camelot_exact_pct": round(100 * camelot_exact, 1),
        "bpm_tight_pct": round(100 * bpm_tight, 1),
        "bpm_octave_pct": round(100 * bpm_octave, 1),
    }


def run(songs_path: Path, download_dir: Path, limit: int | None = None) -> dict:
    songs = json.loads(songs_path.read_text())
    if limit:
        songs = songs[:limit]

    print(f"\nEvaluating {len(songs)} song(s) — fixtures from {download_dir}\n")
    results: list[TrackResult] = []
    for i, song in enumerate(songs, 1):
        print(f"[{i}/{len(songs)}] {song['title']} — {song['artist']}")
        r = evaluate_song(song, download_dir)
        results.append(r)
        print(_row(r))

    summary = _aggregate(results)
    print("\n" + "=" * 80)
    print("AGGREGATE")
    print("=" * 80)
    for k, v in summary.items():
        print(f"  {k:<22} {v}")

    return {
        "summary": summary,
        "results": [r.__dict__ for r in results],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--songs",
        default=str(Path(__file__).parent / "songs.json"),
        help="Path to the songs.json fixture",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "results.json"),
        help="Where to write the JSON results",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only evaluate the first N songs (for fast iteration)",
    )
    parser.add_argument(
        "--download-dir",
        default=str(_DOWNLOAD_DIR),
        help="MP3 fixture directory",
    )
    args = parser.parse_args()

    out = run(
        Path(args.songs),
        Path(args.download_dir),
        limit=args.limit,
    )
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\nWrote results -> {args.out}")


if __name__ == "__main__":
    main()

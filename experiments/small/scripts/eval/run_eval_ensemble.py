"""Eval harness for the ensemble analyzer.

Same fixture + same grading as ``run_eval.py``, but calls
``audio_analyzer_ensemble.analyze_ensemble`` instead of the local-only
``audio_analyzer.analyze``. Cached Replicate responses make repeat runs
free and fast.

Run
---
    uv run python scripts/eval/run_eval_ensemble.py
    uv run python scripts/eval/run_eval_ensemble.py --limit 3   # first 3 only
    uv run python scripts/eval/run_eval_ensemble.py --algos multifeature deepsquare-k16 percival
    uv run python scripts/eval/run_eval_ensemble.py --no-replicate  # local-only for diffing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "library"))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "eval"))

# Load .env so REPLICATE_API_TOKEN is available even outside marimo.
_env = _REPO_ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from audio_analyzer_ensemble import analyze_ensemble  # noqa: E402
from run_eval import (  # noqa: E402
    _resolve_audio_path,
    bpm_match,
    key_to_camelot,
    mirex_score,
)

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
    bpm_votes: dict = field(default_factory=dict)
    ensemble_latencies_s: dict = field(default_factory=dict)
    failures: dict = field(default_factory=dict)
    error: str = ""


def evaluate_song(song: dict, download_dir: Path, algos: tuple, use_replicate: bool) -> TrackResult:
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
        result = analyze_ensemble(mp3_path, algos=algos, use_replicate=use_replicate)
        r.analyze_latency_s = round(time.time() - t0, 2)

        r.predicted_key = result["key"]
        r.predicted_bpm = result["bpm"]
        r.predicted_camelot = result["camelot"]
        r.bpm_votes = result.get("votes", {})
        r.ensemble_latencies_s = result.get("latencies_s", {})
        r.failures = result.get("failures", {})

        r.mirex = mirex_score(r.predicted_key, r.expected_key)
        bpm = bpm_match(r.predicted_bpm, r.expected_bpm)
        r.bpm_tight = bpm["tight"]
        r.bpm_octave = bpm["octave"]
        r.bpm_abs_error = bpm["abs_error_bpm"]
    except Exception as exc:  # noqa: BLE001
        r.error = f"{type(exc).__name__}: {exc}"
    return r


def _row(r: TrackResult) -> str:
    if r.error:
        return f"  [ERROR] {r.title} — {r.error}"
    mirex_tag = {1.0: "EXACT", 0.5: "5th", 0.3: "rel", 0.2: "par"}.get(r.mirex, "miss")
    bpm_tag = "tight" if r.bpm_tight else ("octave" if r.bpm_octave else "miss")
    votes_str = " ".join(f"{k.split('-', 1)[-1][:9]}={v:.1f}" for k, v in r.bpm_votes.items())
    return (
        f"  {r.title:<28} {r.artist:<20}"
        f"  key {r.predicted_key:<10}vs {r.expected_key:<10}({mirex_tag:>5} {r.mirex:.1f})"
        f"  bpm {r.predicted_bpm:>6.1f}vs {r.expected_bpm:>5.1f}({bpm_tag:>6}, Δ{r.bpm_abs_error:>5.1f})"
        f"\n      votes: {votes_str}"
    )


def _aggregate(results: list[TrackResult]) -> dict:
    ok = [r for r in results if not r.error]
    n = len(ok)
    if n == 0:
        return {"n": 0, "errors": len(results)}
    return {
        "n": n,
        "errors": len(results) - n,
        "key_exact_pct": round(100 * sum(1 for r in ok if r.mirex == 1.0) / n, 1),
        "key_mirex_avg": round(sum(r.mirex for r in ok) / n, 3),
        "camelot_exact_pct": round(100 * sum(1 for r in ok if r.predicted_camelot == r.expected_camelot) / n, 1),
        "bpm_tight_pct": round(100 * sum(1 for r in ok if r.bpm_tight) / n, 1),
        "bpm_octave_pct": round(100 * sum(1 for r in ok if r.bpm_octave) / n, 1),
        "avg_total_latency_s": round(sum(r.analyze_latency_s for r in ok) / n, 2),
    }


def run(songs_path: Path, download_dir: Path, algos: tuple, use_replicate: bool, limit: int | None) -> dict:
    songs = json.loads(songs_path.read_text())
    if limit:
        songs = songs[:limit]

    print(
        f"\nEvaluating {len(songs)} song(s) with ensemble "
        f"(replicate={use_replicate}, algos={algos})\n"
    )
    results: list[TrackResult] = []
    for i, song in enumerate(songs, 1):
        print(f"[{i}/{len(songs)}] {song['title']} — {song['artist']}", flush=True)
        r = evaluate_song(song, download_dir, algos, use_replicate)
        results.append(r)
        print(_row(r), flush=True)

    summary = _aggregate(results)
    print("\n" + "=" * 90)
    print("AGGREGATE (ENSEMBLE)")
    print("=" * 90)
    for k, v in summary.items():
        print(f"  {k:<22} {v}")

    return {
        "summary": summary,
        "results": [asdict(r) for r in results],
        "config": {"algos": list(algos), "use_replicate": use_replicate},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--songs", default=str(Path(__file__).parent / "songs.json"))
    parser.add_argument("--out", default=str(Path(__file__).parent / "results_ensemble.json"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--download-dir", default=str(_DOWNLOAD_DIR))
    parser.add_argument(
        "--algos",
        nargs="+",
        default=["multifeature", "deepsquare-k16"],
        help="Which mtg/essentia-bpm algo_types to call",
    )
    parser.add_argument(
        "--no-replicate",
        action="store_true",
        help="Skip Replicate calls (librosa-only — useful for diffing)",
    )
    args = parser.parse_args()

    out = run(
        Path(args.songs),
        Path(args.download_dir),
        tuple(args.algos),
        use_replicate=not args.no_replicate,
        limit=args.limit,
    )
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\nWrote results -> {args.out}")


if __name__ == "__main__":
    main()

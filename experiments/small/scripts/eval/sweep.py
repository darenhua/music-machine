"""Parameter sweep — runs every variant in `variants.VARIANTS` against the
10-song fixture and prints a comparison table.

Loads each MP3 + HPSS-harmonic **once**, then runs every variant over the
cached arrays. ~10× faster than per-variant reload.

Run
---
    uv run python scripts/eval/sweep.py
    uv run python scripts/eval/sweep.py --songs path.json  --out sweep.json

Pure offline — no yt-dlp, no network. Requires `downloads/{video_id}.mp3`
already cached (run `scripts/eval/fetch_songs.py` first).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "library"))
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "eval"))

from run_eval import (  # noqa: E402
    _resolve_audio_path,
    bpm_match,
    key_to_camelot,
    mirex_score,
)
from variants import VARIANTS, analyze_bundle, load_audio_bundle  # noqa: E402

_DOWNLOAD_DIR = _REPO_ROOT / "downloads"


def _grade_row(song: dict, result: dict) -> dict:
    exp_key = song["expected_key"]
    exp_bpm = float(song["expected_bpm"])
    exp_cam = song.get("expected_camelot") or key_to_camelot(exp_key)
    mirex = mirex_score(result["key"], exp_key)
    bpm = bpm_match(result["bpm"], exp_bpm)
    return {
        "title": song["title"],
        "expected_key": exp_key,
        "expected_bpm": exp_bpm,
        "expected_camelot": exp_cam,
        "predicted_key": result["key"],
        "predicted_bpm": result["bpm"],
        "predicted_camelot": result["camelot"],
        "mirex": mirex,
        "bpm_tight": bpm["tight"],
        "bpm_octave": bpm["octave"],
        "bpm_abs_error": bpm["abs_error_bpm"],
        "camelot_exact": result["camelot"] == exp_cam,
    }


def _aggregate(rows: list[dict]) -> dict:
    ok = [r for r in rows if "error" not in r]
    n = len(ok)
    if n == 0:
        return {"n": 0, "errors": len(rows)}
    return {
        "n": n,
        "errors": len(rows) - n,
        "key_exact_pct": round(100 * sum(r["mirex"] == 1.0 for r in ok) / n, 1),
        "key_mirex_avg": round(sum(r["mirex"] for r in ok) / n, 3),
        "camelot_exact_pct": round(100 * sum(r["camelot_exact"] for r in ok) / n, 1),
        "bpm_tight_pct": round(100 * sum(r["bpm_tight"] for r in ok) / n, 1),
        "bpm_octave_pct": round(100 * sum(r["bpm_octave"] for r in ok) / n, 1),
    }


def run(songs_path: Path, download_dir: Path) -> dict:
    songs = json.loads(songs_path.read_text())

    # 1. Preload every audio bundle (y, y_harm, sr) ONCE.
    print(f"\nPreloading {len(songs)} audio files (incl. HPSS) ...", flush=True)
    bundles: list[dict | None] = []
    t0 = time.time()
    for song in songs:
        try:
            path = _resolve_audio_path(song, download_dir)
            tb = time.time()
            b = load_audio_bundle(path)
            print(
                f"  {song['title']:<25} loaded in {time.time() - tb:.2f}s",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  {song['title']:<25} ERROR: {exc}", flush=True)
            b = None
        bundles.append(b)
    print(f"Preload total: {time.time() - t0:.2f}s\n", flush=True)

    # 2. Run every variant over the cached bundles.
    print(f"Sweeping {len(VARIANTS)} variants ...\n", flush=True)
    out: dict = {"songs": [s["title"] for s in songs], "variants": {}}
    for label, kwargs in VARIANTS:
        rows = []
        tv = time.time()
        for song, b in zip(songs, bundles):
            result = analyze_bundle(b, **kwargs)
            rows.append(_grade_row(song, result))
        summary = _aggregate(rows)
        out["variants"][label] = {
            "summary": summary,
            "rows": rows,
            "config": kwargs,
            "wall_s": round(time.time() - tv, 2),
        }
        s = summary
        print(
            f"  {label:<22} "
            f"key {s.get('key_exact_pct', 0):>5}%  "
            f"mirex {s.get('key_mirex_avg', 0):>5.3f}  "
            f"cam {s.get('camelot_exact_pct', 0):>5}%  "
            f"bpmT {s.get('bpm_tight_pct', 0):>5}%  "
            f"bpmO {s.get('bpm_octave_pct', 0):>5}%  "
            f"({time.time() - tv:.1f}s)",
            flush=True,
        )
    return out


def print_per_song_key_matrix(out: dict) -> None:
    songs = out["songs"]
    variant_labels = list(out["variants"].keys())
    print("\n" + "=" * 110)
    print("PER-SONG KEY (✓ exact, • partial MIREX>0, x miss)")
    print("=" * 110)
    short = lambda s, n: (s[:n - 1] + "…") if len(s) > n else s
    header = f"{'song':<22} {'expected':<10}  " + " ".join(f"{short(v, 10):<11}" for v in variant_labels)
    print(header)
    for i, title in enumerate(songs):
        rows = [out["variants"][v]["rows"][i] for v in variant_labels]
        exp = rows[0].get("expected_key", "")
        cells = []
        for r in rows:
            tag = "✓" if r["mirex"] == 1.0 else ("•" if r["mirex"] > 0 else "x")
            cells.append(f"{tag} {short(r['predicted_key'], 9):<9}")
        print(f"{short(title, 22):<22} {exp:<10}  " + " ".join(cells))


def print_per_song_bpm_matrix(out: dict) -> None:
    songs = out["songs"]
    variant_labels = list(out["variants"].keys())
    print("\n" + "=" * 110)
    print("PER-SONG BPM (T tight ±3, O half/double, x miss)")
    print("=" * 110)
    short = lambda s, n: (s[:n - 1] + "…") if len(s) > n else s
    header = f"{'song':<22} {'expected':<10}  " + " ".join(f"{short(v, 10):<11}" for v in variant_labels)
    print(header)
    for i, title in enumerate(songs):
        rows = [out["variants"][v]["rows"][i] for v in variant_labels]
        exp = f"{rows[0].get('expected_bpm', 0):.0f}"
        cells = []
        for r in rows:
            tag = "T" if r["bpm_tight"] else ("O" if r["bpm_octave"] else "x")
            cells.append(f"{tag} {r['predicted_bpm']:>6.1f} ")
        print(f"{short(title, 22):<22} {exp:<10}  " + " ".join(cells))


def announce_winner(out: dict) -> None:
    ranked = sorted(
        out["variants"].items(),
        key=lambda kv: (
            kv[1]["summary"].get("key_mirex_avg", 0),
            kv[1]["summary"].get("bpm_octave_pct", 0),
            kv[1]["summary"].get("bpm_tight_pct", 0),
        ),
        reverse=True,
    )
    print("\n" + "=" * 110)
    print("RANKED BY (key_mirex_avg, bpm_octave_pct, bpm_tight_pct)")
    print("=" * 110)
    for label, result in ranked:
        s = result["summary"]
        print(
            f"  {label:<22} mirex={s.get('key_mirex_avg', 0):.3f}  "
            f"key_exact={s.get('key_exact_pct', 0)}%  "
            f"bpm_tight={s.get('bpm_tight_pct', 0)}%  "
            f"bpm_octave={s.get('bpm_octave_pct', 0)}%"
        )
    print(f"\nWinner: {ranked[0][0]}  config={ranked[0][1]['config']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--songs", default=str(Path(__file__).parent / "songs.json")
    )
    parser.add_argument(
        "--out", default=str(Path(__file__).parent / "sweep.json")
    )
    parser.add_argument(
        "--download-dir", default=str(_DOWNLOAD_DIR)
    )
    args = parser.parse_args()

    out = run(Path(args.songs), Path(args.download_dir))
    print_per_song_key_matrix(out)
    print_per_song_bpm_matrix(out)
    announce_winner(out)
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(f"\nWrote sweep -> {args.out}")


if __name__ == "__main__":
    main()

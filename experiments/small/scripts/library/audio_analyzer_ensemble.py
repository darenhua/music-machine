"""Ensemble BPM/key/Camelot detection — multiple models, one verdict.

How it works
------------
* Local librosa analyzer (Albrecht + HPSS + start_bpm=100) provides ONE BPM
  vote and the SOLE key vote. Replicate has no hosted key-detection model
  as of mid-2026.
* Replicate's ``mtg/essentia-bpm`` model provides up to FOUR additional BPM
  votes — one per ``algo_type``: ``multifeature``, ``deepsquare-k16``,
  ``percival``, ``degara``. We default to the first two (the two best per
  the Essentia paper and the deep-CNN-on-EDM benchmark).
* BPM votes are aggregated by **median** (robust to one outlier; cheap to
  reason about). Outputs in the obvious half/double range are octave-folded
  toward the median before aggregation so a single half-time vote doesn't
  pull the result way off.
* Key is taken straight from the local analyzer.

Caching
-------
Replicate calls cost real money (~$0.0004 each) and take ~30 s per call.
Results are cached per-(audio-sha256, algo) under
``downloads/.cache/essentia-bpm/<sha>/<algo>.json``, keyed by the actual
file contents so cache hits are content-addressed and survive renames.

Concurrency
-----------
The two Replicate calls fire **in parallel** via ``concurrent.futures`` —
wall time for the BPM ensemble drops from 2×~30 s to ~30 s.

Environment
-----------
``REPLICATE_API_TOKEN`` must be set. ``marimo`` auto-loads ``.env`` via
``[tool.marimo.runtime] dotenv`` in pyproject.toml.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import re
import statistics
import time
import urllib.request
from pathlib import Path
from typing import Iterable

import replicate
from replicate.exceptions import ReplicateError

# Local single-vote analyzer
from audio_analyzer import analyze as _local_analyze

# Pinned to the version we probed against — keep the ensemble reproducible.
_ESSENTIA_BPM_VERSION = (
    "mtg/essentia-bpm:"
    "b3045c359817fea53678791886d50aa3e3a995dc4796fe74db0de156d5074a43"
)

_VALID_ALGOS = ("multifeature", "deepsquare-k16", "percival", "degara")
_DEFAULT_ALGOS: tuple[str, ...] = ("multifeature", "deepsquare-k16")

_BPM_RE = re.compile(r"Estimated BPM:\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)

# Default cache directory — sibling of `downloads/`.
_DEFAULT_CACHE_DIR = (
    Path(__file__).resolve().parents[2] / "downloads" / ".cache" / "essentia-bpm"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_bpm(payload: str) -> float:
    m = _BPM_RE.search(payload)
    if not m:
        raise ValueError(f"could not find BPM in payload: {payload!r}")
    return float(m.group(1))


def _read_output_body(output: object) -> str:
    """Replicate `output` may be FileOutput, str URL, or bytes-iterable."""
    if isinstance(output, str):
        with urllib.request.urlopen(output) as r:  # noqa: S310
            return r.read().decode("utf-8", "replace")
    read = getattr(output, "read", None)
    if callable(read):
        data = read()
        return data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
    # iter-of-bytes fallback
    buf = bytearray()
    for chunk in output:  # type: ignore[union-attr]
        if isinstance(chunk, (bytes, bytearray)):
            buf.extend(chunk)
    return buf.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Replicate call (cached)
# ---------------------------------------------------------------------------

def call_essentia_bpm(
    audio_path: Path,
    algo: str,
    *,
    cache_dir: Path | None = None,
    force: bool = False,
) -> dict:
    """Call ``mtg/essentia-bpm`` on ``audio_path`` with ``algo``.

    Returns a dict ``{bpm: float, source: 'essentia-<algo>', cached: bool,
    latency_s: float}``. Raises ``RuntimeError`` on API/parse failure.
    """
    if algo not in _VALID_ALGOS:
        raise ValueError(f"algo must be one of {_VALID_ALGOS}; got {algo!r}")

    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(audio_path)

    cache_dir = Path(cache_dir) if cache_dir else _DEFAULT_CACHE_DIR
    sha = _sha256_of_file(audio_path)
    cache_path = cache_dir / sha / f"{algo}.json"

    if not force and cache_path.exists():
        try:
            data = json.loads(cache_path.read_text())
            return {**data, "cached": True}
        except Exception:
            pass  # cache corrupt — overwrite below

    if not os.environ.get("REPLICATE_API_TOKEN"):
        raise RuntimeError(
            "REPLICATE_API_TOKEN not set. Add it to .env (marimo auto-loads it)."
        )

    # Replicate rate-limits low-credit accounts to 6 req/min, burst 1.
    # Retry with backoff on 429; surface other errors immediately.
    _RATE_LIMIT_RE = re.compile(r"resets in ~?(\d+)\s*s", re.IGNORECASE)
    t0 = time.time()
    last_exc: Exception | None = None
    for attempt in range(6):
        try:
            with audio_path.open("rb") as fh:
                output = replicate.run(
                    _ESSENTIA_BPM_VERSION,
                    input={"audio": fh, "algo_type": algo},
                )
            break
        except ReplicateError as exc:
            last_exc = exc
            msg = str(exc)
            if "429" not in msg and "throttle" not in msg.lower():
                raise
            m = _RATE_LIMIT_RE.search(msg)
            wait = (int(m.group(1)) + 2) if m else (5 * (attempt + 1))
            time.sleep(min(wait, 60))
    else:
        raise RuntimeError(f"essentia-bpm failed after retries: {last_exc}") from last_exc

    body = _read_output_body(output)
    bpm = _parse_bpm(body)
    elapsed = round(time.time() - t0, 2)

    result = {
        "bpm": round(bpm, 2),
        "source": f"essentia-{algo}",
        "latency_s": elapsed,
        "raw": body.strip(),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(result, indent=2))
    return {**result, "cached": False}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _octave_fold(values: list[float], ref: float) -> list[float]:
    """Fold each value toward ``ref`` by repeatedly halving/doubling.

    Picks the {v, v*2, v/2} variant closest to ``ref``. This makes the median
    robust to one model reporting half/double-time.
    """
    out: list[float] = []
    for v in values:
        if v <= 0:
            continue
        candidates = [v, v * 2, v / 2]
        out.append(min(candidates, key=lambda c: abs(c - ref)))
    return out


# Most pop/rock/hip-hop sits in [70, 140] BPM. We fold votes toward this
# anchor before taking the median so a single half/double-time vote can't
# tug the median across the octave boundary.
_BPM_FOLD_ANCHOR = 100.0


def aggregate_bpms(votes: dict[str, float], anchor: float = _BPM_FOLD_ANCHOR) -> float:
    """Octave-fold each vote toward ``anchor`` (default 100 BPM), then median.

    Folding-toward-fixed-anchor beats folding-toward-vote-median when only
    two votes are present and they are exactly half/double of each other
    (the median would land halfway between the two octaves — useless).
    Anchored to 100 BPM the result is always near the pop-music centroid.
    """
    valid = [v for v in votes.values() if v and v > 0]
    if not valid:
        return 0.0
    if len(valid) == 1:
        return round(valid[0], 2)
    folded = _octave_fold(valid, anchor)
    return round(statistics.median(folded), 2)


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

def analyze_ensemble(
    audio_path: Path,
    *,
    algos: Iterable[str] = _DEFAULT_ALGOS,
    use_replicate: bool = True,
    cache_dir: Path | None = None,
    force: bool = False,
) -> dict:
    """Run the ensemble and return ``{bpm, key, camelot, votes, ...}``.

    Same triple as ``audio_analyzer.analyze``, plus a ``votes`` dict showing
    every individual model's BPM call for debugging / UI surface.
    """
    audio_path = Path(audio_path)
    local = _local_analyze(audio_path)

    votes: dict[str, float] = {"librosa": local["bpm"]}
    failures: dict[str, str] = {}
    latencies: dict[str, float] = {}

    if use_replicate:
        # max_workers=1 → serialize Replicate calls. Rate limit on low-credit
        # accounts is 6 req/min with burst=1, so parallel calls just spin on
        # 429-retries. Sequential is faster end-to-end at this scale.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            futures = {
                ex.submit(call_essentia_bpm, audio_path, a, cache_dir=cache_dir, force=force): a
                for a in algos
            }
            for fut in concurrent.futures.as_completed(futures):
                algo = futures[fut]
                key = f"essentia-{algo}"
                try:
                    r = fut.result()
                    votes[key] = r["bpm"]
                    latencies[key] = r["latency_s"]
                except Exception as exc:  # noqa: BLE001 — surface per-model failures
                    failures[key] = f"{type(exc).__name__}: {exc}"

    bpm = aggregate_bpms(votes)
    return {
        "bpm": bpm,
        "key": local["key"],
        "camelot": local["camelot"],
        "votes": votes,
        "failures": failures,
        "latencies_s": latencies,
    }


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(
            "Usage: python audio_analyzer_ensemble.py <audio_path> [algo1 algo2 ...]"
        )
        sys.exit(1)
    p = Path(sys.argv[1])
    algos = tuple(sys.argv[2:]) if len(sys.argv) > 2 else _DEFAULT_ALGOS
    print(f"Analyzing {p} with algos={algos} ...")
    out = analyze_ensemble(p, algos=algos)
    print(json.dumps(out, indent=2))

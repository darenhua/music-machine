"""YouTube search + MP3 download for the DJ-set helper notebook.

This module exposes two sync functions:

    search_youtube(query, n=10) -> list[dict]
        Searches YouTube using yt-dlp's `ytsearchN:` provider and returns
        a list of result dicts with id/title/channel/duration_seconds/url/
        thumbnail_url.

    download_mp3(video_id, out_dir) -> Path
        Downloads the given video as a 320 kbps MP3 into `out_dir`, named
        `{video_id}.mp3`. Idempotent — skips if the file already exists.

Strategy choice (yt-dlp's `ytsearchN:` vs YouTube Data API):
  * `ytsearchN:` is the obvious default — no API key, no quota, ships
    with `yt-dlp` which we already need for downloads. We use
    `extract_flat=True` + `skip_download=True` so the search is cheap
    (one HTML/JSON fetch, no per-video probe). Latency for n=10 is
    typically 1-3s on a residential connection.
  * YouTube Data API v3 is faster and has richer metadata but requires
    an API key + has a 10k units/day quota (each search costs 100). Not
    worth it for a personal tool unless we hit rate limits.

System dependencies:
  * ffmpeg — required for MP3 conversion. macOS users: `brew install
    ffmpeg`. This module raises `RuntimeError` if ffmpeg is missing.

Risks / caveats:
  * YouTube occasionally blocks unauthenticated extraction (cookies/age
    gates). The library handles most cases, but expect occasional
    failures on age-restricted or region-locked content.
  * Search quality is whatever YouTube's relevance ranking gives you for
    the query string. Fuzzy queries like "disclosure latch" work fine;
    short or ambiguous queries surface low-quality matches.
  * `ytsearchN:` returns at most N results; we don't paginate.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


def _check_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg isn't on PATH."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found on PATH. Install with `brew install ffmpeg` "
            "(macOS) or your distro's package manager."
        )


def _build_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _normalize_thumbnail(entry: dict[str, Any]) -> str | None:
    """Pick a reasonable thumbnail URL from the yt-dlp entry."""
    thumbnails = entry.get("thumbnails") or []
    if thumbnails:
        # yt-dlp orders thumbnails low → high quality. Pick the highest.
        return thumbnails[-1].get("url")
    return entry.get("thumbnail")


def search_youtube(query: str, n: int = 10) -> list[dict]:
    """Search YouTube and return up to `n` result dicts.

    Each dict has keys: id, title, channel, duration_seconds, url, thumbnail_url.
    `duration_seconds` may be None for live streams / unknown durations.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if n <= 0:
        raise ValueError("n must be positive")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,  # don't probe each video — just list the search page
        "default_search": "ytsearch",
        "noplaylist": True,
    }

    search_target = f"ytsearch{n}:{query}"
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(search_target, download=False)
    except DownloadError as exc:
        raise RuntimeError(f"YouTube search failed for {query!r}: {exc}") from exc

    entries = (info or {}).get("entries") or []
    results: list[dict] = []
    for entry in entries:
        if not entry:
            continue
        video_id = entry.get("id")
        if not video_id:
            continue
        duration = entry.get("duration")
        results.append(
            {
                "id": video_id,
                "title": entry.get("title") or "",
                "channel": entry.get("channel") or entry.get("uploader") or "",
                "duration_seconds": int(duration) if duration is not None else None,
                "url": entry.get("url") or _build_url(video_id),
                "thumbnail_url": _normalize_thumbnail(entry),
            }
        )
    return results


def download_mp3(
    video_id: str,
    out_dir: Path,
    filename: str | None = None,
) -> Path:
    """Download `video_id` as a 320 kbps MP3 to `out_dir/<filename>`.

    If `filename` is None or empty, falls back to `{video_id}.mp3` for
    backwards compatibility with pre-title-naming callers. The filename
    must already be sanitized by the caller (the worker derives it from
    metadata.json, which sanitizes the YouTube title at enqueue time).

    Idempotent: returns immediately if the target file already exists.
    Raises RuntimeError on download/conversion failure (or if ffmpeg is
    missing).
    """
    if not video_id or not isinstance(video_id, str):
        raise ValueError("video_id must be a non-empty string")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    final_name = filename or f"{video_id}.mp3"
    if not final_name.lower().endswith(".mp3"):
        final_name = f"{final_name}.mp3"
    target = out_dir / final_name
    if target.exists() and target.stat().st_size > 0:
        return target

    _check_ffmpeg()

    # We can't ask yt-dlp's output template to use an arbitrary
    # external string, so we hand it a fixed `{video_id}.{ext}` template
    # and then `os.rename` the result into place. Two reasons this is
    # safer than embedding the title into the outtmpl:
    #   1. yt-dlp performs its own filename sanitization, which differs
    #      from ours (and from what metadata.json claims). A rename
    #      after-the-fact guarantees on-disk == metadata.
    #   2. The same logic still works for the fallback path (no
    #      filename → `{video_id}.mp3`): the rename becomes a no-op.
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
        "keepvideo": False,
    }

    try:
        with YoutubeDL(opts) as ydl:
            ydl.download([_build_url(video_id)])
    except DownloadError as exc:
        raise RuntimeError(
            f"YouTube download failed for video_id={video_id!r}: {exc}"
        ) from exc

    raw_target = out_dir / f"{video_id}.mp3"
    if not raw_target.exists():
        candidates = list(out_dir.glob(f"{video_id}.*"))
        raise RuntimeError(
            f"Expected {raw_target} after download, but it doesn't exist. "
            f"Found instead: {candidates}"
        )
    if raw_target != target:
        raw_target.rename(target)
    return target


if __name__ == "__main__":
    # Smoke test: search a known-good track and download the top hit.
    import json
    import tempfile

    query = "Disclosure Latch"
    print(f"Searching: {query!r}")
    hits = search_youtube(query, n=5)
    print(f"Got {len(hits)} hits:")
    print(json.dumps(hits, indent=2))

    if not hits:
        raise SystemExit("No search results — smoke test cannot continue.")

    top = hits[0]
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        print(f"\nDownloading top hit ({top['id']} — {top['title']!r}) to {tmp_path}")
        mp3_path = download_mp3(top["id"], tmp_path)
        size_mb = mp3_path.stat().st_size / 1024 / 1024
        print(f"OK: {mp3_path} ({size_mb:.2f} MB)")

        # Idempotency check.
        again = download_mp3(top["id"], tmp_path)
        assert again == mp3_path, "idempotent call should return same path"
        print("Idempotent re-download: OK (skipped)")

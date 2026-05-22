"""Fixture downloader for the audio_analyzer eval suite.

Run this ONCE (or whenever you add new songs to `songs.json`) to populate
`downloads/{video_id}.mp3` for every entry. This is the only piece that needs
the network or `youtube_fetcher`; `run_eval.py` runs purely against the cached
MP3s and never touches yt-dlp.

Usage
-----
    uv run python scripts/eval/fetch_songs.py            # download all missing
    uv run python scripts/eval/fetch_songs.py --force    # re-download even if cached
    uv run python scripts/eval/fetch_songs.py --resolve  # also fill missing video_id
                                                         # fields back into songs.json

Behavior
--------
* If an entry has a `video_id`, the file is downloaded to
  `downloads/{video_id}.mp3` (skipped if already present and non-empty).
* If an entry has no `video_id`, the script searches YouTube via
  `youtube_fetcher.search_youtube(youtube_query, n=1)` and uses the top hit.
  With `--resolve`, the resolved id is written back into `songs.json` so
  subsequent runs are deterministic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `youtube_fetcher` importable when running from repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "library"))

from youtube_fetcher import download_mp3, search_youtube  # noqa: E402

_DOWNLOAD_DIR = _REPO_ROOT / "downloads"


def _resolve_video_id(query: str) -> str:
    hits = search_youtube(query, n=1)
    if not hits:
        raise RuntimeError(f"no YouTube hits for {query!r}")
    return hits[0]["id"]


def fetch_all(songs_path: Path, download_dir: Path, force: bool, resolve: bool) -> None:
    songs = json.loads(songs_path.read_text())
    download_dir.mkdir(parents=True, exist_ok=True)

    mutated = False
    for i, song in enumerate(songs, 1):
        title = song.get("title", "<no-title>")
        artist = song.get("artist", "<no-artist>")
        vid = song.get("video_id")

        if not vid:
            query = song.get("youtube_query", f"{artist} {title}")
            print(f"[{i}/{len(songs)}] resolving — {title} — {artist!r}")
            vid = _resolve_video_id(query)
            if resolve:
                song["video_id"] = vid
                mutated = True

        target = download_dir / f"{vid}.mp3"
        if target.exists() and target.stat().st_size > 0 and not force:
            print(f"[{i}/{len(songs)}] cached — {title} ({vid})")
            continue

        print(f"[{i}/{len(songs)}] downloading — {title} ({vid})")
        if force and target.exists():
            target.unlink()
        path = download_mp3(vid, download_dir)
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"            -> {path.name} ({size_mb:.2f} MB)")

    if mutated:
        songs_path.write_text(json.dumps(songs, indent=2) + "\n")
        print(f"\nWrote resolved video_ids back to {songs_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--songs",
        default=str(Path(__file__).parent / "songs.json"),
        help="Path to the songs.json fixture",
    )
    parser.add_argument(
        "--download-dir",
        default=str(_DOWNLOAD_DIR),
        help="Where MP3s live (default: <repo>/downloads/)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the file is already cached",
    )
    parser.add_argument(
        "--resolve",
        action="store_true",
        help="If a song has no video_id, write the resolved id back to songs.json",
    )
    args = parser.parse_args()

    fetch_all(
        Path(args.songs),
        Path(args.download_dir),
        force=args.force,
        resolve=args.resolve,
    )


if __name__ == "__main__":
    main()

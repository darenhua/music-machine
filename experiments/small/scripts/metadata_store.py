"""Shared metadata.json read/write primitives.

Mirrors the design of `jobs_store.py`: lock-free reads, `fcntl.flock` +
atomic-rename writes. The notebook is the only writer; `worker.py` is a
reader (resolves video_id → current on-disk filename when dispatching
download / split jobs, so renamed files still work).

Storage shape (`experiments/small/metadata.json`):

    {
      "video_metadata": {
        "<video_id>": {
          "title":   "<youtube title at download time>",
          "filename": "<current on-disk filename, e.g. 'foo.mp3'>"
        }
      }
    }
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Callable

MetadataState = dict[str, Any]

# Characters allowed in the auto-derived filename. Keeps the FS safe and
# avoids surprises with shells / yt-dlp output templates. Anything outside
# this set is stripped, runs of "." are collapsed.
_FILENAME_CHARS = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    " .-_"
)


def empty_state() -> MetadataState:
    return {"video_metadata": {}}


def load(metadata_file: Path) -> MetadataState:
    """Lock-free read. Returns `empty_state()` on a missing / unparseable
    file — callers needing transactional consistency must use `mutate`."""
    if not metadata_file.exists():
        return empty_state()
    try:
        with metadata_file.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    data.setdefault("video_metadata", {})
    return data


def mutate(metadata_file: Path, fn: Callable[[MetadataState], Any]) -> Any:
    metadata_file = Path(metadata_file)
    metadata_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = metadata_file.with_suffix(metadata_file.suffix + ".lock")
    tmp_path = metadata_file.with_suffix(metadata_file.suffix + ".tmp")
    with lock_path.open("a+") as lockfp:
        fcntl.flock(lockfp.fileno(), fcntl.LOCK_EX)
        try:
            state = load(metadata_file)
            result = fn(state)
            with tmp_path.open("w", encoding="utf-8") as fp:
                json.dump(state, fp, indent=2, sort_keys=True)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp_path, metadata_file)
            return result
        finally:
            fcntl.flock(lockfp.fileno(), fcntl.LOCK_UN)


def sanitize_filename(raw: str) -> str:
    """Sanitize a YouTube title into a safe on-disk filename (no extension).
    Returns the empty string if nothing survives — callers should fall
    back to the video_id in that case."""
    s = raw.strip()
    s = "".join(c for c in s if c in _FILENAME_CHARS and ord(c) >= 0x20)
    while ".." in s:
        s = s.replace("..", ".")
    s = s.strip(" .")
    return s


def derive_filename(
    title: str, video_id: str, state: MetadataState
) -> str:
    """Pick a filename for `video_id` given its `title` and the current
    metadata state. Sanitizes the title; falls back to `{video_id}.mp3`
    if sanitization is empty. Resolves collisions by suffixing the
    video_id."""
    base = sanitize_filename(title)
    if not base:
        return f"{video_id}.mp3"
    candidate = f"{base}.mp3"
    vm = state.get("video_metadata") or {}
    taken = {
        m.get("filename"): vid
        for vid, m in vm.items()
        if m.get("filename")
    }
    if taken.get(candidate) in (None, video_id):
        return candidate
    return f"{base} ({video_id}).mp3"


def resolve_filename(metadata_file: Path, video_id: str) -> str:
    """Lock-free lookup: returns the current on-disk filename for
    `video_id` per metadata.json, or `{video_id}.mp3` as a fallback for
    videos with no metadata entry (legacy / pre-title downloads)."""
    state = load(metadata_file)
    entry = (state.get("video_metadata") or {}).get(video_id) or {}
    return entry.get("filename") or f"{video_id}.mp3"


def upsert_video(
    state: MetadataState,
    video_id: str,
    *,
    title: str | None = None,
    filename: str | None = None,
) -> dict:
    """In-place mutator (call inside `mutate`). Creates / updates the
    entry for `video_id`. Returns the resulting entry."""
    vm = state.setdefault("video_metadata", {})
    entry = vm.setdefault(video_id, {})
    if title is not None:
        entry["title"] = title
    if filename is not None:
        entry["filename"] = filename
    return entry


def delete_video(state: MetadataState, video_id: str) -> None:
    vm = state.setdefault("video_metadata", {})
    vm.pop(video_id, None)

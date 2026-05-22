"""Stem splitting via Replicate's ``ryan5453/demucs`` model.

Pipeline role: takes the local MP3 produced by ``youtube_fetcher`` and returns
four stem MP3s (vocals / drums / bass / other) for the downstream analyser.

File-upload approach
--------------------
Replicate's Demucs model expects ``audio`` to be a URL, but our input lives on
disk. The Replicate Python SDK (v1.x) accepts an open binary file handle for
file-typed inputs and uploads it transparently before kicking off the
prediction — no local HTTP server, no manual ``files.create``. See
https://replicate.com/docs/topics/predictions/input-files. Files up to 100 MB
are supported, which is more than enough for a single song (typical 3-5 min MP3
at 192 kbps lands around 5-8 MB).

Output shape (verified empirically against ``replicate==1.0.7`` + this pinned
model version): ``replicate.run`` returns a **dict of plain URL strings**
keyed by stem name, e.g. ``{"vocals": "https://replicate.delivery/yhqm/.../file",
"drums": "...", "bass": "...", "other": "..."}``. The SDK does NOT wrap these
as ``FileOutput`` objects for this model, so we explicitly download each URL
via stdlib ``urllib.request`` (no extra deps) and write to
``out_dir/<song-basename>/<stem>.mp3``. We still handle ``FileOutput`` /
``.read()`` / iter-of-bytes shapes defensively in case a future SDK version
changes the wrapping.

Each downloaded stem is validated: ``size > 10 KiB`` AND header is either
``b"ID3"`` (ID3v2 tag) or an MPEG audio sync frame (``0xFF`` followed by a
byte whose top 3 bits are set). A failed validation raises
``StemSplitterError`` rather than silently caching a corrupt file.

Environment
-----------
``REPLICATE_API_TOKEN`` must be set. ``marimo`` auto-loads ``.env`` via the
``[tool.marimo.runtime] dotenv`` setting in ``pyproject.toml`` — do NOT call
``dotenv.load_dotenv()`` here. The ``replicate`` SDK reads the token directly
from the environment.

Latency note
------------
Replicate Demucs (``htdemucs``, ``shifts=1``) typically takes 30-90 s per song
on a warm container, plus ~10-30 s of cold-start the first time. Surface this
in the UI as a loading indicator.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

import replicate

_MIN_STEM_BYTES = 10 * 1024  # 10 KiB — anything smaller is suspect.

# Pinned to the version referenced in the task spec.
_DEMUCS_MODEL = (
    "ryan5453/demucs:"
    "5a7041cc9b82e5a558fea6b3d7b12dea89625e89da33f0447bd727c2d0ab9e77"
)

STEM_NAMES: tuple[str, ...] = ("vocals", "drums", "bass", "other")


class StemSplitterError(RuntimeError):
    """Raised when the Replicate call fails or returns an unexpected payload."""


def looks_like_mp3(head: bytes) -> bool:
    """True if ``head`` looks like the start of an MP3 file.

    Accepts either an ID3v2 tag (``b"ID3"``) or a raw MPEG audio sync frame
    (``0xFF`` followed by a byte with the top 3 bits set — i.e. ``& 0xE0 == 0xE0``).
    """
    if len(head) < 3:
        return False
    if head[:3] == b"ID3":
        return True
    return head[0] == 0xFF and (head[1] & 0xE0) == 0xE0


def _validate_stem_file(path: Path) -> None:
    """Raise ``StemSplitterError`` if ``path`` is missing, too small, or not MP3."""
    if not path.exists():
        raise StemSplitterError(f"Stem file not written: {path}")
    size = path.stat().st_size
    if size < _MIN_STEM_BYTES:
        raise StemSplitterError(
            f"Stem file {path} is suspiciously small ({size} bytes; "
            f"need >= {_MIN_STEM_BYTES}). Likely an HTML error body or "
            f"truncated download."
        )
    with path.open("rb") as fh:
        head = fh.read(4)
    if not looks_like_mp3(head):
        raise StemSplitterError(
            f"Stem file {path} does not have MP3 magic bytes (got {head!r}). "
            f"Probably an error page or wrong content-type."
        )


def _existing_stems(song_dir: Path) -> dict[str, Path]:
    """Return the subset of stems already present on disk *and* validating as MP3.

    A file that exists but fails the MP3 magic-byte check is treated as missing
    so we re-download it on the next call.
    """
    found: dict[str, Path] = {}
    for stem in STEM_NAMES:
        p = song_dir / f"{stem}.mp3"
        if not p.exists() or p.stat().st_size < _MIN_STEM_BYTES:
            continue
        try:
            with p.open("rb") as fh:
                head = fh.read(4)
        except OSError:
            continue
        if looks_like_mp3(head):
            found[stem] = p
    return found


def _download_url(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest`` using stdlib urllib (no extra deps)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url) as resp, dest.open("wb") as fh:  # noqa: S310
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
    except urllib.error.URLError as e:
        raise StemSplitterError(f"Failed to download stem from {url}: {e}") from e


def _save_replicate_value(value: object, dest: Path) -> None:
    """Persist one stem-value from a Replicate output dict to ``dest``.

    Empirically, ``ryan5453/demucs`` returns plain URL strings (see module
    docstring). We dispatch on type explicitly — checking ``str`` FIRST so we
    don't accidentally iterate it character-by-character when handling
    ``FileOutput``-like objects.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(value, str):
        _download_url(value, dest)
        return

    # FileOutput exposes .read() returning all bytes.
    read = getattr(value, "read", None)
    if callable(read):
        data = read()
        if not isinstance(data, (bytes, bytearray)):
            raise StemSplitterError(
                f"Expected bytes from .read(), got {type(data).__name__}"
            )
        with dest.open("wb") as fh:
            fh.write(data)
        return

    # Fallback: iter-of-bytes (older FileOutput shape).
    try:
        iterator = iter(value)  # type: ignore[arg-type]
    except TypeError as e:
        raise StemSplitterError(
            f"Don't know how to save Replicate output of type {type(value).__name__}"
        ) from e

    with dest.open("wb") as fh:
        for chunk in iterator:
            if not isinstance(chunk, (bytes, bytearray)):
                raise StemSplitterError(
                    f"Expected bytes chunks, got {type(chunk).__name__}"
                )
            fh.write(chunk)


def split_stems(mp3_path: Path, out_dir: Path) -> dict[str, Path]:
    """Split ``mp3_path`` into 4 stem MP3s under ``out_dir``.

    Returns a mapping ``{stem_name: local_path}`` for vocals / drums / bass /
    other. Idempotent: if all four stems already exist (non-empty) on disk, the
    Replicate API is not called.

    Raises
    ------
    FileNotFoundError
        If ``mp3_path`` does not exist.
    StemSplitterError
        If the API token is missing, the model returns an unexpected payload,
        or fewer than four stems come back.
    """
    mp3_path = Path(mp3_path)
    out_dir = Path(out_dir)
    if not mp3_path.exists():
        raise FileNotFoundError(mp3_path)

    song_dir = out_dir / mp3_path.stem
    existing = _existing_stems(song_dir)
    if len(existing) == len(STEM_NAMES):
        return existing

    if not os.environ.get("REPLICATE_API_TOKEN"):
        raise StemSplitterError(
            "REPLICATE_API_TOKEN not set in environment. Add it to .env "
            "(marimo will auto-load it via [tool.marimo.runtime] dotenv)."
        )

    song_dir.mkdir(parents=True, exist_ok=True)

    # Open the MP3 as a binary handle so the Replicate SDK uploads it for us.
    # This is preferred over hosting our own HTTP server: the SDK handles the
    # presigned upload + URL-substitution transparently.
    with mp3_path.open("rb") as audio_fh:
        try:
            output = replicate.run(
                _DEMUCS_MODEL,
                input={
                    "jobs": 0,
                    "stem": "none",
                    "audio": audio_fh,
                    "model": "htdemucs",
                    "split": True,
                    "shifts": 1,
                    "overlap": 0.25,
                    "clip_mode": "rescale",
                    "mp3_preset": 2,
                    "wav_format": "int24",
                    "mp3_bitrate": 320,
                    "output_format": "mp3",
                },
            )
        except replicate.exceptions.ReplicateError as e:  # type: ignore[attr-defined]
            raise StemSplitterError(f"Replicate Demucs call failed: {e}") from e

    if not isinstance(output, dict):
        raise StemSplitterError(
            f"Expected dict output from Demucs, got {type(output).__name__}: "
            f"{output!r}"
        )

    saved: dict[str, Path] = {}
    for stem in STEM_NAMES:
        if stem not in output:
            raise StemSplitterError(
                f"Demucs output missing stem {stem!r}; got keys: {list(output)}"
            )
        dest = song_dir / f"{stem}.mp3"
        _save_replicate_value(output[stem], dest)
        _validate_stem_file(dest)
        saved[stem] = dest

    return saved


if __name__ == "__main__":
    # Smoke test. Skips gracefully when there's nothing to test against.
    import sys

    if not os.environ.get("REPLICATE_API_TOKEN"):
        print("REPLICATE_API_TOKEN not set — skipping smoke test.")
        sys.exit(0)

    # Look for any local mp3 to use as a fixture. Exclude prior outputs.
    here = Path(__file__).resolve().parent
    candidates = [
        p for p in sorted(here.glob("**/*.mp3"))
        if "stems_out" not in p.parts
    ]
    if not candidates:
        print("No .mp3 fixture found under", here, "— skipping smoke test.")
        sys.exit(0)

    fixture = candidates[0]
    out = here / "stems_out"
    print(f"Splitting {fixture} -> {out} ...")
    result = split_stems(fixture, out)
    for name, path in result.items():
        with path.open("rb") as _fh:
            head = _fh.read(4)
        print(
            f"  {name}: {path} "
            f"({path.stat().st_size} bytes, head={head!r}, "
            f"mp3_ok={looks_like_mp3(head)})"
        )

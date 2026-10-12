"""File → Markdown conversion with an on-disk cache."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import threading
from pathlib import Path

TEXT_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".mdx", ".txt", ".text", ".rst", ".org", ".adoc",
        ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini",
        ".xml", ".log", ".tex", ".bib", ".srt", ".vtt",
    }
)  # fmt: skip
"""Returned as-is (decoded as UTF-8)."""

CONVERTIBLE_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".xhtml", ".epub", ".ipynb"}
)
"""Converted to Markdown via markitdown."""

_SNIFF_BYTES = 8192
_lock = threading.Lock()


class ConversionError(Exception):
    """The file cannot be turned into Markdown."""


def detect_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def _looks_like_text(path: Path) -> bool:
    with path.open("rb") as f:
        head = f.read(_SNIFF_BYTES)
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte sequence cut off at the sniff boundary is still text.
        return e.start >= len(head) - 3
    return True


def is_text(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return True
    if ext in CONVERTIBLE_EXTENSIONS:
        return False
    return _looks_like_text(path)


def is_convertible(path: Path) -> bool:
    """True if :func:`to_markdown` is expected to succeed (text or a supported format)."""
    return path.suffix.lower() in CONVERTIBLE_EXTENSIONS or is_text(path)


def _cache_key(path: Path, st: os.stat_result) -> str:
    raw = f"{path}\x00{st.st_mtime_ns}\x00{st.st_size}".encode()
    return hashlib.sha256(raw).hexdigest()


def _markitdown(path: Path) -> str:
    from markitdown import MarkItDown  # heavy import; load lazily

    try:
        result = MarkItDown(enable_plugins=False).convert_local(str(path))
    except Exception as e:  # markitdown raises a variety of exception types
        raise ConversionError(f"conversion of {path.name} failed: {e}") from e
    text = result.text_content or ""
    if not text.strip():
        raise ConversionError(f"{path.name} converted to empty text (scanned/image-only document?)")
    return text


def to_markdown(path: Path, cache_dir: Path) -> str:
    """Return Markdown for *path*. Text formats are returned unchanged.

    Conversions are cached under *cache_dir*, keyed by (path, mtime, size).
    """
    if is_text(path):
        return path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() not in CONVERTIBLE_EXTENSIONS:
        raise ConversionError(
            f"unsupported format {path.suffix or '(no extension)'!r} "
            f"({detect_type(path)}); cannot convert {path.name} to Markdown"
        )
    st = path.stat()
    cache_file = cache_dir / f"{_cache_key(path, st)}.md"
    if cache_file.is_file():
        return cache_file.read_text(encoding="utf-8")
    text = _markitdown(path)
    with _lock:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(cache_file)
    return text


def html_to_markdown(path: Path) -> str:
    """Convert a fetched HTML file to Markdown (no caching)."""
    return _markitdown(path)

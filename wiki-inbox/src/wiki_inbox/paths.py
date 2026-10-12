"""Safe path handling: every agent-supplied path stays inside its wiki."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath

FAILED_DIR = "_failed"
SOURCE_SUFFIX = ".source.json"
REASON_SUFFIX = ".reason.md"
MAX_NAME_LEN = 120

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")
_DASHES = re.compile(r"[-\s]*-[-\s]*")
_DOT_RUNS = re.compile(r"\.{2,}")


class PathError(ValueError):
    """Raised for rejected or unresolvable paths."""


def safe_relpath(user_path: str) -> PurePosixPath:
    """Validate an agent-supplied relative path (``/`` separated)."""
    if not user_path or not user_path.strip():
        raise PathError("path is empty")
    if "\x00" in user_path or "\\" in user_path:
        raise PathError(f"invalid characters in path {user_path!r}")
    p = PurePosixPath(user_path)
    if p.is_absolute():
        raise PathError(f"absolute paths are not allowed: {user_path!r}")
    parts = [part for part in p.parts if part != "."]
    if not parts:
        raise PathError(f"path is empty: {user_path!r}")
    if ".." in parts:
        raise PathError(f"'..' is not allowed in paths: {user_path!r}")
    return PurePosixPath(*parts)


def resolve_inside(base: Path, rel: PurePosixPath | str) -> Path:
    """Join *rel* to *base* and require the fully resolved result to stay inside *base*.

    Symlinks are followed by ``resolve()``, so a link pointing outside *base*
    is rejected. The target itself need not exist.
    """
    rel_path = rel if isinstance(rel, PurePosixPath) else safe_relpath(rel)
    base_resolved = base.resolve()
    candidate = (base_resolved / rel_path).resolve()
    if candidate == base_resolved or not candidate.is_relative_to(base_resolved):
        raise PathError(f"path escapes {base.name}/: {rel_path}")
    return candidate


def existing_file(base: Path, user_path: str) -> tuple[Path, PurePosixPath]:
    """Resolve *user_path* inside *base* and require a regular file."""
    rel = safe_relpath(user_path)
    path = resolve_inside(base, rel)
    if not path.is_file():
        raise PathError(f"no such file in {base.name}/: {rel}")
    return path, rel


def sanitize_filename(name: str, default_ext: str = ".md") -> str:
    """Turn an agent-supplied name into a safe single path component.

    Separators and unsafe characters become ``-``, leading dots are stripped,
    the name is length-limited and gets *default_ext* if it has no extension.
    """
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.replace("/", "-").replace("\\", "-")
    name = _UNSAFE_CHARS.sub("-", name)
    name = _DOT_RUNS.sub("-", name)
    name = _DASHES.sub("-", name).strip(" -")
    name = name.lstrip(".").strip(" -")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    stem = stem.rstrip(". -") or "note"
    ext = ext.lower().strip()
    if not ext or not ext.isalnum() or len(ext) > 10:
        if ext:
            stem = f"{stem}-{ext}"
        ext = default_ext.lstrip(".")
    stem = stem[: MAX_NAME_LEN - len(ext) - 1].rstrip(". -") or "note"
    return f"{stem}.{ext}"


def unique_path(directory: Path, filename: str) -> Path:
    """Return ``directory/filename``, or ``name-1.ext``, ``name-2.ext``, … if taken."""
    candidate = directory / filename
    if not candidate.exists() and not candidate.is_symlink():
        return candidate
    # Keep compound suffixes like ``.tar.gz`` intact only for the last suffix.
    stem, dot, ext = filename.rpartition(".")
    if not dot or not stem:
        stem, ext = filename, ""
    suffix = f".{ext}" if ext else ""
    n = 1
    while True:
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        n += 1


def is_syncthing_temp(name: str) -> bool:
    return (name.startswith(".syncthing.") and name.endswith(".tmp")) or name.startswith(
        "~syncthing~"
    )


def is_sync_conflict(name: str) -> bool:
    return ".sync-conflict-" in name


def is_hidden(rel: PurePosixPath) -> bool:
    return any(part.startswith(".") for part in rel.parts)


def source_sidecar(path: Path) -> Path:
    """The ``<name>.source.json`` provenance sidecar belonging to *path*."""
    return path.with_name(path.name + SOURCE_SUFFIX)

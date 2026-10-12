import os
from pathlib import Path, PurePosixPath

import pytest

from wiki_inbox.paths import (
    PathError,
    existing_file,
    is_sync_conflict,
    is_syncthing_temp,
    resolve_inside,
    safe_relpath,
    sanitize_filename,
    unique_path,
)


@pytest.mark.parametrize(
    "bad",
    ["", "  ", "../wiki.toml", "a/../../b", "/etc/passwd", "a\\..\\b", "a\x00b", "..", ".", "./"],
)
def test_safe_relpath_rejects(bad: str) -> None:
    with pytest.raises(PathError):
        safe_relpath(bad)


def test_safe_relpath_normalises() -> None:
    assert safe_relpath("./sub/./a.md") == PurePosixPath("sub/a.md")


def test_resolve_inside_rejects_symlink_escape(tmp_path: Path) -> None:
    base = tmp_path / "inbox"
    base.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    os.symlink(outside, base / "link.txt")
    os.symlink(tmp_path, base / "dirlink")
    with pytest.raises(PathError):
        resolve_inside(base, "link.txt")
    with pytest.raises(PathError):
        resolve_inside(base, "dirlink/secret.txt")


def test_resolve_inside_allows_internal_symlink(tmp_path: Path) -> None:
    base = tmp_path / "inbox"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "a.md").write_text("x")
    os.symlink(base / "sub" / "a.md", base / "alias.md")
    assert resolve_inside(base, "alias.md") == (base / "sub" / "a.md").resolve()


def test_existing_file_requires_file(tmp_path: Path) -> None:
    (tmp_path / "d").mkdir()
    with pytest.raises(PathError):
        existing_file(tmp_path, "d")
    with pytest.raises(PathError):
        existing_file(tmp_path, "missing.md")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My Note", "My Note.md"),
        ("notes/../../etc/passwd", "notes-etc-passwd.md"),
        (".hidden.md", "hidden.md"),
        ("...", "note.md"),
        ("a..b", "a-b.md"),
        ("report.PDF", "report.pdf"),
        ("a:b*c?.txt", "a-b-c.txt"),
        ("café über.md", "cafe uber.md"),
        ("weird.ext!", "weird.ext"),
        ("bad.e x", "bad-e x.md"),
        ("x" * 300 + ".md", "x" * 117 + ".md"),
    ],
)
def test_sanitize_filename(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_never_contains_separator() -> None:
    for raw in ["a/b", "a\\b", "/abs", "..\\..\\x"]:
        name = sanitize_filename(raw)
        assert "/" not in name and "\\" not in name and not name.startswith(".")


def test_unique_path_suffixes(tmp_path: Path) -> None:
    assert unique_path(tmp_path, "a.md").name == "a.md"
    (tmp_path / "a.md").write_text("1")
    assert unique_path(tmp_path, "a.md").name == "a-1.md"
    (tmp_path / "a-1.md").write_text("2")
    assert unique_path(tmp_path, "a.md").name == "a-2.md"
    (tmp_path / "noext").write_text("3")
    assert unique_path(tmp_path, "noext").name == "noext-1"


@pytest.mark.parametrize(
    ("name", "temp", "conflict"),
    [
        (".syncthing.paper.pdf.tmp", True, False),
        ("~syncthing~paper.pdf.tmp", True, False),
        ("paper.sync-conflict-20260101-120000-ABCDEFG.pdf", False, True),
        ("paper.pdf", False, False),
    ],
)
def test_syncthing_detection(name: str, temp: bool, conflict: bool) -> None:
    assert is_syncthing_temp(name) is temp
    assert is_sync_conflict(name) is conflict

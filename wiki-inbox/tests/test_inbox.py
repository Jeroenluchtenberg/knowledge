import json
import os
import time
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tests.fixtures import HTML, make_docx, make_pdf
from wiki_inbox.server import Inbox


def touch(path: Path, content: str = "x", age: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return path


# ── inbox_list ───────────────────────────────────────────────────────────────


def test_list_filters_and_sorts(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "new.md", age=10)
    touch(main_inbox / "old.md", age=100)
    touch(main_inbox / "sub" / "nested.txt", age=50)
    touch(main_inbox / ".hidden.md")
    touch(main_inbox / ".stfolder" / "x")
    touch(main_inbox / ".syncthing.big.pdf.tmp")
    touch(main_inbox / "~syncthing~big.pdf.tmp")
    touch(main_inbox / "_failed" / "bad.md")
    touch(main_inbox / "note.sync-conflict-20260101-000000-ABC.md", age=5)
    result = inbox.list_files()
    assert result["wiki"] == "main"
    assert [f["name"] for f in result["files"]] == ["old.md", "sub/nested.txt", "new.md"]
    assert [f["name"] for f in result["conflicts"]] == ["note.sync-conflict-20260101-000000-ABC.md"]
    first = result["files"][0]
    assert first["convertible"] is True and first["type"] == "text/markdown"
    assert set(first) == {"name", "size", "mtime", "type", "convertible", "has_source"}


def test_list_hides_source_sidecar(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "paper.pdf")
    touch(main_inbox / "paper.pdf.source.json", "{}")
    files = inbox.list_files()["files"]
    assert [f["name"] for f in files] == ["paper.pdf"]
    assert files[0]["has_source"] is True


def test_list_skips_symlink_escape(inbox: Inbox, main_inbox: Path, tmp_path: Path) -> None:
    secret = touch(tmp_path / "secret.md")
    os.symlink(secret, main_inbox / "link.md")
    assert inbox.list_files()["files"] == []


def test_list_marks_binary_unconvertible(inbox: Inbox, main_inbox: Path) -> None:
    (main_inbox / "photo.bin").write_bytes(b"\x00\x01\x02")
    assert inbox.list_files()["files"][0]["convertible"] is False


def test_list_other_wiki(inbox: Inbox, wikis: dict[str, Path]) -> None:
    touch(wikis["other"] / "inbox" / "o.md")
    assert [f["name"] for f in inbox.list_files("other")["files"]] == ["o.md"]


# ── inbox_read ───────────────────────────────────────────────────────────────


def test_read_paging(inbox: Inbox, main_inbox: Path) -> None:
    text = "".join(chr(ord("a") + i % 26) for i in range(250))
    touch(main_inbox / "long.md", text)
    page1 = inbox.read("long.md")  # read_chunk = 100
    assert page1["content"] == text[:100]
    assert (page1["offset"], page1["next_offset"], page1["total_chars"]) == (0, 100, 250)
    assert page1["truncated"] is True
    page3 = inbox.read("long.md", offset=200)
    assert page3["content"] == text[200:]
    assert page3["next_offset"] is None and page3["truncated"] is False
    custom = inbox.read("long.md", offset=10, limit=5)
    assert custom["content"] == text[10:15] and custom["next_offset"] == 15
    past_end = inbox.read("long.md", offset=1000)
    assert past_end["content"] == "" and past_end["offset"] == 250
    assert past_end["next_offset"] is None


def test_read_paging_reassembles(inbox: Inbox, main_inbox: Path) -> None:
    text = "ünïcödé " * 40
    touch(main_inbox / "u.md", text)
    out, offset = "", 0
    while offset is not None:
        page = inbox.read("u.md", offset=offset, limit=33)
        out += page["content"]
        offset = page["next_offset"]
    assert out == text


def test_read_bad_args(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "a.md")
    with pytest.raises(ToolError):
        inbox.read("a.md", offset=-1)
    with pytest.raises(ToolError):
        inbox.read("a.md", limit=0)


@pytest.mark.parametrize("bad", ["../wiki.toml", "/etc/passwd", "sub/../../wiki.toml", "missing"])
def test_read_traversal_rejected(inbox: Inbox, main_inbox: Path, bad: str) -> None:
    with pytest.raises(ToolError):
        inbox.read(bad)


def test_read_symlink_escape(inbox: Inbox, main_inbox: Path, wikis: dict[str, Path]) -> None:
    os.symlink(wikis["main"] / "wiki.toml", main_inbox / "cfg.md")
    with pytest.raises(ToolError, match="escapes"):
        inbox.read("cfg.md")


def test_read_size_limit(inbox: Inbox, main_inbox: Path) -> None:
    (main_inbox / "big.md").write_bytes(b"a" * (inbox.settings.max_bytes + 1))
    with pytest.raises(ToolError, match="inbox_reject"):
        inbox.read("big.md")


def test_read_unconvertible_suggests_reject(inbox: Inbox, main_inbox: Path) -> None:
    (main_inbox / "blob.bin").write_bytes(b"\x00\xff" * 10)
    with pytest.raises(ToolError, match="inbox_reject"):
        inbox.read("blob.bin")


def test_read_pdf(inbox: Inbox, main_inbox: Path) -> None:
    make_pdf(main_inbox / "paper.pdf", "Attention is all you need")
    page = inbox.read("paper.pdf", limit=10_000)
    assert "Attention is all you need" in page["content"]
    # Cached: a second read is served from the cache directory.
    assert list(inbox.settings.cache_dir.glob("*.md"))
    assert inbox.read("paper.pdf", limit=10_000)["content"] == page["content"]


def test_read_docx(inbox: Inbox, main_inbox: Path) -> None:
    make_docx(main_inbox / "doc.docx", "Transformers", "Self-attention layers.")
    content = inbox.read("doc.docx", limit=10_000)["content"]
    assert "# Transformers" in content and "Self-attention layers." in content


def test_read_html(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "page.html", HTML)
    content = inbox.read("page.html", limit=10_000)["content"]
    assert "# Gradient descent" in content and "**optimisation**" in content


def test_cache_invalidated_on_change(inbox: Inbox, main_inbox: Path) -> None:
    path = make_pdf(main_inbox / "p.pdf", "first version")
    assert "first version" in inbox.read("p.pdf")["content"]
    make_pdf(path, "second version, longer")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert "second version" in inbox.read("p.pdf")["content"]


# ── inbox_add ────────────────────────────────────────────────────────────────


def test_add_and_collisions(inbox: Inbox, main_inbox: Path) -> None:
    r1 = inbox.add("Meeting notes", "# Notes\n")
    assert r1["file"] == "Meeting notes.md" and r1["path"] == "inbox/Meeting notes.md"
    r2 = inbox.add("Meeting notes.md", "second")
    r3 = inbox.add("Meeting notes", "third")
    assert (r2["file"], r3["file"]) == ("Meeting notes-1.md", "Meeting notes-2.md")
    assert (main_inbox / "Meeting notes.md").read_text() == "# Notes\n"
    assert not list(main_inbox.glob(".wiki-inbox-*"))


def test_add_sanitizes_traversal(inbox: Inbox, main_inbox: Path, wikis: dict[str, Path]) -> None:
    r = inbox.add("../../wiki.toml", "evil")
    assert "/" not in r["file"]
    assert (main_inbox / r["file"]).read_text() == "evil"
    assert (wikis["main"] / "wiki.toml").read_text() == 'name = "x"\n'


def test_add_size_limit(inbox: Inbox) -> None:
    with pytest.raises(ToolError, match="limit"):
        inbox.add("big.md", "a" * (inbox.settings.max_bytes + 1))


def test_add_unknown_wiki(inbox: Inbox) -> None:
    with pytest.raises(ToolError, match="unknown wiki"):
        inbox.add("a.md", "x", wiki="nope")


# ── inbox_archive ────────────────────────────────────────────────────────────


def test_archive_moves_to_raw(inbox: Inbox, wikis: dict[str, Path], main_inbox: Path) -> None:
    touch(main_inbox / "sub" / "deep" / "a.md", "A")
    touch(main_inbox / "sub" / "deep" / "a.md.source.json", "{}")
    r = inbox.archive("sub/deep/a.md")
    assert r["to"] == "raw/sub/deep/a.md" and r["committed"] is False
    raw = wikis["main"] / "raw"
    assert (raw / "sub" / "deep" / "a.md").read_text() == "A"
    assert (raw / "sub" / "deep" / "a.md.source.json").exists()
    assert not (main_inbox / "sub").exists()  # emptied subfolders are cleaned up
    assert main_inbox.is_dir()


def test_archive_collision(inbox: Inbox, wikis: dict[str, Path], main_inbox: Path) -> None:
    touch(wikis["main"] / "raw" / "a.md", "existing")
    touch(main_inbox / "a.md", "new")
    assert inbox.archive("a.md")["to"] == "raw/a-1.md"
    assert (wikis["main"] / "raw" / "a.md").read_text() == "existing"
    assert (wikis["main"] / "raw" / "a-1.md").read_text() == "new"


def test_archive_raw_symlink_escape(
    inbox: Inbox, wikis: dict[str, Path], main_inbox: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, wikis["main"] / "raw" / "sub")
    touch(main_inbox / "sub" / "a.md")
    with pytest.raises(ToolError, match="escapes"):
        inbox.archive("sub/a.md")
    assert (main_inbox / "sub" / "a.md").exists()


@pytest.mark.parametrize("bad", ["../wiki.toml", "../raw/x.md", "/etc/hosts"])
def test_archive_traversal(inbox: Inbox, bad: str) -> None:
    with pytest.raises(ToolError):
        inbox.archive(bad)


def test_archive_refuses_failed(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "_failed" / "x.md")
    with pytest.raises(ToolError, match="_failed"):
        inbox.archive("_failed/x.md")


# ── inbox_reject ─────────────────────────────────────────────────────────────


def test_reject_writes_reason(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "scan.pdf", "not really a pdf")
    touch(main_inbox / "scan.pdf.source.json", json.dumps({"source_url": "http://x"}))
    r = inbox.reject("scan.pdf", "image-only scan, no text layer")
    assert r["to"] == "inbox/_failed/scan.pdf"
    assert r["reason_file"] == "inbox/_failed/scan.pdf.reason.md"
    reason = (main_inbox / "_failed" / "scan.pdf.reason.md").read_text()
    assert "image-only scan" in reason and "Time:" in reason
    assert (main_inbox / "_failed" / "scan.pdf.source.json").exists()
    assert not (main_inbox / "scan.pdf").exists()
    # Second reject of a same-named file does not clobber the first.
    touch(main_inbox / "scan.pdf", "again")
    r2 = inbox.reject("scan.pdf", "still broken")
    assert r2["to"] == "inbox/_failed/scan-1.pdf"
    assert "image-only" in reason and (main_inbox / "_failed" / "scan-1.pdf.reason.md").exists()


def test_reject_requires_reason(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "a.md")
    with pytest.raises(ToolError):
        inbox.reject("a.md", "  ")


def test_reject_twice_refused(inbox: Inbox, main_inbox: Path) -> None:
    touch(main_inbox / "_failed" / "a.md")
    with pytest.raises(ToolError, match="already"):
        inbox.reject("_failed/a.md", "x")

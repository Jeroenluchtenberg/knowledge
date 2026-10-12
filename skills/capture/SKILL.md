---
name: capture
description: >
  Save a note, text snippet, or link to the wiki inbox for later
  ingestion. Use when the user says "save this for the wiki",
  "add this to my knowledge base", "remember this link", or shares
  something worth keeping without asking for a full ingest.
type: skill
status: active
last_updated: "2026-10-12"
disable-model-invocation: false
argument-hint: "[url-or-text]"
when_to_use: >
  Queuing a source (text, notes, a URL) for later processing by the
  ingest skill — quick capture, not synthesis.
tags: [capture, inbox, queue]
compatibility: "wiki-inbox >= 0.1.0"
---

# Capture

> New skill for remote MCP-only use — see [UPSTREAM.md](../UPSTREAM.md).

Queue a source in the wiki's `inbox/` so the **ingest** skill can turn
it into pages later. Capture is fast and does not touch wiki pages.

## A link

```
inbox_add_url(url: "<https-url>")
inbox_add_url(url: "<https-url>", filename: "<short-descriptive-name>")
```

The server fetches the URL itself. HTML is converted to Markdown with
a `> Source:` header; PDFs and other files are stored as-is with a
provenance sidecar. Private and internal addresses are refused. If the
fetch fails (paywall, 403, timeout), fall back to capturing the text
the user pasted with `inbox_add`, including the URL in it.

## Text, notes, or conversation excerpts

```
inbox_add(filename: "<short-descriptive-name>.md", content: "<markdown>")
```

- Write the content as Markdown. Start with a `# Title` line, then the
  material. Add a `Source:` line (URL, book, person, conversation)
  when known, and a line of context on why it matters if the user
  said so.
- Keep the user's material intact — do not summarize or rewrite it;
  synthesis happens at ingest time.
- No YAML frontmatter needed; inbox files are sources, not pages.
- Filenames are sanitized and never overwrite: a `-1`, `-2` suffix is
  added on collision. Use the returned `file` when reporting.

## Choosing the wiki

Both tools take an optional `wiki: "<name>"`; omit it for the default
wiki. List wikis with `wiki_spaces_list()` if the user mentions another
one.

## After capturing

Tell the user what was queued (file name) and that it will be
processed on the next ingest. Offer to run the **ingest** skill now
only if they want the knowledge integrated immediately.

---
name: ingest
description: >
  Process queued sources from the wiki inbox into synthesized wiki
  pages — list the inbox, read each file (PDF/DOCX/HTML converted to
  Markdown), search existing knowledge, write pages, validate and
  commit, then archive the source to raw/ or reject it.
type: skill
status: active
last_updated: "2026-10-12"
disable-model-invocation: true
argument-hint: "[inbox-file]"
when_to_use: >
  Processing files waiting in the inbox, ingesting new sources, or
  turning captured notes and links into wiki pages.
tags: [ingest, workflow, sources, inbox]
owner: jguibert@gmail.com
compatibility: "llm-wiki >= 1.0.0, wiki-inbox >= 0.1.0"
---

# Ingest

> Rewritten for remote MCP-only use around the wiki-inbox server — see
> [UPSTREAM.md](../UPSTREAM.md). The synthesis guidance is upstream's;
> the file handling is new.

Turn sources waiting in the inbox into synthesized, cross-referenced
wiki pages. Sources arrive via the **capture** skill (`inbox_add`,
`inbox_add_url`) or are dropped into `inbox/` by the user (Syncthing).

All file handling goes through MCP:

| Step | Tool |
|------|------|
| See the queue | `inbox_list` |
| Read a source as Markdown | `inbox_read` (pages via `next_offset`) |
| Mark a source done | `inbox_archive` → moves it to `raw/` |
| Give up on a source | `inbox_reject` → moves it to `inbox/_failed/` with a reason |
| Write pages | `wiki_content_*` (see the **content** skill) |
| Validate, index, commit | `wiki_ingest`, `wiki_content_commit` |

There is no filesystem access to the wiki (see **bootstrap**, Remote
mode): wiki-inbox moves files, the engine commits them.

## Workflow

### Step 1 — Scan the inbox

```
inbox_list()                 # default wiki
inbox_list(wiki: "<name>")   # another wiki
```

Returns `files` (oldest first) and `conflicts`. Each file has `name`
(path relative to `inbox/`), `size`, `mtime`, `type`, `convertible`
and `has_source` (a provenance sidecar exists for fetched binaries).

- If `files` is empty, report "Inbox is empty." and stop.
- If `conflicts` is non-empty, mention them to the user — they are
  Syncthing conflict copies that need a human decision. Do not ingest
  or archive them unless the user asks.
- If the user named a file, process only that one.

Process files **one at a time, oldest first** — finish every sub-step
for one file before starting the next.

### Step 2 — Process each file

#### 2a. Read the source

```
inbox_read(file: "<name>")
```

Returns `{content, offset, next_offset, total_chars, truncated}`.
While `next_offset` is not null, keep reading:

```
inbox_read(file: "<name>", offset: <next_offset>)
```

Text formats come back as-is; PDF, DOCX, PPTX, XLSX, HTML and EPUB
are converted to Markdown by the server. Do not skip a file because
of its extension — only reject it if `inbox_read` fails or the
content is unusable (see 2h).

Files fetched with `inbox_add_url` start with a `> Source: <url>`
line — use it as the source URL in the page.

#### 2b. Classify the source

Determine the source type by the material's nature, not its topic:

| Type | Source nature |
|------|-------------|
| `paper` | Academic — research papers, preprints |
| `article` | Editorial — blog posts, news, essays |
| `documentation` | Reference — product docs, API references |
| `clipping` | Web capture — browser clips, bookmarks |
| `transcript` | Spoken — meeting transcripts, podcasts |
| `note` | Informal — freeform drafts, quick captures |
| `data` | Structured — CSV, JSON, datasets |
| `book-chapter` | Published — book excerpts |
| `thread` | Discussion — forum threads, social media |

A blog post about academic research is `article`, not `paper`.

Identify the main topic and domain. Ask: *what does a reader learn
from this?* That drives placement, not the document format.

If the source type is ambiguous (e.g. a PDF that could be a paper
or documentation), ask the user before proceeding.

Skip proactively — reject in 2h with the reason — when:
- A raw extract duplicates a higher-level analysis of the same source
  that is also in the queue
- A document is superseded by a newer version in the queue

#### 2c. Find integration points

For the **first file** in a session, orient with the full wiki map:

```
wiki_list(format: "llms")
```

This produces all pages grouped by type with summaries in a single
call. Subsequent files in the same session can skip it.

Search for existing pages on the same topic:

```
wiki_search(query: "<main topic from source>")
wiki_search(query: "<candidate page title>")
wiki_search(query: "<topic>", type: "concept")
wiki_search(query: "<topic>", type: "<source-type>")
```

Read the most relevant results:

```
wiki_content_read(uri: "<relevant-slug>")
```

This serves two purposes:
- **Duplicate detection** — if a page already covers this subject,
  prefer updating it over creating a new one
- **Context** — understand the current state of knowledge so the
  new page integrates naturally

If multiple candidate pages could be the integration target, ask
the user which one to update.

#### 2d. Analyse the source

Before writing anything, produce an analysis of the source:

**Enumerate durable knowledge items:**
For each concept, claim, finding, or entity in the source, note:
- **What** — one sentence
- **Type** — `concept`, `paper`, `article`, `documentation`, etc.
- **Novelty** — `new` (not in wiki), `extends` (adds to existing page),
  `contradicts` (conflicts with existing page), `duplicate` (already covered)
- **Confidence** — estimated score (see calibration table in the **frontmatter** skill)

**Detect contradictions:**
For each page found in step 2c, compare its claims against the source.
Flag explicit contradictions — do not infer or speculate. Report:
- Which source claim contradicts which wiki page
- The slug of the page to update
- Whether the source or the wiki appears more authoritative

Pages with unresolved contradictions should be created or left with
`status: draft` so the **review** skill picks them up.

**Produce an ingest plan:**

```
Source: "Mixtral of Experts" (paper, 2024)

1. Mixture of Experts — concept — extends concepts/mixture-of-experts [0.9]
2. Mixtral architecture — concept — new: concepts/mixtral-architecture [0.85]
3. Sparse MoE routing efficiency — claim — extends concepts/sparse-routing [0.8]
4. Contradicts: concepts/moe-compute-cost claims O(n); source claims O(k/n) — update needed
5. Switch Transformer comparison — extends sources/switch-transformer-2021 [0.75]
```

Present the plan to the user and confirm before writing. If the user
redirects (e.g. "skip item 2", "merge 1 and 2"), update the plan.

If no user confirmation is available (automated pipeline), proceed with
the plan as written — flag any contradictions as `status: draft`.

#### 2e. Decide: create or update

- If a concept page already covers this topic → update it with new
  claims and sources
- If no existing page → create a new one
- If the source introduces a new concept → create a concept page
- If the source is a summary of a specific document → create a source
  page with the classified type
- One source can produce multiple pages — a rich document may warrant
  a source page plus new concept pages

#### 2f. Write pages

Follow the **content** skill for the exact tool sequence. In short:

- Each `new` item →
  `wiki_content_new(uri, type)` → `wiki_content_read(uri)` (scaffold)
  → `wiki_content_write(uri, content)` with the complete page
- Each `extends` item → `wiki_content_read(uri)` → `wiki_content_write`
  with the full updated page (accumulation contract below)
- Each `contradicts` item → see contradiction handling below
- Each `duplicate` item → skip; note it in the outcome

Get the field list for a type with
`wiki_schema(action: "show", type: "<type>", template: true)` and follow
the **frontmatter** skill for correct values.

For each page:
- Set the correct `type`
- Write all required fields for that type
- Add `sources`, `concepts`, `tags`, `aliases`, `confidence`, `claims` as relevant;
  set `confidence` to `0.5` on new pages (single source, unreviewed) and
  raise it only as corroborating evidence accumulates
- Record where the knowledge came from: the source URL (from the
  `> Source:` line) or the archived file name (`raw/<name>`, see 2h)
- Write a structured body — synthesize, do not copy verbatim
- Add links to related pages found in step 2c

Edge target types: each edge field (`sources`, `concepts`) declares
allowed target types via `x-graph-edges` / `target_types` in the
schema. Check with `wiki_schema(action: "show", type: "<type>")`.

**Updating an existing page (accumulation contract):**

1. Read the current page first with `wiki_content_read`
2. Preserve existing list values — do not drop `tags`, `aliases`, `read_when`,
   `sources`, `concepts`, or `claims` added by prior ingests
3. Add new values to lists, do not replace them
4. Update scalar fields (`summary`, `tldr`, `confidence`) only when
   there is a clear reason
5. Write the complete file, changing nothing else

**Contradiction handling:** when a source contradicts an existing page:
1. Read the existing page with `wiki_content_read`
2. Add a new entry to `claims[]` with the source's version and a reference to the source page
3. Lower the page-level `confidence` if the contradiction is unresolved
4. Add an open question in the body flagging the discrepancy for human review

Do not silently overwrite the existing claim — preserve both versions.

#### 2g. Validate and index

Dry-run each touched page to catch errors without committing:

```
wiki_ingest(path: "<slug>.md", dry_run: true)   # flat page
wiki_ingest(path: "<slug>", dry_run: true)      # bundle or section folder
```

Dry-run validates frontmatter against the type's JSON Schema and
reports edge target type mismatches. Fix reported issues by
re-writing the page, then ingest for real:

```
wiki_ingest(path: "<slug>.md")
```

The engine validates frontmatter, updates the search index, and
commits (when `ingest.auto_commit` is true, the default).

**Redaction (opt-in):** when ingesting external content that may
contain secrets — web clips, session transcripts, raw notes — pass
`redact: true`. The engine replaces matches of its built-in patterns
(GitHub PAT, OpenAI/Anthropic/AWS keys, Bearer tokens, email) with
`[REDACTED:pattern-name]` before commit. **Redaction is lossy**; do
not use it on pages that were already reviewed and committed.

#### 2h. Archive or reject the source

On success — every page from the plan written and `wiki_ingest`
succeeded:

```
inbox_archive(file: "<name>")
→ { "from": "inbox/<name>", "to": "raw/<name>", "committed": false }
```

The source moves to `raw/` (subfolders kept; a `-1`, `-2` suffix is
added on collision — use the returned `to`). A provenance sidecar,
if any, moves with it. A file in `raw/` means it was ingested.

On failure — `inbox_read` errors, the content is unusable (empty
scan, wrong file, spam), it is a duplicate/superseded source, or
validation keeps failing:

```
inbox_reject(file: "<name>", reason: "<one or two sentences>")
→ { "to": "inbox/_failed/<name>", "reason_file": "inbox/_failed/<name>.reason.md" }
```

Never leave a processed file in the inbox, and never delete files.

Track the outcome for this file:
- Pages created (slugs and types)
- Pages updated (what changed)
- Archived (new `raw/` path) or rejected (reason)
- Contradictions flagged (slug + which claims conflict)

Then proceed to the next file.

### Step 3 — Commit the moves

`inbox_archive` and `inbox_reject` do not commit. After the batch,
commit all pending changes (the `inbox/` → `raw/` and `_failed/` moves
and anything else outstanding):

```
wiki_content_commit(message: "ingest: archive <n> sources")
```

Omitting `slugs` commits everything in the wiki repository, including
`raw/` and `inbox/`. "nothing to commit" is fine — a later
`wiki_ingest` may already have picked the moves up.

### Step 4 — Summary

Report to the user:

- Files processed, with outcome per file (archived → `raw/...`,
  rejected → reason)
- Pages created (slugs and types)
- Pages updated (what changed)
- Contradictions flagged (slug, conflicting claims, resolution status)
- Syncthing conflicts still waiting in the inbox
- Suggested follow-ups (run **lint**, check **graph** edges,
  `wiki_suggest(slug: "<new-slug>")` for links)

## Linking policy

When adding links between pages — in frontmatter (`sources`,
`concepts`) or body (`[[wikilinks]]`) — apply this test: would a
reader of this page benefit from navigating to the linked page?

If the connection is only surface-level (shared keyword, same broad
domain), omit the link. Prefer fewer meaningful links over many weak
ones.

## Key rules

1. **The inbox is the queue** — read it with `inbox_list`/`inbox_read`
   only; never touch wiki paths with local tools
2. **You move the file, through MCP** — `inbox_archive` after a
   successful ingest, `inbox_reject` with a reason otherwise; the
   engine never moves inbox files, and nothing is ever deleted
3. **Commit at the end** — `wiki_content_commit` after the batch so
   the moves are recorded in git
4. **Synthesize, do not copy** — wiki pages contain synthesized
   knowledge, not verbatim transcripts of the source
5. **One source can produce multiple pages** — each page's `sources`
   field should reference the source page
6. **Process sequentially** — finish all sub-steps for one file
   before starting the next
7. **Reject, do not block** — if a file is unreadable or fails
   validation, reject it with the reason and continue
8. **Preserve on update** — never drop existing list values; read
   before write

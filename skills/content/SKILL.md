---
name: content
description: >
  Read, create, update, and organize wiki pages and sections —
  direct content operations outside of ingest and crystallize
  workflows.
type: skill
status: active
last_updated: "2026-04-27"
disable-model-invocation: false
argument-hint: "[slug] [--type concept|doc|section|skill|paper|...]"
when_to_use: >
  Reading a page, creating a page or section, updating existing
  content, or organizing wiki structure.
tags: [content, write, create, read, update, section, authoring]
owner: jguibert@gmail.com
compatibility: "llm-wiki >= 1.0.0"
---

# Content

> Modified for remote MCP-only use — see [UPSTREAM.md](../UPSTREAM.md).
> All reads and writes go through `wiki_content_*` tools; there is no
> local file access to the wiki.

Direct operations on wiki pages and sections. This skill covers
deliberate content work — reading, creating, updating, and organizing
pages outside of the [ingest](../ingest/SKILL.md) (inbox→raw) and
[crystallize](../crystallize/SKILL.md) (session→page) workflows.

Content operations do not validate or index — that's what
`wiki_ingest` does. Always ingest after writing.

Every `wiki_content_write` must be followed by `wiki_ingest` — that
is what validates, indexes and commits the page.

## Page anatomy

A wiki page is a Markdown file with YAML frontmatter. The file is
always: frontmatter block + blank line + body.

```
---
title: "Mixture of Experts"
summary: "Sparse routing of tokens to expert subnetworks."
type: concept
status: active
...
---

## Overview

MoE routes tokens to sparse expert subnetworks…
```

Start body content at `##` level. `#` is reserved for the page title
(derived from frontmatter `title`).

For frontmatter field conventions, see the
[frontmatter](../frontmatter/SKILL.md) skill.

## Flat page vs bundle

A flat page is a single `.md` file:

```
concepts/scaling-laws.md
```

A bundle is a folder containing `index.md` and co-located assets:

```
concepts/mixture-of-experts/
├── index.md
├── moe-routing.png
└── vllm-config.yaml
```

Use a bundle when the page has associated assets (images, diagrams,
data files). Assets always belong to one page — there is no shared
asset folder.

## Assets

Assets in a bundle are referenced with short relative paths:

```markdown
![MoE routing](./moe-routing.png)
See [vllm-config.yaml](./vllm-config.yaml)
```

List a page's assets:

```
wiki_content_read(uri: "<slug>", list_assets: true)
```

## Slug resolution

A slug is a path without extension, relative to `wiki/`. Resolution
checks two forms in order:

1. `concepts/mixture-of-experts.md` → flat file
2. `concepts/mixture-of-experts/index.md` → bundle

The same slug is used regardless of which form is on disk.

## Wiki links

Two syntaxes create graph edges from a page body:

| Syntax | Notes |
|--------|-------|
| `[[slug]]` | Wiki-native shorthand |
| `[text](slug)` | Standard Markdown — rendered by Hugo, indexed by the engine |
| `[text](wiki://name/slug)` | Cross-wiki — portable format; works in Hugo CMS and any Markdown renderer |

All three appear in `body_links`, `wiki_graph`, backlinks, and are checked by
the `broken-link` rule.

**Prefer `[text](wiki://name/slug)` for cross-wiki references** — it is the
portable format: indexed by the engine, rendered as a local link by Hugo CMS,
and readable as plain Markdown everywhere.

External URLs, `mailto:`, anchor-only links (`#section`), and image links
(`![alt](path)`) are not indexed.

**Cross-wiki targets** appear as external nodes in `wiki_graph`; set `cross_wiki: true`
to resolve them fully when both wikis are mounted.

## Read a page

```
wiki_content_read(uri: "<slug>")
```

Accepts a slug (`concepts/moe`), short URI (`wiki://concepts/moe`),
or full URI (`wiki://research/concepts/moe`).

Strip frontmatter with `no_frontmatter: true`.

When a page has `superseded_by` set, the output includes a notice
pointing to the replacement.

### Backlinks

Pass `backlinks: true` to discover which pages link to this one:

```
wiki_content_read(uri: "concepts/moe", backlinks: true)
```

Response is JSON:

```json
{
  "content": "# Mixture of Experts\n...",
  "backlinks": [
    { "slug": "concepts/scaling-laws", "title": "Scaling Laws" },
    { "slug": "papers/switch-transformer", "title": "Switch Transformer" }
  ]
}
```

`backlinks` is an array of `{ slug, title }` for every page whose body
contains `[[concepts/moe]]`. Returns an empty array when nothing links
to this page. No overhead when `backlinks` is omitted (default).

## Page history

```
wiki_history(slug: "<slug>")
```

Returns git commit history for the page — hash, date, message,
author. Use to assess freshness, track changes, or find the last
ingest commit. Rename tracking is enabled by default (`follow` parameter).

## Wiki overview

```
wiki_stats()
```

Returns page counts, type/status distribution, orphans, graph
connectivity, staleness, and index health in one call.

## List pages

```
wiki_list()
wiki_list(type: "concept")
wiki_list(status: "draft")
wiki_list(type: "section", page_size: 50)
```

Paginated results ordered alphabetically by slug. Each entry includes
slug, URI, title, type, status, and tags. Facets (`type`, `status`,
`tags` distributions) are always included.

## Search pages

```
wiki_search(query: "<topic>")
wiki_search(query: "<topic>", type: "concept")
```

## Create a page

Check for duplicates first:

```
wiki_search(query: "<topic>")
```

Check the frontmatter fields for the type:

```
wiki_schema(action: "show", type: "<type>", template: true)
```

Then create, fill and ingest the page:

```
1. wiki_content_new(uri: "<slug>", type: "<type>", name: "<Title>")
   → { "uri": "wiki://<wiki>/<slug>", "slug": "<slug>", "bundle": false }
2. wiki_content_read(uri: "<slug>")
   → the scaffolded page (frontmatter + body template)
3. wiki_content_write(uri: "<slug>", content: "<full page>")
4. wiki_ingest(path: "<slug>.md")
```

- `wiki_content_new` returns only `uri`, `slug` and `bundle` — not the
  scaffold — so step 2 is needed to get the generated frontmatter.
- In step 3, send the **complete** file: the scaffold's frontmatter with
  every field filled in, a blank line, then the body.
- `wiki_ingest` takes a path relative to the wiki root: `<slug>.md` for
  a flat page, `<slug>` (the folder) for a bundle or section. A bare
  slug for a flat page fails with "path does not exist".
- Validate first with `wiki_ingest(path: "<slug>.md", dry_run: true)`
  when unsure about frontmatter; fix and re-write before the real
  ingest.

For a bundle (page + co-located assets):

```
wiki_content_new(uri: "<slug>", bundle: true)
wiki_content_read(uri: "<slug>")
wiki_content_write(uri: "<slug>", content: "<full page>")
wiki_ingest(path: "<slug>")
```

Binary assets (images, PDFs) cannot be uploaded to a bundle over MCP.
Link to them instead, or ask the user to add them on the server.

Slugs are lowercase, hyphenated, relative to `wiki/`:
`concepts/mixture-of-experts`, `sources/switch-transformer-2021`.

## Update a page

`wiki_content_write` replaces the whole file, so every edit is
read → modify → write the full page:

1. Read the current page: `wiki_content_read(uri: "<slug>")`
2. Change **only** what the update needs. Copy all other text and
   all frontmatter fields verbatim — do not reformat, reorder, or
   "tidy" untouched parts.
3. Respect the accumulation contract: preserve existing list values
   (`tags`, `read_when`, `sources`, `concepts`, `claims`) — add, do
   not replace. Update scalar fields only with a clear reason.
4. If time has passed since the read (long reasoning, other tool
   calls, user turns), read the page again right before writing — a
   human or another agent may have changed it.
5. Write the complete page and ingest:

```
wiki_content_write(uri: "<slug>", content: "<full updated page>")
wiki_ingest(path: "<slug>.md")
```

## Retiring pages

Pages are never deleted: neither MCP nor the CLI has a delete. To
retire a page, change its frontmatter. Its history stays, and
links to it can be repointed. This section is the canonical retirement workflow;
other skills refer here.

| Situation | Action |
|-----------|--------|
| Page is obsolete or wrong, or is a test/junk page | **Archive** |
| Page was merged into, or replaced by, another page | **Supersede** |

### Archive

1. `wiki_content_read(uri: "<slug>")`
2. Set `status: archived` in the frontmatter. Add a one-line reason as
   the first line of the body:
   `> Archived YYYY-MM-DD: <reason>`. Keep everything else verbatim.
3. `wiki_content_write(uri: "<slug>", content: "<full page>")`

### Supersede

1. Make sure the target page exists and contains the knowledge that
   survives. Create it or update it first (see
   [Create a page](#create-a-page) and [Update a page](#update-a-page));
   move over claims, sources and tags still worth keeping, and ingest
   it.
2. `wiki_content_read(uri: "<old-slug>")`
3. Set `status: superseded` and `superseded_by: <target-slug>`. Keep
   the body verbatim — reads of the page append a
   `> **Superseded** by …` notice automatically.
4. `wiki_content_write(uri: "<old-slug>", content: "<full page>")`

### Then, for both: fix inbound links

1. Find every page that points at the retired page:
   - `wiki_content_read(uri: "<old-slug>", backlinks: true)` lists body
     links (`[[slug]]`, `[text](slug)`).
   - `wiki_graph(root: "<old-slug>", depth: 1, format: "json")` also
     shows frontmatter references (`sources`, `concepts`,
     `superseded_by`, …) — every edge with `"to": "<old-slug>"`.
     Backlinks do not include these.
2. For each linking page, read it, then:
   - **superseded:** repoint the link (body and frontmatter) to the
     target slug. A page that was itself `superseded_by` the old page
     now points at the target, so there are no chains.
   - **archived:** remove the link or reword the sentence so it no
     longer relies on the archived page. Remove the slug from
     frontmatter lists. A page `superseded_by` the archived page is
     archived too (same workflow).

   Change only the link; keep everything else verbatim. Write the full
   page.
3. Ingest every touched slug — the retired page and each linking page:
   `wiki_ingest(path: "<slug>.md")`.
4. Check: `wiki_content_read(uri: "<old-slug>", backlinks: true)`
   returns `"backlinks": []`, and
   `wiki_lint(rules: "broken-link")` has no new findings.

### How retired pages behave

| Tool | Behaviour |
|------|-----------|
| `wiki_search` | Still returned, but with `score: 0` (the deployment sets `search.status.archived`/`superseded` to `0.0`), ranked after every live hit. Treat hits with `score: 0` as retired: do not cite them. |
| `wiki_list` | Included. `wiki_list(status: "active")` lists only live pages; `status` takes one exact value (`archived`, `superseded`, …). |
| `wiki_suggest`, `wiki_graph` | Included, with no status filter. Never add a link to a retired page — check its `status` first. |
| `wiki_export` | Skips `archived` pages; **includes** `superseded` ones (`status: "all"` includes both). |
| `wiki_ingest` | Warns `schema validation: "archived" is not one of "active", "draft" …` — expected: the stock schemas do not list the retirement statuses and validation is loose. Do not revert the status. |
| `wiki_lint` | A retired page with no inbound links is reported as `orphan` — expected, leave it. No rule flags links *to* retired pages, which is why step 1 above exists. |

### Never

- Never delete a page, empty its body, or rename/move it to hide it.
- Never overwrite a page with a different topic — create a new page
  and supersede the old one.
- If a page really must be removed (e.g. secrets, personal or
  otherwise sensitive content), do not try to remove it through MCP.
  Tell the user it needs manual removal on the server
  (`git rm` + commit + `wiki_index_rebuild`; the content stays in git
  history unless that is rewritten) and stop.

## Create a section

A section is a directory with an `index.md` that groups related pages.
Sections use `type: section` and have no additional fields beyond base.

```
wiki_content_new(uri: "<slug>", section: true)
```

Missing parent sections are created automatically with their
`index.md`.

```
wiki/concepts/
├── index.md                    ← section index (type: section)
├── scaling-laws.md
└── mixture-of-experts.md
```

Sections are excluded from search results by default
(pass `include_sections: true` to `wiki_search` to include them). They serve as navigation,
not knowledge.

## Organize sections

List existing sections:

```
wiki_list(type: "section", page_size: 50)
```

Read a section's index page:

```
wiki_content_read(uri: "<section-slug>")
```

Update a section's index to reflect its current contents — add
links to child pages, update the summary.

## Commit changes

Content operations do not commit to git automatically. Commit
explicitly after writing:

```
wiki_content_commit(slugs: "<slug>")
wiki_content_commit(slugs: "<slug1>,<slug2>")
wiki_content_commit()
```

When committing by slug, the engine stages the right files:
- Flat page → single `.md` file
- Bundle → entire bundle folder
- Section → entire section folder recursively

`wiki_ingest` commits automatically when `ingest.auto_commit` is
true in the wiki config.

## View page changes

```
wiki_history(slug: "<slug>", limit: 5)
```

Returns hash, date, message and author per commit. Diffs are not
available over MCP; compare against the current page with
`wiki_content_read` when you need to know what changed.

## Suggest links

After creating or updating a page, suggest related pages to link:

```
wiki_suggest(slug: "<slug>")
```

Returns candidates with scores, reasons, and suggested frontmatter
fields. Apply the backlink quality test before adding suggestions.

## Update related pages

After creating or updating a page, check if related pages need
backlinks. Read before write — preserve existing values.

Apply the backlink quality test: would a reader benefit from
navigating there?

# Upstream

These skills are derived from
[geronimo-iia/llm-wiki-skills](https://github.com/geronimo-iia/llm-wiki-skills)
by Jerome Guibert, dual-licensed **MIT OR Apache-2.0** (see `LICENSE-MIT`
and `LICENSE-APACHE` in this directory). Our modifications are under the
same terms.

| | |
|---|---|
| Upstream repository | https://github.com/geronimo-iia/llm-wiki-skills |
| Pinned commit | `366e1507ff1b6e7ae15832eefc5428fc790b03af` (release 1.0.0, 2026-08-31) |
| Engine | llm-wiki 1.0.1 |
| Plugin name | `knowledge` (upstream: `llm-wiki`, renamed to avoid a clash) |

## Why modified

Upstream assumes the agent runs on the same machine as the wiki: it
writes pages with local file tools at paths from `wiki_content_new` /
`wiki_resolve`, moves inbox files with `mv`, and starts `llm-wiki serve`
via `.mcp.json`. In this deployment agents on other machines reach the
wiki **only over MCP** — llm-wiki (`wiki_*`) plus wiki-inbox (`inbox_*`)
— so every filesystem and CLI step was converted to an MCP call or
removed. No `.mcp.json` is shipped; agents connect to the remote servers
(see `compose/mcp.example.json`).

Every modified skill carries a "Modified for remote MCP-only use" note.

Second change set: a **retirement workflow**. llm-wiki has no page
delete, so pages are archived or superseded through frontmatter. The
canonical flow is "Retiring pages" in `content`; other skills link to
it. Changes are marked *(retirement)* below. Engine behaviour behind
them: `docs/decisions.md` V7–V9.

## Added

| Skill | What |
|---|---|
| `capture` | New. Queue a note (`inbox_add`) or URL (`inbox_add_url`) for later ingestion. Model-invocable ("save this for the wiki"). |
| `admin` | New, merged from upstream `spaces` + `config`: only the MCP parts (`wiki_spaces_list`, `wiki_spaces_set_default`, `wiki_info`, `wiki_config`). Space create/register/remove marked operator-only; `serve.*`/`logging.*` marked deployment-owned. *(retirement)* `search.status.archived`/`superseded` marked deployment-owned (`0.0`, still returned); example weight changed from `archived` to `stub`. |

## Removed

| Skill | Why |
|---|---|
| `setup` | Local installation of the engine — done on the server by hand / Ansible. |
| `migrate` | Filesystem admin of wiki directories — server-side task. |
| `configure-hugo` | Local Hugo site scaffolding — out of scope. |
| `spaces` | Merged into `admin` (CLI and local-path parts dropped). |
| `config` | Merged into `admin` (`--config`, `LLM_WIKI_CONFIG`, `.mcp.json` examples dropped). |

## Modified

| Skill | Changes |
|---|---|
| `bootstrap` | Added "Remote mode" section: MCP only (`wiki_*`, `inbox_*`); never use local Read/Write/Edit/Bash on wiki paths; ignore returned `path` values; no `wiki_resolve`. *(retirement)* Rule: never delete pages; retire via the content skill; `score: 0` search hits are retired. |
| `content` | Removed the "direct write pattern" (write to the returned local `path` with Edit/Write) and all `wiki_resolve` use. Create flow is now `wiki_content_new` → `wiki_content_read` (scaffold; `new` does not return it) → `wiki_content_write` (full page) → `wiki_ingest(path: "<slug>.md")`. Edit flow: read → write full page, change only what's needed, keep other text/frontmatter verbatim, re-read if time has passed. Replaced `git diff` bash examples with `wiki_history`. Removed `llm-wiki serve --watch` and `--include-sections` CLI references. Documented that `wiki_ingest` needs `<slug>.md` for flat pages and that binary bundle assets cannot be uploaded over MCP. *(retirement)* New section "Retiring pages": archive (`status: archived` + `> Archived YYYY-MM-DD: <reason>`) vs supersede (target first, then `status: superseded` + `superseded_by`); then fix inbound links found via backlinks (body links) **and** `wiki_graph(root, depth: 1)` (frontmatter refs, not in backlinks), ingest all touched slugs, verify; table of how retired pages behave per tool; never delete/empty/rename/overwrite — sensitive content → tell the user it needs manual `git rm` + commit + `wiki_index_rebuild` on the server, and stop. |
| `ingest` | Rewritten around wiki-inbox: `inbox_list` (mention `conflicts`) → per file, oldest first: `inbox_read` with paging → search/read existing pages → content flow → `wiki_ingest` → `inbox_archive` on success / `inbox_reject(reason)` on failure → `wiki_content_commit` after the batch → report. Kept upstream's classification, analysis, ingest-plan, accumulation contract, contradiction handling, redaction and linking guidance. Removed the "skip binary files" list (wiki-inbox converts them), the `wiki_ingest(path: "inbox/")` scan (that path is outside the wiki root), the `mv` step and the contradiction about who moves files. *(retirement)* Retired pages are never integration targets (skip `score: 0`, follow `superseded_by`); new "source replaces a page" case → new page + supersede the old one; never overwrite one topic's page with another; "nothing is ever deleted" extended to wiki pages. |
| `crystallize` | Added the `wiki_content_read` scaffold step after `wiki_content_new`; `wiki_ingest` paths made explicit (`<slug>.md`); "change nothing else / re-read" guidance on update. *(retirement)* Ignore retired pages as homes; plan action `replace <old> with <type> <new>`; new "Replace an existing page" section (create new → ingest → supersede old); never overwrite with a different topic. |
| `review` | Fixed `wiki_content_write(slug: …)` → `uri:`; read-before-write; explicit `<slug>.md` ingest path. *(retirement)* "Flag for deletion" (archived + `confidence: 0.1`) replaced by **Archive** / **Supersede** decisions via the content workflow; confirm with the user before retiring a page with `confidence` ≥ 0.7; queue skips retired pages; report counts archived/superseded; "Never delete" rule points to the workflow. |
| `lint` | Replaced the `llm-wiki lint --severity error` CLI reference with `wiki_lint(severity: "error")`; read-before-write on fixes; explicit ingest paths; registering new types delegated to the operator. Removed a duplicated "Modified" note. *(retirement)* Retired pages show up as `orphan` (expected); new "Links to retired pages" check (no engine rule exists); `wiki_suggest` proposes retired pages; fixes: junk orphan → archive, duplicates → merge + supersede, links to retired pages → repoint/remove. |
| `export` | Explained that exports are written on the server; removed "use the absolute `path` for follow-up file operations"; relative paths only. *(retirement)* Corrected the default filter: only `archived` is excluded (superseded and draft pages are included); `status: "all"` adds archived pages, not drafts. |
| `schema` | `wiki_schema(action: "add", schema_path: …)` reads a server path → draft the schema and hand it to the operator. Rephrased the `llm-wiki spaces create` CLI mention. *(retirement)* Removed the `delete_pages: true` instruction; never pass it — retire pages instead. |
| `stats` | Replaced the `llm-wiki stats … \| jq` CI example with an MCP-based health check. *(retirement)* Orphan → "new link or deletion" became "new link or archiving"; retired orphans are expected. |
| `frontmatter` | "Write the complete file" now names `wiki_content_write`. *(retirement)* New `status` section (values incl. `archived`/`superseded`, expected schema warning, archived example); `superseded_by` paired with `status: superseded`, with example. |
| `research` | *(retirement)* Retired hits have `score: 0` — don't cite; follow `superseded_by` on a superseded notice (chains too); never cite archived pages as current knowledge. |

## Unchanged

`graph`, `frontmatter/references/type-taxonomy.md` — audited,
no filesystem or CLI assumptions.

## Frontmatter compatibility

All `SKILL.md` files have `name` (matching the directory) and
`description`, as required by the agentskills / Hermes format. Upstream's
extra fields (`type`, `status`, `last_updated`, `when_to_use`, `tags`,
`owner`, `compatibility`, `disable-model-invocation`, `argument-hint`)
are kept; agents that don't know them ignore them.

## Updating from upstream

1. `git -C <llm-wiki-skills> log 366e150..HEAD -- skills/` to see what changed.
2. Port changes into the kept skills by hand, re-applying the changes above.
3. Re-run the audit: `grep -rnE "wiki_resolve|file tools|\bmv\b|llm-wiki [a-z]|Edit/Write" skills/`
   — only the `bootstrap` sentence forbidding local tools may remain.
4. Bump the pinned commit in this file.

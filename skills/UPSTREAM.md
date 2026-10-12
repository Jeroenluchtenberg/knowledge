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

## Added

| Skill | What |
|---|---|
| `capture` | New. Queue a note (`inbox_add`) or URL (`inbox_add_url`) for later ingestion. Model-invocable ("save this for the wiki"). |
| `admin` | New, merged from upstream `spaces` + `config`: only the MCP parts (`wiki_spaces_list`, `wiki_spaces_set_default`, `wiki_info`, `wiki_config`). Space create/register/remove marked operator-only; `serve.*`/`logging.*` marked deployment-owned. |

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
| `bootstrap` | Added "Remote mode" section: MCP only (`wiki_*`, `inbox_*`); never use local Read/Write/Edit/Bash on wiki paths; ignore returned `path` values; no `wiki_resolve`. |
| `content` | Removed the "direct write pattern" (write to the returned local `path` with Edit/Write) and all `wiki_resolve` use. Create flow is now `wiki_content_new` → `wiki_content_read` (scaffold; `new` does not return it) → `wiki_content_write` (full page) → `wiki_ingest(path: "<slug>.md")`. Edit flow: read → write full page, change only what's needed, keep other text/frontmatter verbatim, re-read if time has passed. Replaced `git diff` bash examples with `wiki_history`. Removed `llm-wiki serve --watch` and `--include-sections` CLI references. Documented that `wiki_ingest` needs `<slug>.md` for flat pages and that binary bundle assets cannot be uploaded over MCP. |
| `ingest` | Rewritten around wiki-inbox: `inbox_list` (mention `conflicts`) → per file, oldest first: `inbox_read` with paging → search/read existing pages → content flow → `wiki_ingest` → `inbox_archive` on success / `inbox_reject(reason)` on failure → `wiki_content_commit` after the batch → report. Kept upstream's classification, analysis, ingest-plan, accumulation contract, contradiction handling, redaction and linking guidance. Removed the "skip binary files" list (wiki-inbox converts them), the `wiki_ingest(path: "inbox/")` scan (that path is outside the wiki root), the `mv` step and the contradiction about who moves files. |
| `crystallize` | Added the `wiki_content_read` scaffold step after `wiki_content_new`; `wiki_ingest` paths made explicit (`<slug>.md`); "change nothing else / re-read" guidance on update. |
| `review` | Fixed `wiki_content_write(slug: …)` → `uri:`; read-before-write; explicit `<slug>.md` ingest path. |
| `lint` | Replaced the `llm-wiki lint --severity error` CLI reference with `wiki_lint(severity: "error")`; read-before-write on fixes; explicit ingest paths; registering new types delegated to the operator. |
| `export` | Explained that exports are written on the server; removed "use the absolute `path` for follow-up file operations"; relative paths only. |
| `schema` | `wiki_schema(action: "add", schema_path: …)` reads a server path → draft the schema and hand it to the operator. Rephrased the `llm-wiki spaces create` CLI mention. |
| `stats` | Replaced the `llm-wiki stats … \| jq` CI example with an MCP-based health check. |
| `frontmatter` | "Write the complete file" now names `wiki_content_write`. |

## Unchanged

`graph`, `research`, `frontmatter/references/type-taxonomy.md` — audited,
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

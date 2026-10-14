# dsh-wps365-plugin

Gives your DSH **WPS 365 cloud document** superpowers.

Once installed there is no more exporting, re-uploading, or copy-paste: ask the agent to read a WPS smart document, write content into one, or change a cell in a cloud spreadsheet, and it will actually operate on your WPS 365 documents — including documents other people shared with you by link.

> The plugin only ships a skill (`wps365-docs`) plus its scripts. It holds **no** WPS account data: every cloud operation runs through the `wps365-cli` you installed.

[中文说明](README.md)

---

## What it can do for you

| You can say | It will |
|---|---|
| "Summarize this WPS document" / "What are the action items in this smart document?" | Read a smart document (`.otl`) back into Markdown (headings, lists, **tables**, quotes, bold, code blocks); long documents are read in pages instead of dumping everything into context |
| "Show me the architecture diagram in that document" | Download **all** embedded images (including images inside table cells and processon diagrams) and pull only the ones you ask about into context |
| "Write this Markdown into that smart document" | Write to a smart document: replace the whole body, append, or prepend; preview the block structure first |
| "Change the second paragraph" | Edit a smart document in place, block by block, instead of rewriting the whole thing |
| "Read this .docx / .xlsx / .pptx" | Download the original and extract body Markdown, tables, **original formulas**, and one CSV per worksheet |
| "Fix the spreadsheet in this attachment and put it back" | Edit locally and upload the whole file back: `file_id` and share link stay the same, version history is kept and can be reverted; it can create new files too |
| "Set B2 to 99 in the cloud spreadsheet and add a formula in D2" | One-command cloud `.xlsx` round trip: download → edit → recalculate formula cache → upload |
| "Here is a link someone shared with me, read it" | Resolve kdocs / plus share links into a document and read it normally |
| "Update this document for me" | **Compare the cloud state before writing**: on a conflict it stops and tells you what changed instead of silently overwriting |

It also carries the hard-won lessons of this workflow (17 hard constraints for smart documents, the office-file API details, measured concurrency semantics), so problems like "my table disappeared", "formulas turned into empty cells", "the code block language was downgraded", or "the download still shows the old content" are handled instead of stumbled into.

## Before you start

1. **`wps365-cli`** — not installed by this plugin, only used by it.
   Default path `~/.local/bin/wps365-cli`. Authorize it first if you have not:
   ```bash
   wps365-cli config init
   wps365-cli auth login --device
   ```
   If it lives elsewhere, set `cli` in the config below. **A missing or unauthenticated CLI does not break the plugin**: the skill still loads and states plainly that the CLI was not found.

2. **Python with openpyxl** — needed to read `.xlsx` formulas and to edit cloud spreadsheets. By default the agent uses the interpreter from `load_workspace_dependencies` (that environment ships openpyxl / python-docx); pin your own with `python`.

3. **A `bash` tool in the session** — the model runs the scripts through it, plus file reads to page through an exported `doc.md` and `read_image` for pictures.

4. **Remove a hand-installed copy of the same skill (important)**
   If you previously installed this skill by hand at `~/.dsh/skills/wps365-docs`, that copy **wins** over the bundled one (a user-directory skill outranks a bundled skill of the same name), so the plugin's version would never be used. To use the bundled one:
   ```bash
   rm -rf ~/.dsh/skills/wps365-docs
   ```
   Skill roots are watched, so no restart is needed.

## Install

Ask the agent inside DSH to call:

```
plugin_manager { action: "install_bundle", target: "/absolute/path/dsh-wps365-plugin" }
```

Command-line equivalent:

```bash
dsh plugin --profile desktop add /absolute/path/dsh-wps365-plugin
```

Then start a session and ask it to read a WPS document by `file_id`. To confirm it took effect, the loaded skill body ends with a "本机资源（由 dsh-wps365-plugin 注入）" block giving the script directory, the `wps365-cli` path, and which Python to use.

Uninstall:

```
plugin_manager { action: "remove_bundle", target: "dsh-wps365-plugin" }
```

## Config (optional)

The row id is `skill-wps365-docs`. All three fields have defaults, so **no config is required**:

| Field | Default | Purpose |
|---|---|---|
| `cli` | `~/.local/bin/wps365-cli` | where `wps365-cli` lives; `false` stops reporting a CLI path |
| `python` | unset | interpreter for the scripts (needs openpyxl / python-docx); unset defers to `load_workspace_dependencies` |
| `assetRoot` | the bundled assets | absolute path, only needed if the skill assets are relocated |

Override the row in the profile's own `cordis.patch.yml` (a patch replaces the whole `config`, so restate every field):

```yaml
- id: skill-wps365-docs
  name: './index.js'
  config:
    cli: /opt/wps365/bin/wps365-cli
    python: /opt/python/bin/python3
```

A mistyped field fails activation instead of being ignored.

## Things to keep in mind

- **Cloud documents have no local undo**: the skill exports a backup before writing, but keep your own copy of anything important.
- **A write timeout is not a failed write**: the smart-document API may have committed while the response was lost, so it checks the block count before retrying rather than writing twice.
- **Read the conflict verdict**: when it reports a conflict, the right move is to pull the latest version and merge; forcing an overwrite takes an explicit flag.
- **Limits**: 1000 blocks per write for smart documents (the script batches automatically); code-block languages outside the API enum are kept as plain text (`powershell` is silently downgraded — it knows).
- **Office files go "download → edit locally → upload back"**: `.docx` / `.xlsx` / `.pptx` have no usable per-paragraph write API; preserving formulas, formatting, merges, and multiple sheets depends on uploading the whole file. Cloud `.xlsx` API writes land asynchronously, so verifying by download immediately after writes is misleading.
- **Permission boundary**: it acts with the identity and permissions of your local `wps365-cli`. The plugin neither stores nor touches credentials.
- **Context cost**: the always-loaded skill body stays short; detail (17 hard constraints, API specifics, measured concurrency semantics) lives in three reference documents read only when needed.
- **Upgrading**: a new version of the JS needs a restart; editing only the skill documents (`assets/**`) takes effect on the next skill load.
- This plugin registers **no** new tools and does not change your MCP configuration; `mcp__wps365__*` (if you configured WPS's MCP server) is a separate capability.

## Verifying the install

Offline self-check (no Harness, no network, no access to your documents):

```bash
node --test          # 30 cases: skill parsing and asset budgets, bundle/patch structure, apply() contract, config
node --check index.js
python3 -m py_compile assets/wps365-docs/scripts/*.py
```

Real check: have the agent read a document you know you can access and ask what its title is and how many tables it has, to confirm the content is real; then have it write once with `--dry-run` (converts without writing and creates nothing) to inspect the block structure and comparison output before touching a real document.

## Repository layout (for people changing the plugin)

```
index.js                      # Host plugin: registers wps365-docs with ctx.skills
src/plugin-config.js          # config validation (hand-written Standard Schema v1, dependency-free)
src/skill-catalog.js          # pure helpers: frontmatter parsing, catalog entry, local-resource block
cordis.patch.yml              # bundle patch inserting the skill-wps365-docs row
assets/wps365-docs/
├── SKILL.md                  # always-loaded body (~3.4k chars, kept under the 8192 pruning threshold)
├── references/               # otl.md / office-files.md / concurrency.md, read on demand
└── scripts/                  # otl_fetch.py otl_write.py file_fetch.py file_write.py xlsx_edit.py
locale/{en,zh}.json  icon.svg  test/
```

Asset paths are written as a `{skillDir}` placeholder and resolved into real absolute paths at load time, so the same assets work from a profile bundle, a workspace bundle, or a packaged app. The package is marked `"private": true` to prevent accidental publishing; drop it and add `repository` / `publishConfig` to publish.

## License

MIT

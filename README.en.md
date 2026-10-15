# @tryao/dsh-wps365-plugin

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
   Default path `~/.local/bin/wps365-cli` (`~\.local\bin\wps365-cli.exe` on Windows). Authorize it first if you have not:
   ```
   wps365-cli config init
   wps365-cli auth login --device
   ```
   If it lives elsewhere, set `cli` in the config below (it accepts `~` and `%NAME%` / `${NAME}` expansion, or a bare command name to look up on `PATH`). **A missing or unauthenticated CLI does not break the plugin**: the skill still loads and states plainly that the CLI was not found.

2. **Python with openpyxl** — needed to read `.xlsx` formulas and to edit cloud spreadsheets. By default the agent uses the interpreter from `load_workspace_dependencies` (that environment ships openpyxl / python-docx); pin your own with `python`.

3. **A shell tool in the session** — the model runs the scripts through it (`bash` on macOS and Linux, PowerShell or cmd on Windows), plus file reads to page through an exported `doc.md` and `read_image` for pictures.

4. **Windows** — every command in the skill body is a single line and its placeholders are injected by the plugin, so the same instructions run on both platforms. The injected "本机资源" block states your platform, interpreter, `wps365-cli` path and temp directory. On Windows, quote paths that contain spaces, and install the CLI with `irm https://open-docs.wpscdn.cn/cli/install.ps1 | iex`.

5. **Remove a hand-installed copy of the same skill (important)**
   If you previously installed this skill by hand at `~/.dsh/skills/wps365-docs`, that copy **wins** over the bundled one (a user-directory skill outranks a bundled skill of the same name), so the plugin's version would never be used. To use the bundled one:
   ```
   rm -rf ~/.dsh/skills/wps365-docs
   ```
   Skill roots are watched, so no restart is needed.

## Install

**From npm (recommended)** — install it by package name:

```bash
dsh plugin --profile desktop add @tryao/dsh-wps365-plugin
```

You can also search for `@tryao/dsh-wps365-plugin` in the DSH Plugin Manager UI.

**From a local directory** (for plugin development — it links the checkout, so edits apply immediately; this path is the **repository directory**, unrelated to the npm name):

```bash
dsh plugin --profile desktop add /absolute/path/dsh-wps365-plugin
```

Or ask the agent to call:

```
plugin_manager { action: "install_bundle", target: "@tryao/dsh-wps365-plugin" }
```

Then start a session and ask it to read a WPS document by `file_id`. To confirm it took effect, the loaded skill body ends with a "本机资源（由 @tryao/dsh-wps365-plugin 注入）" block giving the script directory, the `wps365-cli` path, and which Python to use.

Uninstall:

```
plugin_manager { action: "remove_bundle", target: "@tryao/dsh-wps365-plugin" }
```

> ⚠️ Use **0.1.1 or newer**. 0.1.0 has a severe bug that stops Harness from opening; it was never published to npm. See [CHANGELOG.md](CHANGELOG.md).

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

> ⚠️ This config is hand-written (to keep the package dependency-free) rather than a native Schemastery graph, so DSH's config inspection and settings UI do **not** render a config form for this row (`cordis_inspect_query` reports it as `unsupported`). It does not affect the plugin: the row mounts, the skill loads, and field validation still runs — only *changing* the config means editing the YAML above. The defaults are already right on a normal install.

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
scripts/check-package.mjs     # verifies the npm tarball contents (CI runs it too)
.github/workflows/            # ci.yml runs tests + the tarball check; release.yml publishes on a tag
CHANGELOG.md  RELEASING.md    # version history and the release procedure
```

Asset paths are written as a `{skillDir}` placeholder and resolved into real absolute paths at load time, so the same assets work from a profile bundle, a workspace bundle, or a packaged app.

The package is published to npm (`publishConfig.access: public`). The release procedure lives in [RELEASING.md](RELEASING.md) and the version history in [CHANGELOG.md](CHANGELOG.md). It deliberately declares **no `dependencies` / `peerDependencies`**: a profile directory cannot resolve `@deepseek-ai/*` (those live inside the app bundle), and declaring peer ranges only gives DSH's compatibility check a reason to reject the install.

## Changelog

- **0.1.2** — Adds Windows support and fixes: platform-aware CLI discovery, UTF-8 CLI pipes and script output, Windows discovery for the LibreOffice kit and Node, and an actionable message for legacy `.xls` files renamed to `.xlsx`. The Python suite now has 27 tests. Also detects block reordering during concurrent writes, searches `PATH` when the CLI setting is omitted, and no longer tries to launch Python as Node when Node is missing. See [CHANGELOG.md](CHANGELOG.md).
- **0.1.1** — Fixes a severe bug that made Harness **unable to open**. The hand-written Config in 0.1.0 carried a `toJSON()` method, and DSH's settings service selects the schemas it renders with a `"toJSON" in schema` duck-type test, then immediately reads `schema.meta.volatile`. A hand-written object has no `meta`, so the read threw a `TypeError`, `settings/describe` failed, and the desktop welcome RPC reported `Web RPC failed`. Deleting `toJSON()` fixes it (`~standard.validate` is unaffected, so config validation still runs), and a regression test now pins that shape. Also the first version published to npm.
- **0.1.0** — First release: packages the `wps365-docs` skill as a DSH bundle. **Do not use** — never published to npm.

## License

MIT

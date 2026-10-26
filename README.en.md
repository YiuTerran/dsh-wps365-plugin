# @tryao/dsh-wps365-plugin

Gives your DSH **WPS 365 cloud document** superpowers.

Once installed there is no more exporting, re-uploading, or copy-paste: ask the agent to read a WPS smart document, write content into one, or change a cell in a cloud spreadsheet, and it will actually operate on your WPS 365 documents — including documents other people shared with you by link.

> ⚠️ **This plugin is only for WPS 365 enterprise accounts. Do not use it with a personal account.**
>
> It calls cloud-document APIs that only WPS 365 enterprise accounts provide, and `wps365-cli` must be authorized with an enterprise account. Personal WPS accounts have no such access, so the plugin cannot read or write any document; please do not install it or try to use it with a personal account.

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
| "List the weekly reports in this multi-dimensional table" / "Filter by project" | Read a multi-dimensional table (`.dbt`): inspect the schema, then page/filter records; views, field selection, attachment inventories. Form views warn and fall back to the underlying table |
| "Change the notes on this record" | Snapshot → partial update → read-back verification; invalid Link/select values are silently cleared by the API, so the script validates first and reports mismatches |
| "Download the diagram from that weekly report" | Resolve the attachment `uploadId` from raw records and download it; correct the extension from the file header (the server was observed transcoding PNG to JPEG) |
| "Here is a link someone shared with me, read it" | Resolve kdocs / plus share links into a document and read it normally |
| "Update this document for me" | **Compare the cloud state before writing**: on a conflict it stops and tells you what changed instead of silently overwriting |

It also carries the hard-won lessons of this workflow (17 hard constraints for smart documents, 15 for multi-dimensional tables, the office-file API details, measured concurrency semantics), so problems like "my table disappeared", "formulas turned into empty cells", "the code block language was downgraded", "the download still shows the old content", or "the record write succeeded but the field is empty" are handled instead of stumbled into.

## Before you start

1. **A WPS 365 enterprise account (required)**
   This plugin only works with WPS 365 enterprise accounts; **personal WPS accounts are not supported — do not install or use it with one**. `wps365-cli` must be authorized with an enterprise account before the smart-document, cloud-document, and share-link features described here work.

2. **`wps365-cli` (required to run this plugin; the plugin does not install it, it only uses it)**
   Every cloud operation goes through this official CLI, so without it the plugin cannot read or write any document. [`wps365-open/cli`](https://github.com/wps365-open/cli) is its source and documentation repository; install it with the official installer:

   **macOS / Linux**

   ```bash
   curl -fsSL https://open-docs.wpscdn.cn/cli/install.sh | bash
   ```

   **Windows (PowerShell)**

   ```powershell
   irm https://open-docs.wpscdn.cn/cli/install.ps1 | iex
   ```

   **Windows (Git Bash)**

   ```bash
   curl -fsSL https://open-docs.wpscdn.cn/cli/install.sh | bash
   ```

   On macOS / Linux it installs to `~/.local/bin` (no sudo needed); on Windows the binary is `~\.local\bin\wps365-cli.exe`. To pin a version or an install directory, add the environment variable before `bash` at the end of the pipe: `... | WPS365_VERSION=v0.3.6 bash`, `... | WPS365_INSTALL_DIR=~/.local/bin bash`; or download the binary for your platform from the [releases page](https://github.com/wps365-open/cli/releases).

   Then bind an app and authorize (once):

   ```bash
   wps365-cli config init        # create/bind an app in the browser
   wps365-cli auth login --device
   wps365-cli user me            # confirm the signed-in identity
   ```

   `config init` opens a browser to create/bind the app and confirm the authorization; enterprise admins should set up the CLI app approval exemption in advance, and the full manual flow for creating an app, registering the callback URL, and requesting permissions is in the official [prerequisites guide](https://github.com/wps365-open/cli/blob/main/docs/prerequisites.md). If it lives elsewhere, set `cli` in the config below (it accepts `~` and `%NAME%` / `${NAME}` expansion, or a bare command name to look up on `PATH`). **A missing or unauthenticated CLI does not break the plugin**: the skill still loads and states plainly that the CLI was not found.

   **Token lifetime:** the access token lasts 2 hours, but the CLI refreshes it automatically and retries a 401 transparently. The delegated refresh token lasts **365 days** and rotates on every refresh, so `auth login --device` should be needed about once a year, not every 2 hours. If the refresh token expires or is revoked, the token store is lost, or the CLI falls back to the app identity and reports `required delegated scopes`, the plugin scripts refresh it, then run `auth login --device` (which opens the browser) and retry once. Set `WPS365_NO_AUTO_LOGIN=1` to disable the automatic prompt. You can also run `python3 <skill-dir>/scripts/auth.py ensure --cli <cli>` before direct CLI calls. Do not set `WPS365_ACCESS_TOKEN`: it bypasses storage and refresh and will expire after 2 hours. Sources: the official [user authorization flow](https://open.wps.cn/documents/app-integration-dev/wps365/server/certification-authorization/user-authorization/flow.html) (365-day refresh token, rotated on refresh) and the [CLI authentication notes](https://github.com/wps365-open/cli) (automatic refresh and 401 retry). A fresh login may report `refresh_token_expires_at` as `0001-01-01` (Go's zero time / no expiry supplied); this is **unknown, not expired**, and `auth.py` labels it that way without triggering recovery.

3. **Python with openpyxl** — needed to read `.xlsx` formulas and to edit cloud spreadsheets. By default the agent uses the interpreter from `load_workspace_dependencies` (that environment ships openpyxl / python-docx); pin your own with `python`.

4. **A shell tool in the session** — the model runs the scripts through it (`bash` on macOS and Linux, PowerShell or cmd on Windows), plus file reads to page through an exported `doc.md` and `read_image` for pictures.

5. **Windows** — every command in the skill body is a single line and its placeholders are injected by the plugin, so the same instructions run on both platforms. The injected "本机资源" block states your platform, interpreter, `wps365-cli` path and temp directory. On Windows, quote paths that contain spaces.

6. **Remove a hand-installed copy of the same skill (important)**
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
- **A write timeout is not a failed write**: the smart-document and record-create APIs may have committed while the response was lost, so it checks the block/record count before retrying rather than writing twice.
- **Read the conflict verdict**: when it reports a conflict, the right move is to pull the latest version and merge; forcing an overwrite takes an explicit flag.
- **Limits**: 1000 blocks per write for smart documents (the script batches automatically); 1024 records per dbsheet request and about 10 requests/second (the script caps a batch at 100); code-block languages outside the API enum are kept as plain text (`powershell` is silently downgraded — it knows).
- **Office files go "download → edit locally → upload back"**: `.docx` / `.xlsx` / `.pptx` have no usable per-paragraph write API; preserving formulas, formatting, merges, and multiple sheets depends on uploading the whole file. Cloud `.xlsx` API writes land asynchronously, so verifying by download immediately after writes is misleading.
- **A multi-dimensional table is a database, not a document or folder**: `drive file list` returns nothing and `file-content` fails with "文档内容抽取失败"; use dbsheet schema/records. FlexPaper sheets have no readable body/records, and Form views do not carry records.
- **Expired authorization recovers automatically**: when a script sees an expired token or the CLI's app-identity fallback, it refreshes first; if the refresh token is gone it runs `auth login --device` to open the browser, then retries the original command. There is no user authorization longer than the refresh token; `WPS365_AUTH=app` works unattended but cannot replace user-context document permissions.
- **Permission boundary**: it acts with the identity and permissions of your local `wps365-cli`. The plugin neither stores nor touches credentials.
- **Account boundary**: WPS 365 enterprise accounts only. Do not use a personal WPS account — the APIs are unavailable and the plugin cannot work.
- **Context cost**: the always-loaded skill body stays short; detail (17 smart-document + 15 multi-dimensional-table hard constraints, API specifics, measured concurrency semantics) lives in four reference documents read only when needed.
- **Upgrading**: a new version of the JS needs a restart; editing only the skill documents (`assets/**`) takes effect on the next skill load.
- This plugin registers **no** new tools and does not change your MCP configuration; `mcp__wps365__*` (if you configured WPS's MCP server) is a separate capability.

## Verifying the install

Offline self-check (no Harness, no network, no access to your documents):

```bash
node --test                    # skill parsing and asset budgets, bundle/patch structure, apply() contract, config
python3 test/scripts_test.py   # CLI discovery, pipe decoding, LibreOffice kit detection (stdlib only)
python3 test/dbsheet_test.py   # dbsheet pure functions + an offline loopback download; no cloud calls
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
├── SKILL.md                  # always-loaded body (~5k chars, kept under the 8192 pruning threshold)
├── references/               # otl.md / office-files.md / dbsheet.md / concurrency.md, read on demand
└── scripts/                  # otl_fetch.py otl_write.py file_fetch.py file_write.py xlsx_edit.py dbsheet.py auth.py
locale/{en,zh}.json  icon.svg  test/
scripts/check-package.mjs     # verifies the npm tarball contents (CI runs it too)
.github/workflows/            # ci.yml runs tests + the tarball check; release.yml publishes on a tag
CHANGELOG.md  RELEASING.md    # version history and the release procedure
```

Asset paths are written as a `{skillDir}` placeholder and resolved into real absolute paths at load time, so the same assets work from a profile bundle, a workspace bundle, or a packaged app.

The package is published to npm (`publishConfig.access: public`). The release procedure lives in [RELEASING.md](RELEASING.md) and the version history in [CHANGELOG.md](CHANGELOG.md). It deliberately declares **no `dependencies` / `peerDependencies`**: a profile directory cannot resolve `@deepseek-ai/*` (those live inside the app bundle), and declaring peer ranges only gives DSH's compatibility check a reason to reject the install.

## Changelog

- **0.2.6** — Requires outside-sandbox permission before the first authentication or cloud call in restricted environments, retaining that approved context for later cloud calls. Adds a credential-directory write probe as a fallback, failure-stage markers, and 7 regressions. See [CHANGELOG.md](CHANGELOG.md).
- **0.2.5** — Checks credential-storage errors only for failed business commands, preserving successful document content that happens to contain error messages. Adds a regression and reruns the full checks. See [CHANGELOG.md](CHANGELOG.md).
- **0.2.4** — Separates credential storage denials, rejected refresh grants, and CLI/network/server failures. Storage denials stop recovery and request execution permission instead of repeating login. Device verification stays visible; adds 15 auth regressions including fixture CLI process checks. See [CHANGELOG.md](CHANGELOG.md).
- **0.2.3** — Requires `auth.py ensure` before direct CLI calls, including shared-link metadata; clarifies user-identity recovery after app fallback, read-only `status`, and refresh with device login disabled. Adds `ensure --force-refresh` for remote auth rejection despite a locally valid token, and fixes the GitHub Release workflow. See [CHANGELOG.md](CHANGELOG.md).
- **0.2.2** — Fixes a fresh device login's `refresh_token_expires_at: 0001-01-01` being misread as expired: missing, zero, and malformed values are treated as unknown, `auth.py status` labels them as such, and recovery does not refresh or re-login. See [CHANGELOG.md](CHANGELOG.md).
- **0.2.1** — Adds automatic delegated-auth recovery: scripts recognize expired access tokens and the CLI app-identity fallback, refresh the delegated token, and if the 365-day refresh token is gone run `auth login --device` (opening the browser) before retrying once. `WPS365_NO_AUTO_LOGIN=1` disables the prompt. An API auth rejection forces a refresh even while the local token appears valid; explicit app identity from the environment or command line skips delegated login, with command-line identity taking precedence. Documents the 2-hour access-token / 365-day refresh-token split. See [CHANGELOG.md](CHANGELOG.md).
- **0.2.0** — Adds **multi-dimensional table (`.dbt`) support**: `dbsheet.py` covers schema, paginated/filtered reads, single-record reads, create, snapshot, partial update, delete, and attachment download. Writes compare a record-level snapshot and read every field back; Link display names are resolved to record IDs, invalid select/date values are validated first, and attachments resolve `uploadId` to a signed URL and correct the extension from the file header (an observed PNG was actually JPEG). Ships 15 measured .dbt constraints and the Form/FlexPaper limits, with matching Codex adapter, package manifest, and README updates. See [CHANGELOG.md](CHANGELOG.md).
- **0.1.4** — Fixes `.xlsx` write-back verification for repeated cell assignments, booleans, quoted/whitespace text, and clearing cells with sparse API responses. See [CHANGELOG.md](CHANGELOG.md).
- **0.1.3** — Fixes a **write that fails while looking successful**: byte-uploading an `.xlsx` is not adopted by an online spreadsheet (the version and the stored bytes both advance, the live cells keep their old values). `xlsx_edit.py` now reads back the cells it wrote through `--set` and exits 1 with the reason and the next step when they do not match; pure byte files are unaffected. The docs gain that hard constraint with the measurements, plus re-tested `versions` notes (`page_size` is required). The Python suite is now 34 tests. See [CHANGELOG.md](CHANGELOG.md).
- **0.1.2** — Adds Windows support and fixes: platform-aware CLI discovery, UTF-8 CLI pipes and script output, Windows discovery for the LibreOffice kit and Node, and an actionable message for legacy `.xls` files renamed to `.xlsx`. The Python suite now has 27 tests. Also detects block reordering during concurrent writes, searches `PATH` when the CLI setting is omitted, and no longer tries to launch Python as Node when Node is missing. See [CHANGELOG.md](CHANGELOG.md).
- **0.1.1** — Fixes a severe bug that made Harness **unable to open**. The hand-written Config in 0.1.0 carried a `toJSON()` method, and DSH's settings service selects the schemas it renders with a `"toJSON" in schema` duck-type test, then immediately reads `schema.meta.volatile`. A hand-written object has no `meta`, so the read threw a `TypeError`, `settings/describe` failed, and the desktop welcome RPC reported `Web RPC failed`. Deleting `toJSON()` fixes it (`~standard.validate` is unaffected, so config validation still runs), and a regression test now pins that shape. Also the first version published to npm.
- **0.1.0** — First release: packages the `wps365-docs` skill as a DSH bundle. **Do not use** — never published to npm.

## License

MIT

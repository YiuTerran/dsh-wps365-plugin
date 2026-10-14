# dsh-wps365-plugin

Ships the **`wps365-docs` skill** as a DeepSeek Harness (DSH) plugin bundle: install it into a profile and every session can read and write WPS 365 cloud documents.

[中文说明](README.md)

- **Skill**: `wps365-docs` — read/write/review WPS 365 smart documents (`.otl`) and office files (`.docx` / `.xlsx` / `.pptx`), always comparing for concurrent edits before writing.
- **Bundled resources**: `SKILL.md`, three on-demand `references/`, and five Python scripts.
- **No dependencies**: no `dependencies` / `peerDependencies`, so installation resolves nothing from a registry.
- **Host side only registers the skill**: the scripts are not executed by the plugin; the model runs them through `bash` as the skill instructs.

## Layout

```
index.js                      # Host plugin: registers wps365-docs with ctx.skills
src/plugin-config.js          # hand-written Standard Schema v1 config (dependency-free)
src/skill-catalog.js          # pure helpers: frontmatter parsing, catalog entry, resource block
cordis.patch.yml              # bundle patch inserting the skill-wps365-docs row
assets/wps365-docs/SKILL.md   # always-loaded body (~3.4k chars)
assets/wps365-docs/references/{otl,office-files,concurrency}.md
assets/wps365-docs/scripts/   # otl_fetch.py otl_write.py file_fetch.py file_write.py xlsx_edit.py
locale/{en,zh}.json  icon.svg  test/
```

### Why `SKILL.md` is split into references

The standard preset's `tool-result-pruner` (`thresholdChars: 8192`) trims the middle out of tool results larger than that (head 4096 + tail 1024). The original single-file skill was ~24 KB, so loading it would lose most of its content. Following the shipped DSH skills, the always-loaded body keeps only the essentials (the two document routes, the mandatory conflict check, command skeletons, three hard rules) and the detail lives in `references/`, each under the budget and read on demand.

The body writes paths as a `{skillDir}` placeholder. At load time the plugin appends a "local resources" block with the real absolute paths, so the document is location-independent (profile bundle, workspace bundle, or packaged app).

## Install

Use `plugin_manager` from inside DSH:

```
plugin_manager { action: "install_bundle", target: "/absolute/path/dsh-wps365-plugin" }
```

It performs package installation and bundle selection. Do not hand-edit the profile's `package.json` / `cordis.patch.yml` and do not run pnpm in the profile directory. Command-line equivalent:

```bash
dsh plugin --profile desktop add /absolute/path/dsh-wps365-plugin
```

Remove with `plugin_manager { action: "remove_bundle", target: "dsh-wps365-plugin" }`.

## Config

The row id is `skill-wps365-docs`. Every field has a default, so `config` may be omitted:

| Field | Type | Default | Purpose |
|---|---|---|---|
| `assetRoot` | string | bundled `assets/wps365-docs/` | absolute path, for packaged apps that relocate the assets |
| `cli` | string \| false | `~/.local/bin/wps365-cli` | path to the `wps365-cli` executable; `false` omits the CLI line |
| `python` | string \| false | unset | interpreter with openpyxl / python-docx; unset defers to `load_workspace_dependencies` |

Override the row in the profile's own `cordis.patch.yml` (a patch replaces the whole `config`, so restate every field):

```yaml
- id: skill-wps365-docs
  name: './index.js'
  config:
    cli: /opt/wps365/bin/wps365-cli
    python: /opt/python/bin/python3
```

A mistyped field fails activation (`unknown config field "..."`) instead of being ignored.

## Prerequisites

1. **`wps365-cli`** — not installed by this plugin. Default `~/.local/bin/wps365-cli`; authorize with `wps365-cli config init` and `wps365-cli auth login --device`. Set `cli` if it lives elsewhere. A missing CLI does not break activation: the plugin keeps loading and writes a ⚠️ line into the skill body.
2. **Python with openpyxl** — needed by `file_fetch.py` (`.xlsx` formula parsing) and `xlsx_edit.py`. The skill tells the model to use the interpreter from `load_workspace_dependencies`; pin one with `python` instead.
3. **`bash` tool** — the model runs the scripts through it (this plugin registers no Host tools); file read and `read_image` are used for the produced `doc.md` and images.

## Relationship to a user-installed copy

Same-name skills in one layer are ordered by rank, lowest wins:

| Source | rank |
|---|---|
| `<project>/.dsh/skills`, `<project>/.agents/skills` | 100, 200 |
| custom dirs, `~/.dsh/skills`, `~/.agents/skills` | 300, 400, 500 |
| **this plugin (bundled)** | **600** |

So a hand-installed `~/.dsh/skills/wps365-docs` shadows the bundled copy. Remove it to use the plugin's version; `dsh-skill-filesystem` watches the roots, so no restart is needed.

## Verify

Offline (no Harness, no network, no install):

```bash
node --test          # 30 cases: catalog helpers, bundle/patch structure, apply() contract, config
node --check index.js
python3 -m py_compile assets/wps365-docs/scripts/*.py
```

(One case parses the patch with the Host's `yaml` package when it is resolvable and skips otherwise; the repository itself adds no dependency.)

After installing: confirm the `skill-wps365-docs` row is active with `cordis_inspect_query`, then start a session and ask it to read a WPS document — the loaded skill body must contain the "本机资源（由 dsh-wps365-plugin 注入）" block.

## Known limitations

- Replacing an already-installed package with a new version needs a restart to load fresh JavaScript (config changes can go through HMR).
- Editing `assets/**` takes effect on the next skill load; editing `index.js` / `src/**` needs a reinstall plus restart.
- The Python scripts ship verbatim; their behavior is authoritative over the notes in `references/`.
- The package is marked `"private": true` to prevent accidental publishing; drop it and add `repository` / `publishConfig` to publish.

## License

MIT

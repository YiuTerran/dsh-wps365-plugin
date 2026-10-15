---
name: wps365-docs
description: Read, summarize, inspect, or edit WPS 365 cloud smart documents and Office files using the local wps365-cli. Use for .otl / AirPage documents, shared WPS links, and .docx, .xlsx, or .pptx files; preserve cloud changes with explicit conflict checks before writing.
---

# WPS 365 cloud documents

Use this skill when a task needs content from a WPS 365 document, a shared WPS link, or a change to a cloud document. This is a standalone Codex skill: it uses a local shell, Python, and the user's installed wps365-cli. It does not inject runtime context or provide credentials.

## Resolve local resources first

Before running a document script, resolve these values from the current machine and the skill catalog:

- {skillDir} is the directory containing this SKILL.md, using the actual path shown by the loaded skill catalog. Do not guess a plugin or cache directory.
- {python}: when available, call mcp__codex_app__load_workspace_dependencies and use its bundled Python path. Otherwise probe python3 and then python, using the first interpreter that starts. Check import openpyxl for .xlsx parsing/editing and import docx when a task needs python-docx; choose an interpreter that has the required package. Do not install packages automatically.
- {cli}: use the bundled _wps_cli.resolve() helper to locate the local executable. The helper lives at {skillDir}/scripts/_wps_cli.py in the packaged skill. Import it by adding that scripts directory to sys.path, call resolve(), and print the returned path. If it returns the default path but that file is absent, treat the CLI as unavailable. Do not run config init, device login, or an installer on the user's behalf.
- {tmp}: create a temporary directory with Python's tempfile.mkdtemp() and use the returned absolute path for exports, snapshots, and backups.

Keep tool discovery read-only. Use the temporary directory for required local exports, backups, snapshots, and edited files. Quote executable and file paths. Resolve the CLI by importing _wps_cli.resolve with the scripts folder on sys.path and printing the returned path.

POSIX probes (run only when the Codex workspace dependency tool did not supply Python):

    command -v python3
    command -v python
    "{python}" -c 'import sys,os; sys.path.insert(0, os.path.join(sys.argv[1], "scripts")); from _wps_cli import resolve; print(resolve())' "{skillDir}"
    test -f "{cli}"
    "{python}" -c 'import tempfile; print(tempfile.mkdtemp(prefix="wps365-"))'

PowerShell equivalents:

    Get-Command python3, python -ErrorAction SilentlyContinue
    & "{python}" -c "import sys,os; sys.path.insert(0, os.path.join(sys.argv[1], 'scripts')); from _wps_cli import resolve; print(resolve())" "{skillDir}"
    Test-Path -LiteralPath "{cli}"
    & "{python}" -c "import tempfile; print(tempfile.mkdtemp(prefix='wps365-'))"

When .xlsx parsing or editing is needed, check openpyxl with "{python}" -c 'import openpyxl'; for python-docx work, check docx the same way. In PowerShell use & "{python}" -c "import openpyxl" or import docx. If a required import fails, report the missing dependency without installing it. For subsequent commands, the resolved values are the {python}, {cli}, and {tmp} placeholders.


If wps365-cli is missing, stop before cloud operations and explain that the user needs to install it and authenticate on their own device. Point to the official installer for their platform: [POSIX installer](https://open-docs.wpscdn.cn/cli/install.sh) or [Windows PowerShell installer](https://open-docs.wpscdn.cn/cli/install.ps1). Do not execute either installer or authentication flow. If no shell is available, explain that these local scripts require one and continue only with information already available in the conversation.

## Choose the document path

| Cloud item | Read | Edit and write |
|---|---|---|
| Smart document .otl / AirPage | otl_fetch.py | otl_write.py, with block snapshots and a dry run as appropriate |
| Ordinary .docx, .xlsx, .pptx | file_fetch.py | Edit the downloaded original with an available Office skill, then file_write.py with its baseline |

These write paths have different semantics. Smart documents expose block operations; Office files are replaced as complete byte streams. Do not use one path as a substitute for the other.

## Read a document

Smart document export creates doc.md and, by default, downloads attachments to an img/ directory. The script prints a compact inventory so long document bodies and base64 export payloads stay out of the conversation:

    "{python}" "{skillDir}/scripts/otl_fetch.py" --file-id <file_id> --out "{tmp}/otl_<short-name>" --cli "{cli}"

PowerShell form:

    & "{python}" "{skillDir}\scripts\otl_fetch.py" --file-id <file_id> --out "{tmp}\otl_<short-name>" --cli "{cli}"

Read the resulting doc.md in pages with the available file-reading tool. Do not print a long document to stdout. Inspect an attachment only when the document indicates it matters, using the image-view capability available in the current environment on the listed local image path.

For an ordinary Office file:

    "{python}" "{skillDir}/scripts/file_fetch.py" --file-id <file_id> --out "{tmp}/wps_<short-name>" --download --cli "{cli}"

PowerShell form:

    & "{python}" "{skillDir}\scripts\file_fetch.py" --file-id <file_id> --out "{tmp}\wps_<short-name>" --download --cli "{cli}"

Read doc.md for extracted text and tables, and inspect meta.json for the cloud file baseline. For .xlsx, local parsing with openpyxl preserves formula text in the extracted view; API-only values may omit formulas or display rounded values. Use the locally downloaded original for Office edits.

If the user provides a WPS sharing link instead of a file_id, obtain its link_id from the final path segment and resolve the metadata with the local CLI. Then use the returned file_id with the appropriate fetch script:

    "{cli}" api get /v7/links/<link_id>/meta -o json

PowerShell form:

    & "{cli}" api get /v7/links/<link_id>/meta -o json

## Before every cloud write

Cloud documents are collaborative. Establish a baseline before editing, save a local backup, and pass the matching expected snapshot or expected hash/version to the write command. If the check reports a conflict, stop, fetch the latest content, and reconcile the requested change with it. Only use a force option after the user explicitly authorizes overriding the identified conflict.

- For .otl, list blocks and save a snapshot covering the blocks the change depends on. A replace operation clears and rebuilds the document, so it requires a whole-document comparison even if a narrower scope was recorded. Never use drive-level hash/version as an .otl concurrency check; that metadata can lag behind live block changes.
- For .docx, .xlsx, and .pptx, record the cloud file's SHA-1 hash and version before editing and pass both as --expect-hash and --expect-version when writing. Keep a local backup because the upload API does not provide a dependable rollback path.
- Never infer success from a timeout. A smart-document create request may have committed even if the response timed out. Fetch the document and inspect its blocks before considering a retry; otherwise duplicate content may be inserted.
- A successful write may not immediately appear in drive-level metadata or a downloaded binary. Check that the cloud version has advanced before using a download as verification.

The three reference files contain the detailed rules and tested API behavior. Read only the one needed for the current operation:

| Topic | Reference |
|---|---|
| Smart-document read/write, shared links, and content fidelity | {skillDir}/references/otl.md |
| Office file download/edit/upload and format limitations | {skillDir}/references/office-files.md |
| Snapshot, hash, conflict, and concurrency behavior | {skillDir}/references/concurrency.md |

## Edit and write a smart document

Before editing, export a local backup. List blocks when you need to identify document structure; create a full snapshot before a replace operation. Preview the Markdown conversion with --dry-run, then write with that expected snapshot. A replace always checks the whole document.

    "{python}" "{skillDir}/scripts/otl_fetch.py" --file-id <file_id> --out "{tmp}/otl-backup" --cli "{cli}"
    "{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --list-blocks --cli "{cli}"
    "{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --snapshot-out "{tmp}/snap.json" --cli "{cli}"
    "{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --md "{tmp}/updated.md" --mode replace --dry-run --cli "{cli}"
    "{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --md "{tmp}/updated.md" --mode replace --expect-snapshot "{tmp}/snap.json" --cli "{cli}"

For append or prepend, use that mode only when it matches the user's request and include a snapshot of any blocks the change depends on. Do not handcraft block JSON; the Markdown conversion endpoint handles supported headings, tables, emphasis, code, and lists. If the snapshot check reports a conflict, stop and reconcile with the latest document. Use --force only after the user explicitly authorizes overriding that conflict.

## Edit an Office file

The reliable flow is: fetch the original and save a local backup, record the cloud hash and version, use an available documents, spreadsheets, or presentations skill to edit that file, and upload it with both expected values. Keep the fetched original until the upload is complete. Recalculate spreadsheets with the available spreadsheet capability when formulas need recalculation; do not assume this environment includes the DSH LibreOffice/Node runtime. xlsx_edit.py is an optional compatibility helper, not a required Codex capability. Use it only when its explicit --node or --lo-cli dependency is available, or for a local --dry-run before an Office skill performs recalculation.

    "{python}" "{skillDir}/scripts/file_write.py" --drive-id <drive_id> --file-id <file_id> --print-state --cli "{cli}"
    "{python}" "{skillDir}/scripts/file_fetch.py" --file-id <file_id> --out "{tmp}/office-backup" --download --cli "{cli}"
    "{python}" "{skillDir}/scripts/file_write.py" --drive-id <drive_id> --file-id <file_id> --file "{tmp}/edited.xlsx" --expect-hash <sha1> --expect-version <version> --backup-dir "{tmp}/backup" --cli "{cli}"

For Windows, use the same arguments and paths with PowerShell's call operator, for example & "{python}" "{skillDir}\scripts\file_write.py" ... --cli "{cli}".

New .docx creation from Markdown has limited fidelity: headings, basic lists, tables, and bold text are supported, while images, nested lists, footnotes, full hyperlink styling, and some quote/code styling are not preserved as rich Word structures. For faithful Office edits, edit a fetched original with the appropriate Office skill and upload the complete file.

## Safety and verification

Do not write to the cloud to validate the setup. A fetch is a real read; otl_write.py --dry-run only previews conversion, and file_write.py --dry-run only requests an upload authorization without sending file bytes or committing a file. Use these dry runs only when the user asks for that validation. Report exactly which local or cloud steps actually completed, and distinguish command output from a read-back of the saved cloud document.

# Office files and online spreadsheets

## Ordinary Office files: download, edit, upload

Record a hash/version baseline before fetching and editing; see
[concurrency.md](concurrency.md). Download the original, keep it as a backup, and
edit a separate local copy with any suitable available library or Office tool.
No particular agent skill or document editor is required.

```sh
"{python}" "{skillDir}/scripts/file_fetch.py" --file-id <file_id> --out "{tmp}/original" --download --cli "{cli}"
"{python}" "{skillDir}/scripts/file_write.py" --drive-id <drive_id> --file-id <file_id> --file "{tmp}/edited.xlsx" --expect-hash <sha1> --expect-version <version> --backup-dir "{tmp}/backup" --cli "{cli}"
```

- DOCX extraction produces `doc.md`; the helper requests `include_elements=all`
  because other content modes can silently omit tables.
- XLSX extraction uses local `openpyxl` to preserve formula text, precise values,
  merged ranges, and worksheet structure in the extracted view. `--csv` also writes
  worksheet CSVs. `--via-api` reads cell values but cannot recover formula text.
- PPTX and other formats may only produce the original download and metadata;
  use an available local parser to inspect their content.
- Content fidelity depends on the local editor. Preserve formulas, styles,
  drawings, and workbook structure; do not promise that any library is lossless.
- `openpyxl` saves can clear cached formula results. When cached results matter,
  recalculate with an available spreadsheet engine and verify both formulas and
  computed values. Do not claim recalculation occurred if no engine is installed.
- `file_write.py --md` creates basic DOCX, not a fully faithful Markdown rendering:
  images, hyperlinks, nested lists, footnotes, and rich code/quote styles can lose
  fidelity. Edit the original for faithful changes. Inspect existing styles before
  using names such as `List Bullet`; minimal documents may not define them.

The writer performs request-upload, byte upload, and commit-upload with the same
delegated identity. It calculates MD5/SHA-256 itself: upload requests reject SHA-1,
even though download metadata uses SHA-1. Identical-byte uploads may deduplicate
without creating a version. Keep local backups; historical-version rollback may
not be available in a particular tenant. A dry run requests upload authorization
but does not send bytes or commit content; it is not an offline-only check.

## Live spreadsheets: do not overwrite bytes

An online spreadsheet, including `.ksheet` or an XLSX with a server-side content
model, can accept uploaded bytes and advance its version without adopting those
bytes as live cells. Use the Sheets API for live-cell work. Downloading a `.ksheet`
with `file_fetch.py` does not extract its cells.

After authentication, use relative API paths with the delegated identity:

```sh
"{cli}" api get /v7/sheets/<file_id>/worksheets --token-type delegated -o json
```

Read target cells using
`GET /v7/sheets/{file_id}/worksheets/{sheet_id}/range_data` with the range query
required by the installed CLI/API schema. Prefer `original_cell_value` over
`cell_text`, which can round numbers or otherwise format away precision. Do not
claim to have read formula source from a value-only response.

For writes, inspect the installed CLI help/API schema for the exact range payload:

- Endpoint: `POST /v7/sheets/{file_id}/worksheets/{sheet_id}/range_data/batch_update`.
- The update items are under `sheets_update_range_data`. Use explicit row/column
  bounds from the schema; verify its coordinate convention before sending.
- Supported cell content is `formula`, with
  `op_type=cell_operation_type_formula`. Do not send invented `value`, `cell_text`,
  or `original_cell_value` write fields.
- A numeric value can be expressed as `=999`; literal text as `="text"`.
  Escape embedded double quotes using spreadsheet string-literal rules as well
  as correctly encoding the enclosing JSON and shell arguments.

Limit the operation to the exact authorized worksheet, row identity, and fields.
Read and back up the affected range, then re-read and compare immediately before
writing. Do not infer a baseline from a stale downloaded workbook. If the schema
cannot be established, stop instead of guessing the request or uploading bytes.

Read the same live range after writing and compare the resulting values. Report
mismatches as failures even if the API returned success. Binary downloads can lag
behind API changes; waiting for an exported file is not a substitute for live-cell
verification. Direct requests need the recovery steps in
[authentication.md](authentication.md), unlike wrapped document helpers.

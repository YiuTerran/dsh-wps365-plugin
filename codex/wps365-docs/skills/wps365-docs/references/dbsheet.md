# Multi-dimensional tables (.dbt)

The third route in `wps365-docs`: **a .dbt is a database, not a document and not a folder**. Read and write records through the dbsheet API, wrapped by `{skillDir}/scripts/dbsheet.py`. Structural changes (create/delete sheets and fields) are not wrapped; use `{cli} dbsheet ...` directly.

| Item | Read | Write |
|---|---|---|
| `.otl` smart document | `otl_fetch.py` | `otl_write.py` (block level) |
| `.docx` / `.xlsx` / `.pptx` | `file_fetch.py` | `file_write.py` (whole-file upload) |
| `.dbt` multi-dimensional table | `dbsheet.py schema/list/get` | `dbsheet.py create/update/delete` |

## 15 条硬约束（按实测，v0.3.6）

1. **A .dbt is not a folder, and its body cannot be extracted.** `drive file list <drive> <file_id>` returns nothing; `drive file-content get` fails with `400008018 文档内容抽取失败`. Read the structure only through `GET /v7/coop/dbsheet/{file_id}/schema`.
2. **`record list` defaults to `page_size=1`.** Without an explicit page size it returns one record. The shipped script always sends 100 and follows `page_token`; hand-written requests must paginate too. Do not report "only one row" from a single unpaginated call.
3. **`fields_value` is a JSON string.** In create/update bodies, `records[].fields_value` must be a string such as `"{\"field-id\":...}"`, not an object. The `fields` field in responses is also a string; parse it with `json.loads`.
4. **With `prefer_id=true`, fields are IDs, but Link filters use display names.** Record keys and `filter.criteria[].field` are field IDs. For a Link field, `values` must be the target record's display name; a record ID there returns an empty result.
5. **`text_value=text` and raw are different views.** Text mode gives SingleSelect names, comma-joined MultipleSelect, `"42.00"` for Number, `"1"` for Checkbox, `"陈澍,芦宇轩"` for Link, and only the filename for Attachment. **Attachment `uploadId` exists only in raw mode.** Use raw records for writes and attachment downloads.
6. **A Form view does not carry a record list.** Form views report `records_count=0`; `list --view <form>` returns nothing. Read the underlying table. Form field order and hidden flags live in `views[].fields_attribute` as `{field, hidden}`.
7. **FlexPaper sheets cannot be read.** A sheet with `sheet_type=xlEtFlexPaperSheet` has a `content_id` but no public content API; it has no fields and no records. Say it is unreadable instead of inventing a body.
8. **Attachment downloads need `uploadId` plus an endpoint call.** Use `GET /v7/coop/dbsheet/{file_id}/attachments/{uploadId}?type=<mime-or-ext>`; the URL is `data.attachment.value.download_url` and the size is `value.file_size`. Do not build a URL from `fileName`.
9. **Create is not idempotent, and a batch is all-or-nothing.** `records/create` may have committed even when the response times out; **do not retry blindly**. Search/list for the record first. If any record in a batch fails, the whole batch is rejected.
10. **There is no per-record optimistic lock.** The API exposes no record version/etag. Concurrency safety is: `snapshot` a baseline, write with `--expect-snapshot`, then read back. `record update` is partial (only supplied fields change); deleting must compare every field.
11. **These fields are not writable:** Attachment, Formula, Lookup, AutoNumber, CreatedTime/By, LastModifiedTime/By, OneWayLink, Contact. Cascade and Location are structurally complex and are rejected unless the caller knows the exact shape and uses `--set-json`.
12. **Link writes use record IDs, not display names.** Writing `["陈澍"]` does not error; it **silently clears** the field. The script maps display names to the target table's primary-field record IDs, writes, and reads back; ambiguous or missing names fail. Filters use the opposite convention: display names.
13. **Validate Date values first.** Accept `YYYY/MM/DD` and `YYYY-MM-DD`, optionally with `HH:MM[:SS]`; an invalid value returns `400000003 ... field: xx`. A date-only field drops the time part; compare after normalizing to the field format.
14. **At most 1024 records per request, about 10 requests per second.** The script caps a batch at 100 and paginates reads. Large writes must be split and paced; do not hammer the endpoint concurrently.
15. **Attachments are download-only.** There is no public upload endpoint, and an Attachment field cannot be written with `--set` or `--set-json`. Add attachments in the WPS UI; the script only fetches existing files.

## Read: schema first, then records

    "{python}" "{skillDir}/scripts/dbsheet.py" schema --file-id <file_id> --out "{tmp}/dbt_schema.json" --cli "{cli}"
    "{python}" "{skillDir}/scripts/dbsheet.py" list --file-id <file_id> --sheet 12 --view "周报明细" --fields "汇报日期,姓名,组别" --max-records 20 --cli "{cli}"
    "{python}" "{skillDir}/scripts/dbsheet.py" get --file-id <file_id> --sheet 12 --record <record_id> --cli "{cli}"

- `schema` prints each sheet's type, record count, primary field, views, fields, select options, and Link targets. `--out` stores the full JSON. **Check `sheet_type` first**; give up on FlexPaper record reads.
- `list` reads 50 records by default and 100 per page. `--max-records 0` reads all. Use `--format md|cards|json|jsonl`; `--raw` keeps raw values (Link as IDs).
- A filter looks like `--filter '{"mode":"And","criteria":[{"field":"项目名称","operator":"Intersected","values":["引隆补水工程信息化项目"]}]}'`. Field names may be used; the script converts them to IDs. Link `values` must be display names. `--filter-file` reads a file; `-` reads stdin.
- A Form view prints a warning and automatically reads the underlying table. The form order is in the schema output under "form ... visible fields".
- Long text is truncated by default. Use `--no-truncate` or `--format json --out` for full values.

## Write: snapshot, update, read back

    # 1. Save a baseline (record IDs or a filter)
    "{python}" "{skillDir}/scripts/dbsheet.py" snapshot --file-id <file_id> --sheet 12 --record <record_id> --out "{tmp}/snap.json" --cli "{cli}"

    # 2. Partial update: compare only the fields being written
    "{python}" "{skillDir}/scripts/dbsheet.py" update --file-id <file_id> --sheet 12 --record <record_id> --set "备注=已电话确认" --expect-snapshot "{tmp}/snap.json" --cli "{cli}"

    # 3. Delete: compare every field
    "{python}" "{skillDir}/scripts/dbsheet.py" delete --file-id <file_id> --sheet 12 --record <record_id> --expect-snapshot "{tmp}/snap.json" --cli "{cli}"

- `create` takes repeatable `--set "field=value"`; for a batch use `--records-file records.json` (an array whose items contain `fields`). Add `--check-duplicate "姓名,汇报日期"` to search before creating; a hit aborts unless `--allow-duplicate` is passed. **Create is not idempotent**: after a timeout, search before retrying.
- A failed duplicate query stops creation even with `--allow-duplicate`. Link duplicate checks convert record IDs to display names before filtering. Batch update files must contain unique record IDs; duplicates are rejected.
- `update` and `delete` refuse to run without `--expect-snapshot`. `--force` is only for an explicitly authorized overwrite; it skips the conflict check, not the read-back check.
- After writing, the script reads the records by ID and compares each field. Link/MultipleSelect compare as sets, Number numerically, Date after format normalization, Checkbox as booleans. A mismatch exits 1 and prints the written value beside the read-back value.
- Date comparisons retain seconds when the field format includes them; only minute-precision fields ignore seconds. Checkbox text values `"0"`/`"false"` mean unchecked rather than truthy nonempty strings.
- `--set-json '状态=""'` is the escape hatch: the value is sent verbatim, useful for clearing a select or a known complex shape. Prefer `--set`, which validates types and resolves Links.
- `--dry-run` prints the request body without sending it; create/update dry runs still validate fields and resolve Links.

## Field write rules

| Type | Value |
|---|---|
| Text / Phone / Email / URL | string |
| Number / Currency / Percentage / Rating | number; `Percentage` accepts `"12%"` (0.12); NaN/Infinity rejected |
| Checkbox | `true/false`, `1/0`, `是/否` |
| Date | `YYYY/MM/DD` or `YYYY-MM-DD`, optionally `HH:MM[:SS]` |
| SingleSelect | option **display name** (or option ID); an unknown option fails instead of silently clearing |
| MultipleSelect | array or comma string; every item is validated |
| Link | target primary-field **display name** (converted to a record ID) or a record ID; duplicates/misses fail |
| Attachment / Formula / Lookup / ... | read-only; rejected |
| Cascade / Location / unknown | rejected by default; use `--set-json` only when the exact shape is known |

Link `values` are display names in filters and record IDs in writes. These are two different conventions; do not swap them.

## Attachments and images

    # Metadata only, no download
    "{python}" "{skillDir}/scripts/dbsheet.py" attachments --file-id <file_id> --sheet 12 --record <record_id> --out "{tmp}/dbt_att" --list-only --cli "{cli}"

    # Images only; detect the real format from the bytes
    "{python}" "{skillDir}/scripts/dbsheet.py" attachments --file-id <file_id> --sheet 12 --filter '...' --out "{tmp}/dbt_att" --images-only --cli "{cli}"

- Attachment data must come from raw records. `--field "相关附件"` limits the field; `--images-only` filters to images.
- Filenames are stripped of paths and illegal characters. The same `uploadId` in the same record is downloaded once. After download the script reads the file header: the server may transcode a PNG to JPEG (observed: a `.png` name with JPEG bytes), so it renames to the real extension and prints a note. **Do not trust the filename suffix alone.**
- The printed `read_image("<path>")` hint is only usable when the current model accepts image input. If it does not, report only objective facts (size, dimensions, format); never pretend to have viewed an image.
- `--show-urls` prints signed download URLs; do not put them in public documents.

## Structural changes (outside the script)

    "{cli}" dbsheet schema get <file_id> -o json
    "{cli}" dbsheet sheet create <file_id> --name "新表" --fields-body '[...]' --views-body '[...]'
    "{cli}" dbsheet field create <file_id> <sheet_id> --fields-body '[...]'
    "{cli}" dbsheet field delete <file_id> <sheet_id> --fields "Dr"

Creating a Link field also creates a reverse-link field in the target table. Deleting the source sheet/field can leave that reverse field behind (observed: after deleting a scratch sheet, the target table still had `Dr __dbsheet_probe__`); remove it with `field delete`. Run `schema get` before and after structural changes.

## Common failures

| Symptom | Cause / action |
|---|---|
| `文档内容抽取失败` | Treated a .dbt as an ordinary file; use dbsheet schema/records |
| Only one row | No `page_size`; send 100 and paginate |
| Write succeeds but the field is empty | Invalid SingleSelect/MultipleSelect/Link value was silently cleared; validate, then read back |
| Link filter is empty | `values` used record IDs; use display names |
| Link write clears the field | Wrote display names; use record IDs (the script converts) |
| `field: xx` 400 | Invalid Date or similar; validate before sending |
| A Form view has no records | Form views do not carry records; read the underlying table |
| A FlexPaper sheet is unreadable | No public content API; tell the user |
| Update/delete refused | Snapshot differs from current state; re-snapshot, merge, and use `--force` only with explicit authorization |

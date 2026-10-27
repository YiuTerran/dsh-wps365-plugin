# Multi-dimensional tables (.dbt)

A `.dbt` is a database, not a folder or Office file. Use schema and record APIs;
body extraction and listing it as a drive folder do not retrieve its records.

## Read schema, then records

```sh
"{python}" "{skillDir}/scripts/dbsheet.py" schema --file-id <file_id> --out "{tmp}/schema.json" --cli "{cli}"
"{python}" "{skillDir}/scripts/dbsheet.py" list --file-id <file_id> --sheet <sheet_id> --max-records 50 --cli "{cli}"
"{python}" "{skillDir}/scripts/dbsheet.py" get --file-id <file_id> --sheet <sheet_id> --record <record_id> --cli "{cli}"
```

Inspect field IDs, types, select options, Link targets, and views first. Form views
do not carry record lists; use the underlying table. FlexPaper sheets do not expose
readable records/body through this route; report that limitation.

The helper pages with 100 records per request; raw CLI listing can default to one.
`--max-records 0` reads all pages. Never call a capped result complete. Use `--raw`
when you need typed values, Link IDs, or attachment metadata; text view may contain
formatted numbers or only attachment filenames. For long text use `--no-truncate`
or `--format json --out <path>`.

`--filter` or `--filter-file` can select records. Link filter values are display
names, not record IDs. Obtain current operators and field types from schema/help
rather than inventing a filter that silently returns nothing.

## Snapshot, mutate, read back

```sh
"{python}" "{skillDir}/scripts/dbsheet.py" snapshot --file-id <file_id> --sheet <sheet_id> --record <record_id> --out "{tmp}/record.json" --cli "{cli}"
"{python}" "{skillDir}/scripts/dbsheet.py" update --file-id <file_id> --sheet <sheet_id> --record <record_id> --set "Notes=Reviewed" --expect-snapshot "{tmp}/record.json" --cli "{cli}"
```

Repeat `--set` for multiple authorized fields. Updates are partial; unspecified
fields remain unchanged. Deletes also require `--expect-snapshot` and compare all
fields, not just those in an update. Schema changes invalidate snapshots. These
checks are client-side, not record-level server locks.

- Use normal `--set` validation for text, numbers, dates, checkboxes, and selects.
  Dates accept `YYYY/MM/DD` or `YYYY-MM-DD`, optionally time; read-back precision
  follows the field's format.
- Link writes require record IDs. The helper resolves display names to IDs and
  rejects missing or ambiguous targets. Link filtering has the opposite convention:
  it requires display names.
- Invalid Link/select values may be silently cleared by the server. The helper
  validates and reads back fields, exiting nonzero on a mismatch. Treat that as a
  failed write, not merely a warning.
- Attachment, Formula, Lookup, AutoNumber, identity/timestamp fields, and other
  computed or unsupported types are not normal writable fields. Cascade/Location
  require known API structures. `--set-json` is only for a deliberately understood
  raw representation, not a way around missing schema knowledge.

`create` accepts repeated `--set` or a `--records-file`. Use
`--check-duplicate "<field1>,<field2>"` when appropriate. Creates are not idempotent:
after timeout, list/search for the intended record before retrying. A create batch
is all-or-nothing; the helper caps batches at 100. Do not use concurrent bursts to
evade limits. `--dry-run` skips mutation but may still read schema and resolve Links.

For direct API work, `records[].fields_value` is a JSON-encoded string, not an
object; response record fields may also need JSON decoding. Prefer the helper's
typed interface to manual payload construction.

## Attachments

```sh
"{python}" "{skillDir}/scripts/dbsheet.py" attachments --file-id <file_id> --sheet <sheet_id> --record <record_id> --out "{tmp}/attachments" --list-only --cli "{cli}"
"{python}" "{skillDir}/scripts/dbsheet.py" attachments --file-id <file_id> --sheet <sheet_id> --record <record_id> --out "{tmp}/attachments" --images-only --cli "{cli}"
```

The helper resolves raw attachment `uploadId` to a download URL and corrects the
extension from the actual file header; the server can transcode images. Filenames
alone cannot provide download URLs. Do not expose signed URLs in a public answer.
Attachment fields have no supported upload path through this helper.

Schema edits are separate CLI operations, not record updates. Only perform them
when requested, back up the schema, and read it again afterward. Creating Link
fields can also create reverse fields in a target table; account for those effects
before changing or deleting structure.

# Backups, conflicts, and verification

Establish the baseline before preparing edits, then compare again immediately
before mutation. Preserve the baseline and original content in `{tmp}`. If the
cloud changed within the operation's dependency scope, stop rather than silently
overwriting another editor's work.

## Smart documents

Use live block snapshots, never drive-level hash/version: drive metadata can lag
behind real-time block changes. The snapshot helper supports `--scope-index` and
`--scope-blocks` to declare dependencies. A whole-document replacement always
checks the entire document, even with a scoped snapshot.

```sh
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --snapshot-out "{tmp}/blocks.json" --cli "{cli}"
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --md "{tmp}/updated.md" --mode replace --expect-snapshot "{tmp}/blocks.json" --cli "{cli}"
```

Do not use replace for a narrow edit. For a direct block update, save and compare
the target block and any positional dependencies yourself; the CLI does not
automatically apply the helper's snapshot check to a direct API call.

Block create indexes are insertion positions, not stable append anchors.
Concurrent insertion can reorder content. Range deletion uses indexes and can
delete different blocks after concurrent changes. Do not enable `--allow-added`
without considering ordering dependencies. Replace is delete followed by create,
not an atomic transaction; a partial failure may leave the document empty.

## Ordinary Office files

Record SHA-1 hash and version using `file_write.py --print-state`, before fetching
the original. Fetch the file and check that its state still matches the baseline
before editing. Pass both expected values when uploading:

```sh
"{python}" "{skillDir}/scripts/file_write.py" --drive-id <drive_id> --file-id <file_id> --print-state --cli "{cli}"
"{python}" "{skillDir}/scripts/file_write.py" --drive-id <drive_id> --file-id <file_id> --file "{tmp}/edited.docx" --expect-hash <sha1> --expect-version <version> --backup-dir "{tmp}/backup" --cli "{cli}"
```

Keep the downloaded original too. A hash change from reserialization may be a
conservative conflict, but is not a reason to bypass the check. Upload hashing
uses MD5/SHA-256 internally; do not confuse that with the SHA-1 baseline.

## Multi-dimensional tables

Save a record snapshot before preparing an update or deletion. Pass it as
`--expect-snapshot`. Updates compare the fields being changed; deletes compare all
fields. A schema change invalidates the snapshot. The helper reads back writes and
fails on mismatches. Do not bypass that failure with `--force`.

## Online spreadsheets and remaining races

For direct cell writes, save the exact affected range, worksheet identity, current
values, and formulas where available. Re-read and compare immediately before
writing. Check row identity as well as coordinates if rows can move. Stop if you
cannot establish a reliable baseline; do not replace the workbook as a fallback.

These are client-side preflight comparisons, not atomic conditional writes or
collaborative merge operations. Another editor can still change content between
the comparison and mutation. Minimize that interval and report this limitation
when strict concurrent guarantees matter.

Read live blocks, records, or cells after mutation. Their exported bytes and drive
metadata may update later. Use bounded polling if verifying a later binary export;
never report live-content success from metadata changes alone.

# Smart documents (.otl / AirPage)

## Read

```sh
"{python}" "{skillDir}/scripts/otl_fetch.py" --file-id <file_id> --out "{tmp}/document" --cli "{cli}"
```

The helper exports JSON, decodes it locally, reconstructs `doc.md`, and downloads
attachments to `img/`. It prints an inventory rather than the full export.
`otl.model.json` retains the raw decoded model for precise inspection. Read text
first, then only the images needed for the task. `--no-images` omits downloads.

The JSON export's attachment list supplies download URLs and source keys, including
images in tables. A block-tree read alone does not provide those image URLs.
The export model is base64 encoded on the wire; do not decode it in model context
or assume attachment hashes describe the downloaded bytes. Some exports transcode
images. Markdown reconstruction is not a lossless source round trip.

## Append, prepend, or replace

Export a backup and save a snapshot before editing, following
[concurrency.md](concurrency.md). Choose a mode matching the requested scope:

```sh
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --list-blocks --cli "{cli}"
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --snapshot-out "{tmp}/snapshot.json" --cli "{cli}"
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --md "{tmp}/addition.md" --mode append --dry-run --cli "{cli}"
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <file_id> --md "{tmp}/addition.md" --mode append --expect-snapshot "{tmp}/snapshot.json" --cli "{cli}"
```

`--dry-run` calls the cloud Markdown converter but does not mutate document
content; it still needs authentication. The converter handles headings, tables,
emphasis, lists, and code. Do not handcraft create-block JSON unnecessarily.
Unsupported code-language identifiers may be downgraded to plain text. The helper
batches large writes because one create request accepts at most 1000 blocks.

Replace clears the existing body before inserting the converted content. Use it
only for an explicitly intended whole-document replacement, with a full snapshot
and backup. After timeout, fetch the current blocks before any retry: the server
may already have inserted content or completed the deletion.

## Targeted block edits

For one paragraph or another narrow edit, inspect the current block tree and use
the CLI's current API schema/help for `blocks/update` under
`/v7/airpage/v2/{file_id}/blocks/`. The bundled writer does not implement targeted
updates; do not substitute whole-document replacement.

- Block-tree responses are keyed by block ID: root children are in
  `data.block["doc"].children`, not `data.block.children`.
- Update content uses an object named `update_elements`, not `elements` or an
  array. A paragraph's content shape is
  `{"update_elements":{"paragraph_elements":{"elements":[...]}}}`; obtain the
  rest of the request from the current schema instead of guessing fields.
- Save and compare the target block and necessary ordering dependencies before
  the direct mutation, then re-read the changed block. Direct API calls do not
  inherit snapshot checks from a previous helper invocation.

Use live block reads for verification. Drive-level version/hash can lag behind
changes and is unsuitable for detecting smart-document conflicts.

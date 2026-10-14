#!/usr/bin/env node
/**
 * Delete Python bytecode from the shipped assets before packing.
 *
 * Running any script in `assets/wps365-docs/scripts/` leaves a `__pycache__/`
 * directory beside it. `package.json.files` lists `assets` as a whole, so those
 * `.pyc` files would otherwise be published — they did once, 52 KiB of them.
 * `.gitignore` keeps them out of the repository but says nothing about the
 * tarball, so this runs from `prepack` (which `npm publish` and even
 * `npm pack --dry-run` invoke) and `scripts/check-package.mjs` still fails the
 * build if bytecode ever reaches the file list by another route.
 *
 * Cross-platform by construction: it walks with `node:fs` rather than shelling
 * out to `find`.
 */
import { readdirSync, rmSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../assets/', import.meta.url))
const removed = []

function sweep(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = `${directory.replace(/[/\\]+$/u, '')}/${entry.name}`
    if (entry.isDirectory()) {
      if (entry.name === '__pycache__') {
        rmSync(path, { recursive: true, force: true })
        removed.push(path)
        continue
      }
      sweep(path)
      continue
    }
    if (entry.isFile() && /\.py[co]$/u.test(entry.name)) {
      rmSync(path, { force: true })
      removed.push(path)
    }
  }
}

try {
  statSync(root)
} catch {
  console.error('no assets directory; nothing to clean')
  process.exit(0)
}

sweep(root)
// stderr on purpose: `npm pack --json` captures stdout, so anything printed here
// would be prepended to the JSON that scripts/check-package.mjs parses.
console.error(removed.length === 0 ? 'no Python bytecode to remove' : `removed ${removed.length} bytecode path(s)`)
for (const path of removed) console.error(`  - ${path.slice(root.length)}`)

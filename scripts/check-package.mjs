#!/usr/bin/env node
/**
 * Assert that `npm pack` produces a complete, clean bundle tarball.
 *
 * `package.json.files` is a whitelist, so a directory that exists in the
 * checkout can still be missing from the published package — and an installed
 * bundle with no `assets/` or no `cordis.patch.yml` fails silently at load time
 * rather than at publish time. This check is the guard for that failure mode.
 *
 * Run with `npm run check:pack` (CI runs it too). Exits non-zero and prints the
 * offending paths when the tarball drifts.
 */
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))

/** Paths an installed bundle needs; dropping any of them breaks real users. */
const REQUIRED = [
  'package.json',
  'index.js',
  'cordis.patch.yml',
  'icon.svg',
  'LICENSE',
  'CHANGELOG.md',
  'README.md',
  'README.en.md',
  'src/plugin-config.js',
  'src/skill-catalog.js',
  'src/cli-locate.js',
  'assets/wps365-docs/SKILL.md',
  'assets/wps365-docs/references/otl.md',
  'assets/wps365-docs/references/office-files.md',
  'assets/wps365-docs/references/dbsheet.md',
  'assets/wps365-docs/references/concurrency.md',
  'assets/wps365-docs/scripts/_wps_cli.py',
  'assets/wps365-docs/scripts/otl_fetch.py',
  'assets/wps365-docs/scripts/otl_write.py',
  'assets/wps365-docs/scripts/file_fetch.py',
  'assets/wps365-docs/scripts/file_write.py',
  'assets/wps365-docs/scripts/xlsx_edit.py',
  'assets/wps365-docs/scripts/dbsheet.py',
  'locale/en.json',
  'locale/zh.json',
]

/** Repository-only paths that must not reach the registry. */
const FORBIDDEN = [
  'test/',
  '.github/',
  '.gitignore',
  'RELEASING.md',
  'node_modules/',
  // Running the shipped scripts leaves Python bytecode behind; it once shipped
  // in the tarball because `files` includes the whole assets/ directory.
  '__pycache__/',
  '.DS_Store',
]

/** Path suffixes that must not appear anywhere in the tarball. */
const FORBIDDEN_SUFFIXES = ['.pyc', '.pyo', '.log', '.tgz']

const raw = execFileSync('npm', ['pack', '--dry-run', '--json'], { cwd: root, encoding: 'utf8' })
// `npm pack` runs `prepack` first, and any lifecycle output shares stdout with
// the JSON payload; start at the array rather than assuming stdout is pure JSON.
const jsonStart = raw.indexOf('[')
const [report] = JSON.parse(jsonStart === -1 ? raw : raw.slice(jsonStart))
const paths = report.files.map((file) => file.path)

const missing = REQUIRED.filter((path) => !paths.includes(path))
const leaked = paths.filter(
  (path) => FORBIDDEN.some((bad) => path.startsWith(bad)) || FORBIDDEN_SUFFIXES.some((bad) => path.endsWith(bad)),
)

if (missing.length > 0) {
  console.error(`tarball is missing ${missing.length} required path(s):`)
  for (const path of missing) console.error(`  - ${path}`)
}
if (leaked.length > 0) {
  console.error(`tarball must not ship ${leaked.length} repository-only path(s):`)
  for (const path of leaked) console.error(`  - ${path}`)
}
if (missing.length > 0 || leaked.length > 0) {
  console.error('\nFix package.json "files" (and re-check that the files exist).')
  process.exit(1)
}

const bytes = report.files.reduce((total, file) => total + (file.size ?? 0), 0)
console.log(`tarball OK: ${paths.length} files, ${(bytes / 1024).toFixed(1)} KiB, all ${REQUIRED.length} required paths present`)

#!/usr/bin/env node
import { copyFileSync, mkdirSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const source = new URL('../assets/wps365-docs/scripts/', import.meta.url)
const target = new URL('../general-skill/wps365-docs/scripts/', import.meta.url)
// Keep the portable helpers identical to the canonical implementation. The XLSX
// convenience wrapper is omitted because its recalculation discovery is host-specific.
const names = [
  '_wps_cli.py', 'auth.py', 'otl_fetch.py', 'otl_write.py',
  'file_fetch.py', 'file_write.py', 'dbsheet.py',
]
const check = process.argv.includes('--check')

if (!check) mkdirSync(target, { recursive: true })
for (const name of names) {
  const from = new URL(name, source)
  const to = new URL(name, target)
  if (check) {
    if (!readFileSync(from).equals(readFileSync(to))) {
      throw new Error(`Portable helper is stale: ${name}; run node scripts/sync-general-skill.mjs`)
    }
  } else {
    copyFileSync(from, to)
  }
}
console.log(`${check ? 'Checked' : 'Synced'} ${names.length} portable helpers: ${fileURLToPath(target)}`)

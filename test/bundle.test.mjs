/**
 * Bundle-manifest tests: the patch dialect, the row that mounts the plugin, and
 * the display metadata the Plugin Manager reads without activating anything.
 *
 * The patch is checked structurally with regexes so this runs with a plain
 * `node --test`; when the Host's `yaml` package happens to be resolvable (a DSH
 * source checkout), the same patch is parsed for real as well.
 */
import assert from 'node:assert/strict'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

const ROOT = new URL('../', import.meta.url)
const read = (relative) => readFileSync(new URL(relative, ROOT), 'utf8')

const manifest = JSON.parse(read('package.json'))
const patch = read(manifest.dsh.bundle.patch)

test('the manifest declares an installable Host-only bundle', () => {
  assert.equal(manifest.type, 'module')
  assert.equal(manifest.dsh.bundle.patch, './cordis.patch.yml')
  assert.equal(manifest.private, true, 'keep the publish guard on')
  assert.equal(manifest.dependencies, undefined)
  assert.equal(manifest.peerDependencies, undefined)
  assert.equal(manifest.main, './index.js')
})

test('every path listed in `files` exists', () => {
  for (const entry of manifest.files.filter((value) => !value.includes('*'))) {
    assert.ok(existsSync(new URL(entry, ROOT)), `${entry} is missing`)
  }
  assert.ok(manifest.files.includes('assets'), 'the skill assets must ship')
})

test('the patch inserts exactly one row, anchored beside the patch file', () => {
  const rows = [...patch.matchAll(/^\s*-\s*id:\s*(\S+)\s*$/gmu)].map((match) => match[1])
  assert.deepEqual(rows, ['skill-wps365-docs'])
  const names = [...patch.matchAll(/^\s*name:\s*(.+?)\s*$/gmu)].map((match) => match[1])
  assert.deepEqual(names, ["'./index.js'"])
  assert.ok(statSync(new URL('index.js', ROOT)).isFile())
  assert.ok(patch.includes('- insert:'), 'the row must be inserted, not used to target an existing row')
})

test('the patch carries no commented-out or active config override', () => {
  const active = patch
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('#'))
    .join('\n')
  assert.ok(!active.includes('config:'), 'defaults must be usable without a config block')
  assert.ok(!active.includes('disabled:'), 'the bundle must mount its row by default')
})

test('display metadata is complete for every shipped locale', () => {
  for (const locale of ['en', 'zh']) {
    const data = JSON.parse(read(`locale/${locale}.json`))
    assert.equal(typeof data.meta?.title, 'string')
    assert.ok(data.meta.title.length > 0)
    assert.equal(typeof data.meta?.description, 'string')
    assert.ok(data.meta.description.length > 0)
  }
  assert.ok(manifest.icon === './icon.svg')
})

test('the icon fits the Plugin Manager budget', () => {
  const icon = read('icon.svg')
  assert.ok(Buffer.byteLength(icon) <= 256 * 1024)
  assert.ok(icon.trimStart().startsWith('<svg'))
})

test('the patch parses as real YAML when the Host package is resolvable', async (t) => {
  let parse
  try {
    ;({ parse } = await import('yaml'))
  } catch {
    t.skip('the yaml package is not resolvable outside a DSH install')
    return
  }
  const document = parse(patch)
  assert.ok(Array.isArray(document))
  assert.deepEqual(document[0].insert, [{ id: 'skill-wps365-docs', name: './index.js' }])
})

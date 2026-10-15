import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative, sep } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { buildCodexPlugin } from '../build.mjs'

const CODEX_DIR = dirname(dirname(fileURLToPath(import.meta.url)))
const REPO_DIR = dirname(CODEX_DIR)
const SOURCE_DIR = join(CODEX_DIR, 'wps365-docs')
const SCRIPT_SOURCE_DIR = join(REPO_DIR, 'assets', 'wps365-docs', 'scripts')
const SCRIPT_NAMES = [
  '_wps_cli.py',
  'file_fetch.py',
  'file_write.py',
  'otl_fetch.py',
  'otl_write.py',
  'xlsx_edit.py',
]
const FORBIDDEN_PARTS = new Set(['node_modules', '__pycache__', '.git', '.venv', 'venv'])
const FORBIDDEN_FILE = /^(?:\.env(?:\..*)?|\.npmrc|\.pypirc|credentials?(?:[._-].*)?|secrets?(?:[._-].*)?|.*\.(?:pem|key|p12|pfx|token))$/i

function hash(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function json(path) {
  return JSON.parse(readFileSync(path, 'utf8'))
}

function filesBelow(root) {
  const files = []
  const visit = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      if (entry.isDirectory()) visit(path)
      else files.push(relative(root, path).split(sep).join('/'))
    }
  }
  visit(root)
  return files.sort()
}

function assertPortableTree(root) {
  const visit = (dir, names = []) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name)
      const segments = [...names, entry.name]
      assert.equal(entry.isSymbolicLink(), false, `unexpected symlink: ${segments.join('/')}`)
      assert.equal(FORBIDDEN_PARTS.has(entry.name), false, `unexpected dependency/cache path: ${segments.join('/')}`)
      assert.equal(FORBIDDEN_FILE.test(entry.name), false, `unexpected secret/dependency file: ${segments.join('/')}`)
      assert.equal(/\.(?:pyc|pyo|log)$/i.test(entry.name), false, `unexpected generated file: ${segments.join('/')}`)
      if (entry.isDirectory()) visit(path, segments)
    }
  }
  visit(root)
}

function assertManifestShape(root) {
  const portable = json(join(root, 'plugin.json'))
  const legacy = json(join(root, '.codex-plugin', 'plugin.json'))
  const rootVersion = json(join(REPO_DIR, 'package.json')).version
  assert.equal(portable.name, 'wps365-docs')
  assert.equal(legacy.name, portable.name)
  assert.equal(portable.version, rootVersion)
  assert.equal(legacy.version, portable.version)
  assert.equal(portable.extensions?.['com.openai']?.requires_local_executor, true)
  assert.equal(legacy.requires_local_executor, true)
  assert.equal(legacy.skills, './skills/')
  for (const key of ['skills', 'apps', 'mcpServers', 'interface']) assert.equal(key in portable, false, `portable manifest has top-level ${key}`)

  const presentation = portable.extensions['com.openai'].interface
  assert.deepEqual(legacy.interface, presentation)
  assert.equal(typeof presentation.displayName, 'string')
  assert.ok(presentation.displayName.trim().length > 0)
  assert.equal(typeof presentation.shortDescription, 'string')
  assert.ok(presentation.shortDescription.trim().length > 0)
  assert.ok([...presentation.shortDescription].length <= 30, 'shortDescription must use at most 30 Unicode code points')
  assert.equal(presentation.logo, './assets/icon.svg')
  assert.equal(presentation.composerIcon, './assets/icon.svg')
}

function archiveFiles(archivePath) {
  const names = execFileSync('tar', ['-tzf', archivePath], { encoding: 'utf8' })
    .trimEnd()
    .split('\n')
    .filter(Boolean)
  for (const name of names) {
    assert.ok(name === 'wps365-docs/' || name.startsWith('wps365-docs/'), `archive entry escaped plugin folder: ${name}`)
    assert.equal(name.split('/').includes('..'), false, `archive entry contains parent traversal: ${name}`)
  }
  return names.filter((name) => !name.endsWith('/')).map((name) => name.slice('wps365-docs/'.length)).sort()
}

test('builds portable plugin contents and a matching tar.gz archive', (t) => {
  const temporaryRoot = mkdtempSync(join(tmpdir(), 'wps365-docs-plugin-'))
  t.after(() => rmSync(temporaryRoot, { recursive: true, force: true }))

  const outputDir = join(temporaryRoot, 'wps365-docs')
  const { archivePath } = buildCodexPlugin({ outputDir, archive: true })
  assert.ok(archivePath)
  assertManifestShape(outputDir)
  assertPortableTree(outputDir)

  const skillSource = join(SOURCE_DIR, 'skills', 'wps365-docs', 'SKILL.md')
  const skillOutput = join(outputDir, 'skills', 'wps365-docs', 'SKILL.md')
  assert.ok(statSync(join(outputDir, 'skills', 'wps365-docs', 'agents', 'openai.yaml')).isFile())
  const skillText = readFileSync(skillOutput, 'utf8')
  const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(skillText)
  assert.ok(frontmatter, 'skill must have YAML frontmatter')
  assert.match(frontmatter[1], /^name:\s*["']?wps365-docs["']?\s*$/m, 'frontmatter name must match the plugin skill directory')
  assert.deepEqual(readFileSync(skillOutput), readFileSync(skillSource))

  const sourceReferencesDir = join(SOURCE_DIR, 'skills', 'wps365-docs', 'references')
  const outputReferencesDir = join(outputDir, 'skills', 'wps365-docs', 'references')
  const referenceNames = readdirSync(sourceReferencesDir).filter((name) => name.endsWith('.md')).sort()
  assert.ok(referenceNames.length > 0)
  assert.deepEqual(readdirSync(outputReferencesDir).sort(), referenceNames)
  for (const name of referenceNames) {
    assert.deepEqual(readFileSync(join(outputReferencesDir, name)), readFileSync(join(sourceReferencesDir, name)))
  }

  for (const name of SCRIPT_NAMES) {
    assert.equal(hash(join(outputDir, 'skills', 'wps365-docs', 'scripts', name)), hash(join(SCRIPT_SOURCE_DIR, name)), `${name} must match canonical source exactly`)
  }
  assert.deepEqual(readFileSync(join(outputDir, 'assets', 'icon.svg')), readFileSync(join(REPO_DIR, 'icon.svg')))
  assert.deepEqual(readFileSync(join(outputDir, 'LICENSE')), readFileSync(join(REPO_DIR, 'LICENSE')))

  const outputFiles = filesBelow(outputDir)
  assert.deepEqual(archiveFiles(archivePath), outputFiles, 'archive inventory must match the build directory exactly')

  const extractionDir = join(temporaryRoot, 'extracted')
  mkdirSync(extractionDir)
  execFileSync('tar', ['-xzf', archivePath, '-C', extractionDir], { stdio: 'pipe' })
  const extractedRoot = join(extractionDir, 'wps365-docs')
  assert.ok(statSync(extractedRoot).isDirectory())
  assert.deepEqual(filesBelow(extractedRoot), outputFiles, 'archive must extract to the same inventory')
  for (const path of outputFiles) assert.deepEqual(readFileSync(join(extractedRoot, path)), readFileSync(join(outputDir, path)))
})

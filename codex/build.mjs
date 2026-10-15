#!/usr/bin/env node
import { execFileSync } from 'node:child_process'
import {
  cpSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  rmSync,
  statSync,
} from 'node:fs'
import { createHash } from 'node:crypto'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import { fileURLToPath, pathToFileURL } from 'node:url'

const CODEx_DIR = dirname(fileURLToPath(import.meta.url))
const REPO_DIR = resolve(CODEx_DIR, '..')
const ROOT_PACKAGE_VERSION = readJson(join(REPO_DIR, 'package.json'), 'root package.json').version
const SOURCE_DIR = join(CODEx_DIR, 'wps365-docs')
const CANONICAL_SCRIPTS_DIR = join(REPO_DIR, 'assets', 'wps365-docs', 'scripts')
const DEFAULT_OUTPUT_DIR = join(CODEx_DIR, 'dist', 'wps365-docs')
const SCRIPT_NAMES = [
  '_wps_cli.py',
  'file_fetch.py',
  'file_write.py',
  'otl_fetch.py',
  'otl_write.py',
  'xlsx_edit.py',
]
const SOURCE_TOP_LEVEL = new Set(['plugin.json', '.codex-plugin', 'skills'])
const FORBIDDEN_PARTS = new Set(['node_modules', '__pycache__', '.git', '.venv', 'venv'])
const FORBIDDEN_FILE = /^(?:\.env(?:\..*)?|\.npmrc|\.pypirc|credentials?(?:[._-].*)?|secrets?(?:[._-].*)?|.*\.(?:pem|key|p12|pfx|token))$/i

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function readJson(path, label) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch (error) {
    throw new Error(`${label} must be valid JSON: ${error.message}`)
  }
}

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validateManifests(portable, legacy) {
  for (const [label, manifest] of [['plugin.json', portable], ['.codex-plugin/plugin.json', legacy]]) {
    assert(isObject(manifest), `${label} must contain a JSON object`)
    assert(manifest.name === 'wps365-docs', `${label} must have name "wps365-docs"`)
    assert(manifest.version === ROOT_PACKAGE_VERSION, `${label} version must match root package.json (${ROOT_PACKAGE_VERSION})`)
  }

  const extension = portable.extensions?.['com.openai']
  assert(extension?.requires_local_executor === true, 'plugin.json extensions["com.openai"].requires_local_executor must be true')
  assert(!('skills' in portable), 'portable plugin.json must not declare top-level skills')
  assert(!('apps' in portable), 'portable plugin.json must not declare top-level apps')
  assert(!('mcpServers' in portable), 'portable plugin.json must not declare top-level mcpServers')
  assert(!('interface' in portable), 'portable plugin.json must not declare top-level interface')

  assert(legacy.requires_local_executor === true, '.codex-plugin/plugin.json requires_local_executor must be true')
  assert(legacy.skills === './skills/', '.codex-plugin/plugin.json skills must be "./skills/"')
  assert(isObject(extension.interface), 'plugin.json extensions["com.openai"].interface must be an object')
  assert(isObject(legacy.interface), '.codex-plugin/plugin.json interface must be an object')
  assert(isDeepStrictEqual(extension.interface, legacy.interface), 'both manifests must carry the same interface presentation metadata')

  const { displayName, shortDescription } = extension.interface
  assert(typeof displayName === 'string' && displayName.trim().length > 0, 'manifest interface must include a displayName')
  assert(typeof shortDescription === 'string' && shortDescription.trim().length > 0, 'manifest interface must include a shortDescription')
  assert([...shortDescription].length <= 30, 'manifest interface shortDescription must be at most 30 Unicode code points')

  const portablePresentation = portable.extensions['com.openai'].interface
  for (const [label, presentation] of [
    ['plugin.json', portablePresentation],
    ['.codex-plugin/plugin.json', legacy.interface],
  ]) {
    assert(presentation.logo === './assets/icon.svg', `${label} interface logo must be "./assets/icon.svg"`)
    assert(presentation.composerIcon === './assets/icon.svg', `${label} interface composerIcon must be "./assets/icon.svg"`)
  }
}

function assertSourceTreeIsPortable(sourceDir) {
  assert(existsSync(sourceDir) && statSync(sourceDir).isDirectory(), `plugin source directory is missing: ${sourceDir}`)

  const visit = (dir, prefix = '') => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name
      const path = join(dir, entry.name)
      assert(!entry.isSymbolicLink(), `plugin source must not contain symlinks: ${rel}`)
      if (entry.name === '.DS_Store') continue
      assert(!FORBIDDEN_PARTS.has(entry.name), `plugin source must not contain dependency or cache path: ${rel}`)
      assert(!FORBIDDEN_FILE.test(entry.name), `plugin source must not contain secret or dependency file: ${rel}`)
      assert(!/\.(?:pyc|pyo|log)$/i.test(entry.name), `plugin source must not contain generated file: ${rel}`)

      if (prefix === '') assert(SOURCE_TOP_LEVEL.has(entry.name), `unexpected top-level plugin source path: ${rel}`)
      if (prefix === 'skills') assert(entry.name === 'wps365-docs', `unexpected skill source path: ${rel}`)
      const allowed = rel === 'plugin.json'
        || rel === '.codex-plugin'
        || rel === '.codex-plugin/plugin.json'
        || rel === 'skills'
        || rel === 'skills/wps365-docs'
        || rel === 'skills/wps365-docs/SKILL.md'
        || rel === 'skills/wps365-docs/agents'
        || rel === 'skills/wps365-docs/agents/openai.yaml'
        || rel === 'skills/wps365-docs/references'
        || /^skills\/wps365-docs\/references\/[A-Za-z0-9._-]+\.md$/.test(rel)
      assert(allowed, `unexpected plugin source path: ${rel}`)

      if (entry.isDirectory()) visit(path, rel)
      else assert(entry.isFile(), `unsupported file type in plugin source: ${rel}`)
    }
  }
  visit(sourceDir)

  const required = [
    'plugin.json',
    '.codex-plugin/plugin.json',
    'skills/wps365-docs/SKILL.md',
    'skills/wps365-docs/agents/openai.yaml',
  ]
  for (const rel of required) assert(existsSync(join(sourceDir, rel)), `plugin source is missing ${rel}`)
  const referencesDir = join(sourceDir, 'skills', 'wps365-docs', 'references')
  assert(existsSync(referencesDir) && statSync(referencesDir).isDirectory(), 'plugin source is missing skills/wps365-docs/references')
  const references = readdirSync(referencesDir).filter((name) => name.endsWith('.md'))
  assert(references.length > 0, 'plugin source must include at least one skills/wps365-docs/references/*.md file')
  assert(references.length === readdirSync(referencesDir).length, 'references directory may contain Markdown files only')
}

function assertRegularFile(path, label) {
  assert(existsSync(path), `required ${label} is missing: ${path}`)
  assert(lstatSync(path).isFile(), `required ${label} must be a regular file: ${path}`)
}

function assertSafeOutput(outputDir) {
  const resolved = resolve(outputDir)
  assert(!resolved.includes(`${sep}node_modules${sep}`), 'output directory must not be inside node_modules')
  if (resolved === DEFAULT_OUTPUT_DIR) {
    const controlledParent = join(CODEx_DIR, 'dist')
    if (existsSync(controlledParent)) {
      assert(!lstatSync(controlledParent).isSymbolicLink(), `refusing to write through a symlink output parent: ${controlledParent}`)
    }
  }
  if (!existsSync(resolved)) return

  assert(!lstatSync(resolved).isSymbolicLink(), `refusing to replace a symlink output directory: ${resolved}`)
  assert(statSync(resolved).isDirectory(), `output path exists and is not a directory: ${resolved}`)
  assert(resolved === DEFAULT_OUTPUT_DIR, `refusing to replace existing custom output directory: ${resolved}`)

  // The CLI's one controlled destination may be recreated on repeat builds.
  // Check the path beneath the repository before removing it.
  const realRepo = realpathSync(REPO_DIR)
  const realOut = realpathSync(resolved)
  assert(realOut === resolved, 'refusing to replace output whose path resolves through a symlink')
  assert(relative(realRepo, realOut) === relative(REPO_DIR, DEFAULT_OUTPUT_DIR), 'output directory is outside the controlled dist path')
}

function checkOutputTree(outputDir) {
  const visit = (dir, prefix = '') => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const rel = prefix ? `${prefix}/${entry.name}` : entry.name
      assert(!entry.isSymbolicLink(), `built plugin must not contain symlinks: ${rel}`)
      assert(!FORBIDDEN_PARTS.has(entry.name), `built plugin must not contain dependency or cache path: ${rel}`)
      assert(!FORBIDDEN_FILE.test(entry.name), `built plugin must not contain secret or dependency file: ${rel}`)
      assert(!/\.(?:pyc|pyo|log)$/i.test(entry.name), `built plugin must not contain generated file: ${rel}`)
      if (entry.isDirectory()) visit(join(dir, entry.name), rel)
      else assert(entry.isFile(), `built plugin contains an unsupported file type: ${rel}`)
    }
  }
  visit(outputDir)
}

/** Build the standalone Codex plugin directory and, by default, its tar.gz. */
export function buildCodexPlugin({ outputDir = DEFAULT_OUTPUT_DIR, archive = true } = {}) {
  assertSourceTreeIsPortable(SOURCE_DIR)
  assertRegularFile(join(REPO_DIR, 'icon.svg'), 'source icon')
  assertRegularFile(join(REPO_DIR, 'LICENSE'), 'source license')

  for (const name of SCRIPT_NAMES) assertRegularFile(join(CANONICAL_SCRIPTS_DIR, name), `canonical script ${name}`)

  const portableManifest = readJson(join(SOURCE_DIR, 'plugin.json'), 'plugin.json')
  const legacyManifest = readJson(join(SOURCE_DIR, '.codex-plugin', 'plugin.json'), '.codex-plugin/plugin.json')
  validateManifests(portableManifest, legacyManifest)

  const resolvedOutput = resolve(outputDir)
  assertSafeOutput(resolvedOutput)
  mkdirSync(dirname(resolvedOutput), { recursive: true })
  if (existsSync(resolvedOutput)) rmSync(resolvedOutput, { recursive: true })

  try {
    cpSync(SOURCE_DIR, resolvedOutput, {
      recursive: true,
      filter: (source) => {
        const rel = relative(SOURCE_DIR, source).split(sep).join('/')
        return rel !== '.DS_Store'
          && !rel.endsWith('/.DS_Store')
      },
    })

    const scriptsOutput = join(resolvedOutput, 'skills', 'wps365-docs', 'scripts')
    mkdirSync(scriptsOutput, { recursive: true })
    for (const name of SCRIPT_NAMES) cpSync(join(CANONICAL_SCRIPTS_DIR, name), join(scriptsOutput, name))

    const assetsOutput = join(resolvedOutput, 'assets')
    mkdirSync(assetsOutput, { recursive: true })
    cpSync(join(REPO_DIR, 'icon.svg'), join(assetsOutput, 'icon.svg'))
    cpSync(join(REPO_DIR, 'LICENSE'), join(resolvedOutput, 'LICENSE'))

    validateManifests(
      readJson(join(resolvedOutput, 'plugin.json'), 'built plugin.json'),
      readJson(join(resolvedOutput, '.codex-plugin', 'plugin.json'), 'built .codex-plugin/plugin.json'),
    )
    for (const name of SCRIPT_NAMES) {
      const sourceHash = sha256(join(CANONICAL_SCRIPTS_DIR, name))
      const builtHash = sha256(join(scriptsOutput, name))
      assert(sourceHash === builtHash, `canonical script copy changed content: ${name}`)
    }
    checkOutputTree(resolvedOutput)
  } catch (error) {
    throw new Error(`Codex plugin build failed: ${error.message}`, { cause: error })
  }

  let archivePath
  if (archive) {
    const pluginName = portableManifest.name
    archivePath = join(dirname(resolvedOutput), `${pluginName}-${portableManifest.version}.tar.gz`)
    try {
      if (existsSync(archivePath)) assert(lstatSync(archivePath).isFile(), `refusing to overwrite non-file archive path: ${archivePath}`)
      execFileSync('tar', ['-czf', archivePath, '-C', dirname(resolvedOutput), pluginName], { stdio: 'pipe' })
    } catch (error) {
      if (error.code === 'ENOENT') throw new Error('The system `tar` command is required to create the Codex plugin archive.')
      throw new Error(`Could not create Codex plugin archive: ${error.message}`, { cause: error })
    }
  }

  return { outputDir: resolvedOutput, archivePath }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const result = buildCodexPlugin()
    console.log(`Codex plugin built: ${result.outputDir}`)
    console.log(`Codex plugin archive created: ${result.archivePath}`)
  } catch (error) {
    console.error(error.message)
    process.exitCode = 1
  }
}

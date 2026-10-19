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
  'dbsheet.py',
]
const FORBIDDEN_PARTS = new Set(['node_modules', '__pycache__', '.git', '.venv', 'venv'])
const FORBIDDEN_FILE = /^(?:\.env(?:\..*)?|\.npmrc|\.pypirc|credentials?(?:[._-].*)?|secrets?(?:[._-].*)?|.*\.(?:pem|key|p12|pfx|token))$/i

/**
 * Paragraphs the Codex copy shares with the DSH original.
 *
 * The Codex references are adaptations, not copies, so comparing whole files
 * would be noise. What must not drift is the measured API behaviour and the
 * operating rules: those numbered constraints state the same facts on both
 * sides, and a correction applied to only one of them leaves the other telling
 * readers something false.
 *
 * `shared` items must hash-match after whitespace normalisation. `adapted` items
 * are deliberately reworded for Codex and are only checked for presence — the
 * point of listing them is that a new constraint cannot appear on one side and
 * be silently skipped on the other: it has to be declared here as one or the
 * other, and the item counts must agree.
 *
 * Adding a constraint to either side therefore fails this check until the
 * manifest below is updated.
 */
const SHARED_DOCUMENTS = [
  {
    name: 'office-files.md',
    dsh: join(REPO_DIR, 'assets', 'wps365-docs', 'references', 'office-files.md'),
    codex: join(SOURCE_DIR, 'skills', 'wps365-docs', 'references', 'office-files.md'),
    sections: {
      '## 底层路线与硬约束': { shared: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], adapted: [11] },
      // Codex has no LibreOffice kit, so the recalculation advice is its own.
      '## 本地编辑的两个坑': { shared: [2], adapted: [1] },
    },
    wholeSections: [],
  },
  {
    name: 'otl.md',
    dsh: join(REPO_DIR, 'assets', 'wps365-docs', 'references', 'otl.md'),
    codex: join(SOURCE_DIR, 'skills', 'wps365-docs', 'references', 'otl.md'),
    sections: {
      '## 硬约束（踩过的坑）': {
        shared: [1, 2, 3, 4, 5, 6, 8, 10, 11, 12, 13, 14, 15, 16, 17],
        // 7 and 9 are phrased for Codex: it passes --cli explicitly and talks
        // about visual context rather than the DSH image pipeline.
        adapted: [7, 9],
      },
    },
    wholeSections: ['## 为什么有这条专用路线'],
  },
  {
    name: 'dbsheet.md',
    dsh: join(REPO_DIR, 'assets', 'wps365-docs', 'references', 'dbsheet.md'),
    codex: join(SOURCE_DIR, 'skills', 'wps365-docs', 'references', 'dbsheet.md'),
    sections: {
      // Both sides state the same 15 measured constraints; the Codex copy is
      // reworded rather than copied, so only the count/presence is compared.
      '## 15 条硬约束（按实测，v0.3.6）': {
        shared: [],
        adapted: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
      },
    },
    wholeSections: [],
  },
  {
    name: 'concurrency.md',
    dsh: join(REPO_DIR, 'assets', 'wps365-docs', 'references', 'concurrency.md'),
    codex: join(SOURCE_DIR, 'skills', 'wps365-docs', 'references', 'concurrency.md'),
    sections: {},
    // Same document on both sides, so the whole body must match.
    wholeSections: [
      '## 并发写入的实测语义（为什么 `replace` 必须全文比对）',
      '## 盘级元数据是异步滞后的（.otl 尤其明显）',
    ],
  },
]

/** Collapse formatting-only differences so a reflow is not reported as drift. */
function normalizeParagraph(text) {
  return text
    .replace(/\r\n/gu, '\n')
    .replace(/[ \t]+$/gmu, '')
    .replace(/\s+/gu, ' ')
    .trim()
}

/** Map every `##`/`###` heading to the body that follows it. */
function splitSections(markdown) {
  const sections = new Map()
  let heading = '(preamble)'
  let body = []
  for (const line of markdown.split('\n')) {
    if (/^#{2,3}\s/u.test(line)) {
      sections.set(heading, body.join('\n'))
      heading = line.trim()
      body = []
      continue
    }
    body.push(line)
  }
  sections.set(heading, body.join('\n'))
  return sections
}

/** Map `1. …` style paragraphs (with their continuation lines) to their numbers. */
function numberedItems(body) {
  const items = new Map()
  let current = null
  let buffer = []
  const flush = () => {
    if (current !== null) items.set(current, normalizeParagraph(buffer.join('\n')))
    current = null
    buffer = []
  }
  for (const line of body.split('\n')) {
    const match = /^(\d+)\.\s+(.*)$/u.exec(line)
    if (match) {
      flush()
      current = Number(match[1])
      buffer = [match[2]]
      continue
    }
    if (current !== null) buffer.push(line)
  }
  flush()
  return items
}

function shortHash(text) {
  return createHash('sha256').update(text).digest('hex').slice(0, 12)
}

/**
 * Compare every declared shared paragraph and report the drift found.
 *
 * @returns a list of human-readable problems; empty means the copies agree.
 */
function sharedDocumentProblems() {
  const problems = []
  for (const document of SHARED_DOCUMENTS) {
    const dshSections = splitSections(readFileSync(document.dsh, 'utf8'))
    const codexSections = splitSections(readFileSync(document.codex, 'utf8'))

    for (const [heading, declaration] of Object.entries(document.sections)) {
      const dshBody = dshSections.get(heading)
      const codexBody = codexSections.get(heading)
      if (dshBody === undefined || codexBody === undefined) {
        problems.push(`${document.name}: 小节 ${heading} 只存在于一侧`)
        continue
      }
      const dshItems = numberedItems(dshBody)
      const codexItems = numberedItems(codexBody)
      const declared = new Set([...declaration.shared, ...declaration.adapted])

      for (const number of dshItems.keys()) {
        if (!codexItems.has(number)) problems.push(`${document.name} ${heading}: codex 缺第 ${number} 条`)
      }
      for (const number of codexItems.keys()) {
        if (!dshItems.has(number)) problems.push(`${document.name} ${heading}: codex 多出第 ${number} 条`)
      }
      for (const number of declared) {
        if (!dshItems.has(number) && !codexItems.has(number)) {
          problems.push(`${document.name} ${heading}: 清单里的第 ${number} 条两侧都不存在（清单写错了？）`)
        }
      }
      for (const number of new Set([...dshItems.keys(), ...codexItems.keys()])) {
        if (!declared.has(number)) {
          problems.push(
            `${document.name} ${heading}: 第 ${number} 条既未声明为 shared 也未声明为 adapted —— `
            + '新增/改动的条目必须在 checks/validate.mjs 的 SHARED_DOCUMENTS 里登记',
          )
        }
      }
      for (const number of declaration.shared) {
        const dshText = dshItems.get(number)
        const codexText = codexItems.get(number)
        if (dshText === undefined || codexText === undefined) continue
        if (dshText !== codexText) {
          problems.push(
            `${document.name} 第 ${number} 条两侧不再一致（DSH ${shortHash(dshText)} / codex ${shortHash(codexText)}）`,
          )
        }
      }
    }

    for (const heading of document.wholeSections) {
      const dshBody = dshSections.get(heading)
      const codexBody = codexSections.get(heading)
      if (dshBody === undefined || codexBody === undefined) {
        problems.push(`${document.name}: 共享小节 ${heading} 只存在于一侧`)
        continue
      }
      if (normalizeParagraph(dshBody) !== normalizeParagraph(codexBody)) {
        problems.push(
          `${document.name} 小节 ${heading} 两侧不一致（DSH ${shortHash(normalizeParagraph(dshBody))} / `
          + `codex ${shortHash(normalizeParagraph(codexBody))}）`,
        )
      }
    }
  }
  return problems
}

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

test('shared paragraphs still match the DSH originals', () => {
  const problems = sharedDocumentProblems()
  assert.deepEqual(
    problems,
    [],
    `the Codex references drifted from the DSH originals:\n  ${problems.join('\n  ')}`,
  )
})

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

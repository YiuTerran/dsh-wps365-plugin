/**
 * Unit tests for the pure catalog helpers and the shipped asset layout.
 *
 * These run with a plain `node --test`; no Harness, no network, no dependency
 * install. The rendered-body budget assertion is a regression guard for the
 * standard preset's tool-result pruner (`thresholdChars: 8192`), which would
 * otherwise truncate the middle of the skill body when it is loaded.
 */
import assert from 'node:assert/strict'
import { readFileSync, statSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  BUNDLED_SKILL_RANK,
  PROVIDER_NAME,
  SKILL_HELPER,
  SKILL_MODULES,
  SKILL_NAME,
  SKILL_SCRIPTS,
  buildCandidate,
  buildRuntimeHint,
  expandHome,
  parseFrontmatter,
  parseSkillMarkdown,
} from '../src/skill-catalog.js'

const ASSET_ROOT = fileURLToPath(new URL('../assets/wps365-docs/', import.meta.url))
/**
 * The same directory without the URL's trailing separator, as the hint renders
 * it. The separator is platform-dependent (`\` on Windows) and `fileURLToPath`
 * can leave either kind behind, so both are stripped.
 */
const ASSET_DIR = ASSET_ROOT.replace(/[\\/]+$/, '')
/** Separator the hint must use for this asset root, on this platform. */
const ASSET_SEP = ASSET_DIR.includes('\\') ? '\\' : '/'
const SKILL_PATH = fileURLToPath(new URL('../assets/wps365-docs/SKILL.md', import.meta.url))

test('SKILL.md parses into the advertised name and description', () => {
  const raw = readFileSync(SKILL_PATH, 'utf8')
  const parsed = parseSkillMarkdown(raw, SKILL_PATH)
  assert.equal(parsed.name, SKILL_NAME)
  assert.ok(parsed.description.includes('WPS 365'), 'description should describe the capability')
  assert.ok(parsed.content.startsWith('# '), 'body must not keep the frontmatter')
  assert.ok(!parsed.content.includes('name: wps365-docs'), 'frontmatter must be stripped')
})

test('rendered skill body stays inside the 8192-code-point pruner budget', () => {
  const body = parseSkillMarkdown(readFileSync(SKILL_PATH, 'utf8'), SKILL_PATH).content
  const hint = buildRuntimeHint({ skillDir: ASSET_ROOT, cli: '/usr/local/bin/wps365-cli', cliExists: true })
  const rendered = body + hint
  assert.ok(
    [...rendered].length < 8192,
    `rendered body is ${[...rendered].length} code points; the standard preset prunes tool results over 8192`,
  )
})

test('the body keeps the mandatory conflict-check rule and points at the references', () => {
  const body = parseSkillMarkdown(readFileSync(SKILL_PATH, 'utf8'), SKILL_PATH).content
  assert.ok(body.includes('写入前必须比对'), 'the conflict rule must stay in the always-loaded body')
  for (const reference of ['references/otl.md', 'references/office-files.md', 'references/concurrency.md']) {
    assert.ok(body.includes(reference), `body should link ${reference}`)
    assert.ok(statSync(new URL(`../assets/wps365-docs/${reference}`, import.meta.url)).isFile())
  }
})

test('every reference also fits the pruner budget, since it is read as a tool result', () => {
  for (const reference of ['references/otl.md', 'references/office-files.md', 'references/concurrency.md']) {
    const text = readFileSync(new URL(`../assets/wps365-docs/${reference}`, import.meta.url), 'utf8')
    const codePoints = [...text].length
    assert.ok(codePoints < 8192, `${reference} is ${codePoints} code points`)
    assert.ok(text.startsWith('# '), `${reference} should start with a title`)
    assert.ok(text.includes('wps365-docs'), `${reference} should name the skill it belongs to`)
  }
})

test('every module the instructions and scripts need ships beside SKILL.md', () => {
  for (const script of SKILL_MODULES) {
    assert.ok(statSync(new URL(`../assets/wps365-docs/scripts/${script}`, import.meta.url)).isFile(), script)
  }
  assert.ok(SKILL_MODULES.includes(SKILL_HELPER), 'the shared CLI-discovery helper must be validated too')
  assert.ok(!SKILL_SCRIPTS.includes(SKILL_HELPER), 'the helper is not a user-facing script')
})

test('shipped scripts use the CLI path the plugin reports, not a hardcoded skill directory', () => {
  for (const script of SKILL_MODULES) {
    const source = readFileSync(new URL(`../assets/wps365-docs/scripts/${script}`, import.meta.url), 'utf8')
    assert.ok(!source.includes('.dsh/skills/wps365-docs'), `${script} must not hardcode a skill install path`)
  }
})

test('parseSkillMarkdown rejects unusable input', () => {
  assert.throws(() => parseSkillMarkdown('# no frontmatter\n', 'x.md'), /no YAML frontmatter/)
  assert.throws(() => parseSkillMarkdown('---\nname: a\n---\nbody\n', 'x.md'), /no description/)
  assert.throws(() => parseSkillMarkdown('---\ndescription: >-\n  folded\n---\n', 'x.md'), /multi-line/)
})

test('parseFrontmatter reads flat scalars and unquotes them', () => {
  const fields = parseFrontmatter('name: demo\ndescription: "a: b"\ndisable-model-invocation: true\n', 'x.md')
  assert.deepEqual(fields, { name: 'demo', description: 'a: b', 'disable-model-invocation': 'true' })
})

test('buildCandidate produces a registry entry in the bundled layer', () => {
  const candidate = buildCandidate({ assetRoot: ASSET_ROOT, locator: SKILL_PATH, description: 'd' })
  assert.deepEqual(candidate, {
    name: SKILL_NAME,
    description: 'd',
    invocation: { modelInvocable: true, userInvocable: true },
    provider: PROVIDER_NAME,
    source: 'bundled',
    rank: BUNDLED_SKILL_RANK,
    resourceBase: { kind: 'directory', path: ASSET_ROOT },
    locator: SKILL_PATH,
  })
})

test('the rank matches the bundled layer, so a user copy still wins', () => {
  assert.equal(BUNDLED_SKILL_RANK, 600)
})

test('buildRuntimeHint reports a present CLI', () => {
  // No explicit platform: the hint must render for the machine running the test,
  // which is what the plugin does at activation.
  const hint = buildRuntimeHint({ skillDir: ASSET_ROOT, cli: '/opt/wps365-cli', cliExists: true })
  assert.ok(hint.includes(`\`{skillDir}\` = \`${ASSET_DIR}\``))
  assert.ok(hint.includes(`\`${ASSET_DIR}${ASSET_SEP}scripts${ASSET_SEP}\``),
    'the scripts directory must have no doubled separator')
  // URLs legitimately contain "//"; the paths this block injects must not.
  for (const line of hint.split('\n')) {
    if (line.includes('http')) continue
    assert.ok(!line.includes('//'), `no injected path may contain a doubled slash: ${line}`)
  }
  assert.ok(hint.includes('`/opt/wps365-cli`'))
  assert.ok(hint.includes('load_workspace_dependencies'))
  assert.ok(!hint.includes('未找到'))
})

test('buildRuntimeHint warns when the CLI is missing', () => {
  const hint = buildRuntimeHint({ skillDir: ASSET_ROOT, cli: '/nope/wps365-cli', cliExists: false })
  assert.ok(hint.includes('未找到'))
  assert.ok(hint.includes('auth login --device'))
})

test('buildRuntimeHint honours a disabled CLI and an explicit interpreter', () => {
  const hint = buildRuntimeHint({ skillDir: ASSET_ROOT, python: '/opt/python3' })
  assert.ok(hint.includes('不注入 CLI 路径'))
  assert.ok(hint.includes('`/opt/python3`'))
  assert.ok(!hint.includes('load_workspace_dependencies'))
})

test('buildRuntimeHint defines only the placeholders it actually resolves', () => {
  const full = buildRuntimeHint({
    skillDir: ASSET_ROOT,
    cli: '/opt/wps365-cli',
    cliExists: true,
    python: '/opt/python3',
    tmpDir: '/tmp/dsh-work/',
    platform: 'linux',
  })
  assert.ok(full.includes('`{python}` = `/opt/python3`'))
  assert.ok(full.includes('`{tmp}` = `/tmp/dsh-work`'), 'the temp directory keeps no trailing separator')
  assert.ok(full.includes('`{cli}` = `/opt/wps365-cli`'))
  const footer = full.split('\n').filter((line) => line.includes('一律替换')).join('')
  for (const name of ['{skillDir}', '{python}', '{tmp}', '{cli}']) {
    assert.ok(footer.includes(name), `the footer must name the placeholder it defines: ${name}`)
  }

  // Nothing resolved: the block must not promise a temp directory or a CLI path
  // it never printed, or the model will look for a value that is not there.
  const deferred = buildRuntimeHint({ skillDir: ASSET_ROOT, cli: undefined, cliExists: false })
  assert.ok(deferred.includes('`{python}` = 用 `load_workspace_dependencies` 返回的解释器'))
  assert.ok(deferred.includes('不注入 CLI 路径'))
  assert.ok(!deferred.includes('{tmp}'), 'no temp directory may be mentioned when the caller provides none')
  assert.ok(!deferred.includes('`{cli}`'), 'no CLI placeholder may be named when no CLI was resolved')

  const noTmp = buildRuntimeHint({ skillDir: ASSET_ROOT, cli: '/opt/wps365-cli', cliExists: true })
  assert.ok(noTmp.includes('`{cli}` = `/opt/wps365-cli`'))
  assert.ok(!noTmp.includes('{tmp}'))
})

test('buildRuntimeHint names the installer and the shell dialect of the platform', () => {
  const windows = buildRuntimeHint({
    skillDir: 'C:\\Users\\u\\plugins\\assets\\wps365-docs',
    cli: 'C:\\Users\\u\\.local\\bin\\wps365-cli.exe',
    cliExists: false,
    platform: 'win32',
  })
  assert.ok(windows.includes('install.ps1'), 'Windows users get the PowerShell installer')
  assert.ok(windows.includes('**Windows**'))
  assert.ok(windows.includes('PowerShell'))
  assert.ok(!windows.includes('install.sh'))

  const posix = buildRuntimeHint({ skillDir: ASSET_ROOT, cli: '/nope/wps365-cli', cliExists: false, platform: 'linux' })
  assert.ok(posix.includes('install.sh'))
  assert.ok(posix.includes('bash'))
  assert.ok(!posix.includes('install.ps1'))
})

test('expandHome only rewrites a leading tilde', () => {
  assert.equal(expandHome('~/.local/bin/wps365-cli', '/home/u'), '/home/u/.local/bin/wps365-cli')
  assert.equal(expandHome('~', '/home/u'), '/home/u')
  assert.equal(expandHome('/abs/tool', '/home/u'), '/abs/tool')
  assert.equal(expandHome('/abs/~tool', '/home/u'), '/abs/~tool')
})

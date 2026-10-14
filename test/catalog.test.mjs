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
  SKILL_NAME,
  SKILL_SCRIPTS,
  buildCandidate,
  buildRuntimeHint,
  expandHome,
  parseFrontmatter,
  parseSkillMarkdown,
} from '../src/skill-catalog.js'

const ASSET_ROOT = fileURLToPath(new URL('../assets/wps365-docs/', import.meta.url))
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

test('every script the instructions call ships beside SKILL.md', () => {
  for (const script of SKILL_SCRIPTS) {
    assert.ok(statSync(new URL(`../assets/wps365-docs/scripts/${script}`, import.meta.url)).isFile(), script)
  }
})

test('shipped scripts use the CLI path the plugin reports, not a hardcoded skill directory', () => {
  for (const script of SKILL_SCRIPTS) {
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
  const hint = buildRuntimeHint({ skillDir: ASSET_ROOT, cli: '/opt/wps365-cli', cliExists: true })
  assert.ok(hint.includes(`\`{skillDir}\` = \`${ASSET_ROOT}\``))
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

test('expandHome only rewrites a leading tilde', () => {
  assert.equal(expandHome('~/.local/bin/wps365-cli', '/home/u'), '/home/u/.local/bin/wps365-cli')
  assert.equal(expandHome('~', '/home/u'), '/home/u')
  assert.equal(expandHome('/abs/tool', '/home/u'), '/abs/tool')
  assert.equal(expandHome('/abs/~tool', '/home/u'), '/abs/~tool')
})

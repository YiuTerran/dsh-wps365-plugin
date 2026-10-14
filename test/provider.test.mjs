/**
 * Contract tests for the plugin entry: `apply()` must register exactly one
 * skill provider with `ctx.skills`, and that provider must serve the bundled
 * SKILL.md with the injected environment block.
 *
 * The stub context implements only the surface the plugin declares it needs
 * (`inject = ['skills']`), so a dependency on any other service fails here
 * rather than in a user's profile.
 */
import assert from 'node:assert/strict'
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { Config, apply, inject, name } from '../index.js'
import { CONFIG_JSON_SCHEMA } from '../src/plugin-config.js'
import { PROVIDER_NAME, SKILL_NAME } from '../src/skill-catalog.js'

const ASSET_ROOT = fileURLToPath(new URL('../assets/wps365-docs/', import.meta.url))

/** Minimal context: captures providers and warnings, nothing else. */
function stubContext() {
  const providers = []
  const warnings = []
  return {
    providers,
    warnings,
    ctx: {
      skills: {
        registerProvider(factory) {
          providers.push(factory())
          return () => {}
        },
      },
      logger: { warn: (message) => warnings.push(message) },
    },
  }
}

test('the entry exports the documented plugin surface', () => {
  assert.equal(name, 'skill-wps365-docs')
  assert.deepEqual(inject, ['skills'])
  assert.equal(typeof apply, 'function')
  assert.equal(Config['~standard'].version, 1)
})

test('the plugin imports nothing but relative files and node: builtins', () => {
  // Load-bearing: a bundle installed into a profile is imported from the
  // profile directory, whose node_modules holds no @deepseek-ai package, so a
  // bare specifier would fail at activation with ERR_MODULE_NOT_FOUND.
  const sources = ['../index.js', '../src/skill-catalog.js', '../src/plugin-config.js']
  for (const source of sources) {
    const url = new URL(source, import.meta.url)
    const text = readFileSync(url, 'utf8')
    const specifiers = [...text.matchAll(/\bfrom\s+(['"])([^'"]+)\1/g)].map((match) => match[2])
    if (source === '../index.js') assert.ok(specifiers.length > 0, 'index.js should import the pure helpers')
    for (const specifier of specifiers) {
      assert.ok(
        specifier.startsWith('./') || specifier.startsWith('../') || specifier.startsWith('node:'),
        `${source} imports "${specifier}"; only relative paths and node: builtins resolve from a profile bundle`,
      )
    }
  }
})

test('apply() registers one provider that serves the bundled skill', async () => {
  const { ctx, providers } = stubContext()
  apply(ctx, Config['~standard'].validate({ cli: '/definitely/not/here/wps365-cli' }).value)

  assert.equal(providers.length, 1)

  const candidates = await providers[0].list()
  assert.equal(candidates.length, 1)
  assert.equal(candidates[0].name, SKILL_NAME)
  assert.equal(candidates[0].provider, PROVIDER_NAME)
  assert.equal(candidates[0].resourceBase.path, ASSET_ROOT)

  const skill = await providers[0].get(candidates[0], {})
  assert.equal(skill.name, SKILL_NAME)
  assert.equal(skill.provider, PROVIDER_NAME)
  assert.deepEqual(skill.resourceBase, { kind: 'directory', path: ASSET_ROOT })
  assert.ok(skill.content.includes('# 读取与写入 WPS 365 云文档'))
  assert.ok(skill.content.includes('## 本机资源（由 @tryao/dsh-wps365-plugin 注入）'), 'the environment block must be appended')
  assert.ok(skill.content.includes(`\`{skillDir}\` = \`${ASSET_ROOT.replace(/\/+$/, '')}\``))
  assert.ok(!skill.content.includes('wps365-docs//'), 'the injected block must not double the path separator')
  assert.ok(skill.content.includes('未找到'), 'a missing CLI must be reported to the model')
})

test('an existing CLI path is pinned in the injected block', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-wps365-'))
  try {
    // Create the executable so this branch does not depend on the machine
    // having wps365-cli installed at all (CI runners do not).
    const cli = join(dir, 'wps365-cli')
    writeFileSync(cli, '#!/bin/sh\n', { mode: 0o755 })

    const { ctx, providers } = stubContext()
    apply(ctx, { cli })
    const [candidate] = await providers[0].list()
    const skill = await providers[0].get(candidate, {})

    assert.ok(skill.content.includes(`\`wps365-cli\` = \`${cli}\``), 'the resolved CLI path must reach the model')
    assert.ok(!skill.content.includes('未找到'), 'an existing CLI must not be reported as missing')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('the default config reports the CLI state without leaking a tilde', async () => {
  const { ctx, providers } = stubContext()
  apply(ctx, {})
  const [candidate] = await providers[0].list()
  const skill = await providers[0].get(candidate, {})

  // Whether ~/.local/bin/wps365-cli exists is a property of the machine, not of
  // the bundle (a CI runner has no such file, a developer machine usually does),
  // so assert the invariant rather than the environment: the block always says
  // which CLI to use, and a raw tilde never reaches the model.
  const reportsPath = skill.content.includes('`wps365-cli` = `')
  const reportsMissing = skill.content.includes('`wps365-cli` 未找到')
  assert.ok(reportsPath || reportsMissing, 'the block must always report the CLI state')
  assert.ok(!skill.content.includes('~/.local/bin'), 'the tilde must be expanded before the model sees it')
  assert.ok(!skill.content.includes('`~/'), 'no unexpanded tilde path may reach the model')
})

test('every load re-reads SKILL.md, so edits need no reload', async () => {
  const { ctx, providers } = stubContext()
  apply(ctx, {})
  const [candidate] = await providers[0].list()
  const dir = mkdtempSync(join(tmpdir(), 'dsh-wps365-'))
  try {
    const target = join(dir, 'SKILL.md')
    writeFileSync(target, '---\nname: wps365-docs\ndescription: edited\n---\n\nedited body\n', 'utf8')
    const copy = { ...candidate, locator: target }
    const skill = await providers[0].get(copy, {})
    assert.equal(skill.description, 'edited')
    assert.ok(skill.content.startsWith('edited body'))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('config.cli and config.python control the injected block', async () => {
  const { ctx, providers } = stubContext()
  apply(ctx, { cli: false, python: '/opt/python3' })
  const [candidate] = await providers[0].list()
  const skill = await providers[0].get(candidate, {})
  assert.ok(skill.content.includes('不注入 CLI 路径'))
  assert.ok(skill.content.includes('`/opt/python3`'))
})

test('a missing CLI is logged as a warning but does not fail activation', () => {
  const { ctx, warnings } = stubContext()
  apply(ctx, { cli: '/definitely/not/here/wps365-cli' })
  assert.equal(warnings.length, 1)
  assert.ok(warnings[0].includes('not found'))
})

test('activation fails loudly on unusable assets', () => {
  const dir = mkdtempSync(join(tmpdir(), 'dsh-wps365-'))
  try {
    const bad = join(dir, 'assets')
    cpSync(ASSET_ROOT, bad, { recursive: true })
    rmSync(join(bad, 'scripts', 'otl_write.py'))

    const { ctx } = stubContext()
    assert.throws(() => apply(ctx, { assetRoot: bad }), /scripts\/otl_write\.py is missing/)
    assert.throws(() => apply(ctx, { assetRoot: 'relative/path' }), /must be an absolute directory/)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('Config normalizes, defaults, and rejects bad input', () => {
  const validate = (value) => Config['~standard'].validate(value)

  assert.deepEqual(validate(undefined), { value: {} })
  assert.deepEqual(validate({ cli: '~/.local/bin/wps365-cli' }), { value: { cli: '~/.local/bin/wps365-cli' } })
  assert.deepEqual(validate({ cli: false, python: false }), { value: { cli: false, python: false } })

  const unknown = validate({ clii: 'x' })
  assert.deepEqual(unknown.issues[0].path, ['clii'])
  assert.match(unknown.issues[0].message, /unknown config field/)

  assert.match(validate({ cli: 42 }).issues[0].message, /non-empty string/)
  assert.match(validate({ assetRoot: ' x ' }).issues[0].message, /whitespace/)
  assert.match(validate('nope').issues[0].message, /must be an object/)

  assert.equal(CONFIG_JSON_SCHEMA.additionalProperties, false)
  assert.deepEqual(Object.keys(CONFIG_JSON_SCHEMA.properties).sort(), ['assetRoot', 'cli', 'python'])
  // The schema must stay off the Config object itself: a toJSON() member makes
  // dsh-settings treat Config as a native schema and crash the welcome RPC.
  assert.equal('toJSON' in Config, false)
})

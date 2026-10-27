import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { cpSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'

const root = new URL('../', import.meta.url)
const skill = new URL('general-skill/wps365-docs/', root)

test('portable helpers match the canonical implementation', () => {
  execFileSync(process.execPath, [fileURLToPath(new URL('scripts/sync-general-skill.mjs', root)), '--check'])
})

test('portable skill links resolve within its own directory', () => {
  const files = [new URL('SKILL.md', skill), ...readdirSync(new URL('references/', skill))
    .map((name) => new URL(`references/${name}`, skill))]
  for (const file of files) {
    const text = readFileSync(file, 'utf8')
    for (const [, path] of text.matchAll(/\]\(([^)]+\.md)\)/g)) {
      const target = new URL(path, file)
      assert.ok(target.href.startsWith(skill.href), `reference escapes skill: ${target}`)
      assert.ok(readFileSync(target).length > 0)
    }
    for (const [, name] of text.matchAll(/\{skillDir\}\/scripts\/([a-z_]+\.py)/g)) {
      assert.ok(readFileSync(new URL(`scripts/${name}`, skill)).length > 0)
    }
  }
})

test('copied skill scripts start without the repository or an agent runtime', () => {
  const temp = mkdtempSync(join(tmpdir(), 'wps365-portable-'))
  try {
    const standalone = join(temp, 'skill with spaces')
    cpSync(skill, standalone, { recursive: true })
    for (const name of readdirSync(join(standalone, 'scripts')).filter((name) => name !== '_wps_cli.py' && name.endsWith('.py'))) {
      const output = execFileSync('python3', ['-B', join(standalone, 'scripts', name), '--help'], {
        cwd: temp,
        encoding: 'utf8',
        env: { ...process.env, PYTHONPATH: '', PYTHONDONTWRITEBYTECODE: '1' },
      })
      assert.match(output, /usage:/i, `${name} did not expose a usable CLI`)
    }
  } finally {
    rmSync(temp, { recursive: true, force: true })
  }
})

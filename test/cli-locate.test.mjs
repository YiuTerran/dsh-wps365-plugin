/**
 * Unit tests for CLI discovery.
 *
 * The probe is injected, so the Windows behaviour (extensionless commands,
 * `PATHEXT`, `;`-separated `PATH`, `%USERPROFILE%`) is verified on macOS. This
 * is the part that used to fail silently: `statSync('…/wps365-cli')` reports
 * "missing" on a Windows machine where the file is `wps365-cli.exe`.
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  CLI_BASENAME,
  commandSpellings,
  defaultCliPath,
  expandPath,
  locateCli,
} from '../src/cli-locate.js'

/** A probe that only knows the paths listed here, like a real filesystem. */
function fakeFiles(paths) {
  const known = new Set(paths)
  return (path) => known.has(path)
}

test('expandPath rewrites a leading tilde in both separators', () => {
  assert.equal(expandPath('~/.local/bin/wps365-cli', { home: '/home/u' }), '/home/u/.local/bin/wps365-cli')
  assert.equal(
    expandPath('~\\.local\\bin\\wps365-cli.exe', { home: 'C:\\Users\\u' }),
    'C:\\Users\\u\\.local\\bin\\wps365-cli.exe',
    'a backslash tilde must stay a native Windows path',
  )
  assert.equal(expandPath('~', { home: '/home/u' }), '/home/u')
  assert.equal(expandPath('~/x', { home: '/home/u/' }), '/home/u/x', 'a trailing separator on home is not doubled')
  assert.equal(expandPath('/abs/~tool', { home: '/home/u' }), '/abs/~tool')
})

test('expandPath resolves environment references and keeps unknown ones visible', () => {
  const env = { USERPROFILE: 'C:\\Users\\u', HOME: '/home/u' }
  assert.equal(expandPath('%USERPROFILE%\\.local\\bin\\wps365-cli.exe', { home: '', env }), 'C:\\Users\\u\\.local\\bin\\wps365-cli.exe')
  assert.equal(expandPath('${HOME}/bin/wps365-cli', { home: '', env }), '/home/u/bin/wps365-cli')
  assert.equal(expandPath('$HOME/bin/wps365-cli', { home: '', env }), '/home/u/bin/wps365-cli')
  assert.equal(expandPath('%MISSING%/x', { home: '', env }), '%MISSING%/x', 'a typo must stay visible')
})

test('defaultCliPath carries the Windows extension and native separators', () => {
  assert.equal(defaultCliPath('win32'), '~\\.local\\bin\\wps365-cli.exe')
  assert.equal(defaultCliPath('darwin'), '~/.local/bin/wps365-cli')
  assert.equal(defaultCliPath('linux'), '~/.local/bin/wps365-cli')
})

test('commandSpellings adds PATHEXT variants only where Windows needs them', () => {
  assert.deepEqual(commandSpellings('/opt/wps365-cli', { platform: 'linux' }), ['/opt/wps365-cli'])
  const windows = commandSpellings('C:\\bin\\wps365-cli', { platform: 'win32', env: { PATHEXT: '.EXE;.CMD' } })
  assert.equal(windows[0], 'C:\\bin\\wps365-cli')
  assert.ok(windows.includes('C:\\bin\\wps365-cli.exe'))
  assert.ok(windows.includes('C:\\bin\\wps365-cli.EXE'))
  assert.ok(windows.includes('C:\\bin\\wps365-cli.cmd'))
  assert.deepEqual(
    commandSpellings('C:\\bin\\wps365-cli.exe', { platform: 'win32' }),
    ['C:\\bin\\wps365-cli.exe'],
    'a path that already names its extension is not spelled again',
  )
})

test('locateCli honours an explicitly configured path on its own', () => {
  const found = locateCli({
    cli: '~/tools/wps365-cli',
    platform: 'darwin',
    home: '/home/u',
    env: {},
    isFile: fakeFiles(['/home/u/tools/wps365-cli']),
  })
  assert.deepEqual(found, { attempted: '/home/u/tools/wps365-cli', resolved: '/home/u/tools/wps365-cli' })

  // A configured path that is wrong must surface, not silently fall back.
  const missing = locateCli({
    cli: '/wrong/wps365-cli',
    platform: 'darwin',
    home: '/home/u',
    env: { PATH: '/home/u/.local/bin' },
    isFile: fakeFiles(['/home/u/.local/bin/wps365-cli']),
  })
  assert.equal(missing.resolved, undefined)
  assert.equal(missing.attempted, '/wrong/wps365-cli')
})

test('locateCli finds the default install location, with the Windows extension', () => {
  const windows = locateCli({
    platform: 'win32',
    home: 'C:\\Users\\u',
    env: { PATHEXT: '.EXE;.CMD' },
    isFile: fakeFiles(['C:\\Users\\u\\.local\\bin\\wps365-cli.exe']),
  })
  assert.equal(windows.attempted, 'C:\\Users\\u\\.local\\bin\\wps365-cli.exe')
  assert.equal(windows.resolved, 'C:\\Users\\u\\.local\\bin\\wps365-cli.exe')

  const posix = locateCli({
    platform: 'linux',
    home: '/home/u',
    env: {},
    isFile: fakeFiles(['/home/u/.local/bin/wps365-cli']),
  })
  assert.equal(posix.resolved, '/home/u/.local/bin/wps365-cli')
})

test('locateCli falls back to PATH, which is how the Windows installer is found', () => {
  const windows = locateCli({
    platform: 'win32',
    home: 'C:\\Users\\u',
    env: { PATH: 'C:\\Program Files\\wps;C:\\tools\\bin', PATHEXT: '.EXE' },
    isFile: fakeFiles(['C:\\tools\\bin\\wps365-cli.exe']),
  })
  assert.equal(windows.resolved, 'C:\\tools\\bin\\wps365-cli.exe')

  const posix = locateCli({
    platform: 'linux',
    home: '/home/u',
    env: { PATH: '/usr/bin:/opt/wps/bin' },
    isFile: fakeFiles(['/opt/wps/bin/wps365-cli']),
  })
  assert.equal(posix.resolved, '/opt/wps/bin/wps365-cli')
})

test('a bare configured name is looked up instead of being treated as a relative path', () => {
  const result = locateCli({
    cli: CLI_BASENAME,
    platform: 'win32',
    home: 'C:\\Users\\u',
    env: { PATH: 'C:\\tools\\bin', PATHEXT: '.EXE' },
    isFile: fakeFiles(['C:\\tools\\bin\\wps365-cli.exe']),
  })
  assert.equal(result.attempted, CLI_BASENAME)
  assert.equal(result.resolved, 'C:\\tools\\bin\\wps365-cli.exe')
})

test('locateCli reports nothing found without throwing', () => {
  const windows = locateCli({
    platform: 'win32',
    home: 'C:\\Users\\u',
    env: { PATH: '', PATHEXT: '.EXE' },
    isFile: () => false,
  })
  assert.equal(windows.resolved, undefined)
  assert.equal(
    windows.attempted,
    'C:\\Users\\u\\.local\\bin\\wps365-cli.exe',
    'the attempted path is still reported to the model',
  )

  const posix = locateCli({ platform: 'darwin', home: '/home/u', env: {}, isFile: () => false })
  assert.deepEqual(posix, { attempted: '/home/u/.local/bin/wps365-cli', resolved: undefined })
})

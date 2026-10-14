/**
 * Locate the `wps365-cli` executable, on any platform.
 *
 * Everything here is pure: the environment, the platform, the home directory and
 * the "is this a file?" probe are injected, so the Windows behaviour is unit
 * tested on macOS instead of being taken on faith. `index.js` wires the real
 * `process.platform`, `process.env`, `os.homedir()` and `statSync` in.
 *
 * Two platform facts drive the design:
 *
 * - On Windows an executable is not reachable by its bare name: the file is
 *   `wps365-cli.exe`, and a `PATH` search must honour `PATHEXT`. A plain
 *   `statSync('…/wps365-cli')` reports "missing" for a perfectly good install.
 * - The documented installers place the binary in `~/.local/bin` on every
 *   platform (`.exe` on Windows), and the PowerShell one also adds it to `PATH`,
 *   so discovery tries that directory and `PATH`, and reports which answered.
 *
 * @module @tryao/dsh-wps365-plugin/cli-locate
 */

/** Command name shared by every platform; Windows appends its own extension. */
export const CLI_BASENAME = 'wps365-cli'

/** Extensions Windows appends when a command is invoked without one. */
const WINDOWS_SUFFIXES = ['.exe', '.cmd', '.bat']

function isWindows(platform) {
  return platform === 'win32'
}

function searchDelimiter(platform) {
  return isWindows(platform) ? ';' : ':'
}

function pathSeparator(platform) {
  return isWindows(platform) ? '\\' : '/'
}

/** Join a directory and a leaf with the platform's separator and no double separator. */
function joinCommand(platform, directory, leaf) {
  return `${directory.replace(/[/\\]+$/u, '')}${pathSeparator(platform)}${leaf}`
}

/**
 * Expand a leading `~` and environment references in a configured path.
 *
 * Expansion is platform independent on purpose: `%NAME%` (the Windows spelling)
 * and `${NAME}` / `$NAME` are all recognised, and an unknown variable is left
 * untouched so a typo shows up in the reported path instead of collapsing to an
 * empty string. The separator used after the home directory follows the tilde
 * spelling, so `~\x` stays a native Windows path instead of mixing separators.
 *
 * @param value - configured or default path.
 * @param options.home - the current user's home directory.
 * @param options.env - environment used for variable expansion.
 * @returns the expanded path.
 */
export function expandPath(value, { home = '', env = {} } = {}) {
  let out = value
  if (out === '~') {
    out = home
  } else if (out.startsWith('~/') || out.startsWith('~\\')) {
    out = `${home.replace(/[/\\]+$/u, '')}${out[1]}${out.slice(2)}`
  }
  out = out.replace(/%([A-Za-z_][A-Za-z0-9_]*)%/gu, (match, name) => env[name] ?? match)
  out = out.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/gu, (match, name) => env[name] ?? match)
  out = out.replace(/\$([A-Za-z_][A-Za-z0-9_]*)/gu, (match, name) => env[name] ?? match)
  return out
}

/**
 * The directory the documented installers use, spelled for the platform.
 *
 * @param platform - `process.platform` value.
 * @returns a `~`-relative directory with native separators.
 */
export function defaultCliDir(platform) {
  return isWindows(platform) ? '~\\.local\\bin' : '~/.local/bin'
}

/**
 * The path the documented installers use when nothing overrides them.
 *
 * @param platform - `process.platform` value.
 * @returns a `~`-relative path with native separators.
 */
export function defaultCliPath(platform) {
  const name = `${CLI_BASENAME}${isWindows(platform) ? '.exe' : ''}`
  return joinCommand(platform, defaultCliDir(platform), name)
}

/**
 * Spellings of one command that Windows may actually have on disk.
 *
 * @param path - a command path, with or without an extension.
 * @param options.platform - `process.platform` value.
 * @param options.env - environment holding `PATHEXT`.
 * @returns candidate spellings, in probe order; a single spelling off Windows.
 */
export function commandSpellings(path, { platform = 'linux', env = {} } = {}) {
  if (!isWindows(platform)) return [path]
  const leaf = path.slice(Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')) + 1)
  if (leaf.includes('.')) return [path]
  const declared = (env.PATHEXT ?? WINDOWS_SUFFIXES.join(';'))
    .split(';')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
  const spellings = new Set([path])
  for (const extension of declared.length > 0 ? declared : WINDOWS_SUFFIXES) {
    spellings.add(`${path}${extension.toLowerCase()}`)
    spellings.add(`${path}${extension.toUpperCase()}`)
  }
  return [...spellings]
}

/**
 * Resolve the executable to report to the model and use in commands.
 *
 * Precedence: an explicitly configured path is honoured on its own, because a
 * wrong path should surface rather than be papered over. Anything else — a bare
 * configured name or no configuration at all — searches the installer directory
 * and then `PATH`, so a Windows install reachable only by name is still found.
 *
 * @param options.cli - configured value, when the row sets one.
 * @param options.platform - `process.platform` value.
 * @param options.env - environment (`PATH`, `PATHEXT`, variables to expand).
 * @param options.home - the current user's home directory.
 * @param options.isFile - probe returning whether a path is an existing file.
 * @returns `attempted` (the path to report) and `resolved` (a real file, or undefined).
 */
export function locateCli({ cli, platform = 'linux', env = {}, home = '', isFile } = {}) {
  const configured = typeof cli === 'string' && cli.length > 0
  const attempted = expandPath(configured ? cli : defaultCliPath(platform), { home, env })
  const probe = (path) => commandSpellings(path, { platform, env }).find((candidate) => isFile(candidate))

  if (configured && (attempted.includes('/') || attempted.includes('\\'))) {
    return { attempted, resolved: probe(attempted) }
  }

  const installerDir = expandPath(defaultCliDir(platform), { home, env })
  const searchDirs = (env.PATH ?? '')
    .split(searchDelimiter(platform))
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0)
  const lookups = [
    joinCommand(platform, installerDir, CLI_BASENAME),
    ...searchDirs.map((dir) => joinCommand(platform, dir, CLI_BASENAME)),
  ]

  for (const lookup of lookups) {
    const found = probe(lookup)
    if (found !== undefined) return { attempted, resolved: found }
  }
  return { attempted, resolved: undefined }
}

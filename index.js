/**
 * @tryao/dsh-wps365-plugin — ship the `wps365-docs` skill (WPS 365 cloud documents)
 * as a DeepSeek Harness bundle.
 *
 * The plugin registers one skill provider with `ctx.skills`, mirroring how the
 * shipped `@deepseek-ai/dsh-skill-office` package publishes the Office skills:
 * the catalog entry is built at activation, and the instruction body is read
 * from disk on every load so editing `assets/wps365-docs/SKILL.md` needs no
 * reload. The Python scripts are not executed by the Host process; the skill
 * instructions tell the model to run them through whatever shell the platform
 * provides (bash on macOS and Linux, PowerShell or cmd on Windows).
 *
 * Import-free by design: no package is resolved from a registry when the bundle
 * is installed, and `Config` is a hand-written Standard Schema v1 document.
 *
 * @module @tryao/dsh-wps365-plugin
 */
import { readFileSync, statSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { isAbsolute, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expandPath, locateCli } from './src/cli-locate.js'
import { Config, DEFAULT_CLI } from './src/plugin-config.js'
import {
  PROVIDER_NAME,
  SKILL_MODULES,
  SKILL_NAME,
  buildCandidate,
  buildRuntimeHint,
  parseSkillMarkdown,
} from './src/skill-catalog.js'

/** Cordis plugin identity for this bundle's row. */
export const name = 'skill-wps365-docs'

/** Required services: the plugin stays inactive without the skill registry. */
export const inject = ['skills']

export { Config }

function requireFile(path, subject) {
  let stats
  try {
    stats = statSync(path)
  } catch {
    throw new Error(`${PROVIDER_NAME}: ${subject} is missing: ${path}`)
  }
  if (!stats.isFile()) throw new Error(`${PROVIDER_NAME}: ${subject} is not a file: ${path}`)
}

/** Existence probe handed to the CLI locator; never throws for a missing path. */
function isFile(path) {
  try {
    return statSync(path).isFile()
  } catch {
    return false
  }
}

/**
 * Resolve and validate the skill assets for this activation.
 *
 * @param config - the row's config, already normalized by `Config`.
 * @returns absolute asset root, SKILL.md path, parsed metadata, and hint inputs.
 */
function resolveAssets(config) {
  const assetRoot = config.assetRoot ?? fileURLToPath(new URL('./assets/wps365-docs/', import.meta.url))
  if (!isAbsolute(assetRoot)) throw new Error(`${PROVIDER_NAME}: assetRoot must be an absolute directory: ${assetRoot}`)

  const skillPath = join(assetRoot, 'SKILL.md')
  requireFile(skillPath, 'SKILL.md')
  for (const script of SKILL_MODULES) requireFile(join(assetRoot, 'scripts', script), `scripts/${script}`)

  const parsed = parseSkillMarkdown(readFileSync(skillPath, 'utf8'), skillPath)
  if (parsed.name !== SKILL_NAME) {
    throw new Error(`${PROVIDER_NAME}: ${skillPath} declares name "${parsed.name}", expected "${SKILL_NAME}"`)
  }

  const cliValue = config.cli === false ? undefined : config.cli ?? DEFAULT_CLI
  const python = config.python === false || config.python === undefined
    ? undefined
    : expandPath(config.python, { home: homedir(), env: process.env })

  return { assetRoot, skillPath, parsed, cliValue, python }
}

/**
 * Register the bundled skill with the skill registry.
 *
 * @param ctx - context carrying the `skills` service.
 * @param config - normalized row config from `Config`.
 */
export function apply(ctx, config = {}) {
  const { assetRoot, skillPath, parsed, cliValue, python } = resolveAssets(config)

  // `resolved` is a real file (honouring PATHEXT on Windows); when nothing is
  // found the attempted path is still reported so the model can see where the
  // CLI was expected.
  const { attempted, resolved } = cliValue === undefined
    ? { attempted: undefined, resolved: undefined }
    : locateCli({ cli: cliValue, platform: process.platform, env: process.env, home: homedir(), isFile })
  const cli = resolved ?? attempted
  const cliExists = resolved !== undefined

  const runtimeHint = buildRuntimeHint({
    skillDir: assetRoot,
    cli,
    cliExists,
    python,
    platform: process.platform,
    tmpDir: tmpdir(),
  })
  const candidate = buildCandidate({ assetRoot, locator: skillPath, description: parsed.description })

  const provider = {
    name: PROVIDER_NAME,
    /** @returns the single catalog entry this bundle contributes. */
    list() {
      return Promise.resolve([candidate])
    },
    /**
     * Read the current SKILL.md, so edits reach the next load without a reload.
     *
     * @param candidate - the catalog entry selected by the registry; its
     *   `locator` is the file this provider advertised.
     * @param options - registry load options; `signal` cancels the read.
     * @returns the full skill definition.
     */
    async get(candidate, options = {}) {
      const locator = candidate?.locator ?? skillPath
      const raw = await readFile(locator, { encoding: 'utf8', signal: options.signal })
      const current = parseSkillMarkdown(raw, locator)
      return {
        name: SKILL_NAME,
        description: current.description,
        invocation: { modelInvocable: true, userInvocable: true },
        provider: PROVIDER_NAME,
        source: 'bundled',
        resourceBase: { kind: 'directory', path: assetRoot },
        content: current.content + runtimeHint,
      }
    },
  }

  // The registry owns this registration through the calling context's layer, so
  // unloading the bundle disposes the provider with it.
  ctx.skills.registerProvider(() => provider)

  if (cli !== undefined && !cliExists) {
    ctx.logger?.warn?.(`${PROVIDER_NAME}: wps365-cli not found at ${cli}; the skill will report the missing CLI`)
  }
}

/**
 * Dependency-free configuration schema for the `dsh-wps365-plugin` bundle.
 *
 * The Cordis loader validates a plugin row's `config` through the Standard
 * Schema v1 interface (`Config['~standard'].validate`); that is the only
 * contract this bundle must satisfy. Implementing it by hand keeps the package
 * installable with zero runtime dependencies, so `install_bundle` never has to
 * resolve a package from a registry.
 *
 * Known consequence: DSH's Config inspector projects a JSON Schema only for
 * native Schemastery graphs (`isNativeConfigSchema` in `@deepseek-ai/dsh-app-boot`
 * checks `Symbol.for('schemastery')`, `type`, and `meta`). This hand-written
 * object fails that check, so the row is reported with `status: "unsupported"`
 * and no config form is rendered for it. The loader treats that as a report, not
 * an error: the row still mounts, `apply()` runs, and `~standard.validate`
 * performs the real validation. To change a field, override the row in the
 * profile's `cordis.patch.yml` (`CONFIG_JSON_SCHEMA` below is the same contract,
 * for humans and other tooling). Faking the Schemastery graph protocol was
 * rejected deliberately: it would couple the bundle to another package's
 * internals for a display-only benefit.
 *
 * @module dsh-wps365-plugin/config
 */

/** CLI location used when the row's `config.cli` is omitted. */
export const DEFAULT_CLI = '~/.local/bin/wps365-cli'

/** Human-readable JSON Schema for tooling that renders a plugin row's config. */
export const CONFIG_JSON_SCHEMA = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  title: 'dsh-wps365-plugin config',
  type: 'object',
  additionalProperties: false,
  properties: {
    assetRoot: {
      type: 'string',
      description:
        'Absolute directory holding SKILL.md and scripts/. Defaults to the assets/ directory shipped beside index.js; set it when a packaged application copies the assets elsewhere.',
    },
    cli: {
      anyOf: [{ type: 'string', minLength: 1 }, { const: false }],
      description:
        `Absolute or ~-relative path of the wps365-cli executable. Defaults to ${DEFAULT_CLI}. Pass false to omit the CLI line from the injected skill resources.`,
    },
    python: {
      anyOf: [{ type: 'string', minLength: 1 }, { const: false }],
      description:
        'Absolute or ~-relative Python interpreter that has openpyxl / python-docx available. Omit it to tell the model to use the interpreter returned by load_workspace_dependencies.',
    },
  },
}

/**
 * An issue raised while normalizing a plugin row's config.
 */
class ConfigIssue extends Error {
  /**
   * @param {string} message - what is wrong with the value.
   * @param {string[]} path - JSON path of the offending field.
   */
  constructor(message, path) {
    super(message)
    this.name = 'ConfigIssue'
    this.path = path
  }
}

function isPlainObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function readString(value, path) {
  if (typeof value !== 'string' || value.length === 0) {
    throw new ConfigIssue(`${path.join('.')} must be a non-empty string`, path)
  }
  if (value.includes('\0')) throw new ConfigIssue(`${path.join('.')} must not contain NUL`, path)
  if (value !== value.trim()) {
    throw new ConfigIssue(`${path.join('.')} must not have leading or trailing whitespace`, path)
  }
  return value
}

function readStringOrFalse(value, path) {
  if (value === false) return false
  return readString(value, path)
}

const FIELDS = {
  assetRoot: (value, path) => readString(value, path),
  cli: (value, path) => readStringOrFalse(value, path),
  python: (value, path) => readStringOrFalse(value, path),
}

/**
 * Normalize and validate one plugin row's `config` into the shape `apply()`
 * consumes. Unknown fields are rejected rather than ignored, so a typo in a
 * profile patch fails loudly at activation instead of silently doing nothing.
 *
 * @param raw - the row's `config`, or undefined when the row carries none.
 * @returns the normalized options; omitted fields stay `undefined`.
 * @throws {ConfigIssue} when the value is not a supported config object.
 */
export function normalizeConfig(raw) {
  if (raw === undefined || raw === null) return {}
  if (!isPlainObject(raw)) throw new ConfigIssue('config must be an object', [])
  const options = {}
  for (const key of Object.keys(raw)) {
    const read = FIELDS[key]
    if (read === undefined) throw new ConfigIssue(`unknown config field "${key}"`, [key])
    const value = raw[key]
    if (value === undefined || value === null) continue
    options[key] = read(value, [key])
  }
  return options
}

/**
 * Standard Schema v1 wrapper consumed by the Cordis loader.
 *
 * `toJSON()` returns a plain JSON Schema for surfaces that render a row's
 * config schema; it is not part of the Standard Schema contract.
 */
export const Config = {
  '~standard': {
    version: 1,
    vendor: 'dsh-wps365-plugin',
    /**
     * @param value - the raw row config.
     * @returns the normalized value, or the issues that reject it.
     */
    validate(value) {
      try {
        return { value: normalizeConfig(value) }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        const path = Array.isArray(error?.path) ? error.path : undefined
        return { issues: [path === undefined ? { message } : { message, path }] }
      }
    },
  },
  /** @returns the JSON Schema describing this plugin's config. */
  toJSON() {
    return CONFIG_JSON_SCHEMA
  },
}

/**
 * Pure helpers for the bundled `wps365-docs` skill.
 *
 * Everything here is dependency-free and side-effect-free so the catalog
 * contract can be unit-tested without a running Harness: `index.js` owns the
 * filesystem reads and the Cordis wiring.
 *
 * @module dsh-wps365-plugin/skill-catalog
 */

/** Skill name as it appears in the catalog and in `skill` tool calls. */
export const SKILL_NAME = 'wps365-docs'

/** Provider name reported by the skill registry. */
export const PROVIDER_NAME = 'dsh-wps365-docs'

/**
 * Rank shared with other packaged and bundled skill roots
 * (`BUNDLED_SKILL_RANK` of `@deepseek-ai/dsh-skill`). Within one layer a lower
 * rank wins, so a user skill under `<dshHome>/skills` (rank 400) overrides
 * this bundled copy.
 */
export const BUNDLED_SKILL_RANK = 600

/** Scripts the skill instructions invoke; every one must ship beside SKILL.md. */
export const SKILL_SCRIPTS = [
  'otl_fetch.py',
  'otl_write.py',
  'file_fetch.py',
  'file_write.py',
  'xlsx_edit.py',
]

const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/u

function unquote(value) {
  if (value.length >= 2) {
    const first = value[0]
    const last = value[value.length - 1]
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return value.slice(1, -1)
    }
  }
  return value
}

/**
 * Read the flat `key: value` pairs a skill's YAML frontmatter uses. The
 * bundled frontmatter only ever carries single-line scalars, so a folded or
 * block scalar is reported instead of being silently misread.
 *
 * @param block - the frontmatter body, without its `---` fences.
 * @param source - path used in error messages.
 * @returns the parsed string fields.
 */
export function parseFrontmatter(block, source) {
  const fields = {}
  for (const line of block.split(/\r?\n/u)) {
    const trimmed = line.trim()
    if (trimmed.length === 0 || trimmed.startsWith('#')) continue
    const match = /^([A-Za-z0-9_-]+):[ \t]*(.*)$/u.exec(line)
    if (match === null) continue
    const value = unquote(match[2].trim())
    if (value === '|' || value === '>' || value === '|-' || value === '>-' || value === '|+' || value === '>+') {
      throw new Error(`${PROVIDER_NAME}: ${source} uses a multi-line "${match[1]}" frontmatter value, which is not supported`)
    }
    fields[match[1]] = value
  }
  return fields
}

/**
 * Split one SKILL.md into its frontmatter metadata and its instruction body.
 *
 * @param raw - the whole file.
 * @param source - path used in error messages.
 * @returns the parsed name, description, and body.
 * @throws {Error} when the frontmatter or the required description is missing.
 */
export function parseSkillMarkdown(raw, source) {
  const match = FRONTMATTER.exec(raw)
  if (match === null) throw new Error(`${PROVIDER_NAME}: ${source} has no YAML frontmatter`)
  const fields = parseFrontmatter(match[1], source)
  const description = fields.description
  if (typeof description !== 'string' || description.length === 0) {
    throw new Error(`${PROVIDER_NAME}: ${source} has no description`)
  }
  return {
    name: fields.name,
    description,
    content: raw.slice(match[0].length).trim(),
  }
}

/**
 * Build the invocation-neutral catalog entry the skill registry advertises.
 *
 * @param options - asset root, the SKILL.md path, and the parsed description.
 * @returns the candidate entry passed to `skills.registerProvider()`.
 */
export function buildCandidate({ assetRoot, locator, description }) {
  return {
    name: SKILL_NAME,
    description,
    invocation: { modelInvocable: true, userInvocable: true },
    provider: PROVIDER_NAME,
    source: 'bundled',
    rank: BUNDLED_SKILL_RANK,
    resourceBase: { kind: 'directory', path: assetRoot },
    locator,
  }
}

/**
 * Expand a leading `~` in a configured path without touching the filesystem.
 *
 * @param value - configured or default path.
 * @param home - the current user's home directory.
 * @returns the expanded path.
 */
export function expandHome(value, home) {
  if (value === '~') return home
  if (value.startsWith('~/')) return `${home}/${value.slice(2)}`
  return value
}

/**
 * Render the environment block appended to the skill body at load time.
 *
 * The instruction body is written against a `{skillDir}` placeholder because
 * the plugin directory differs per installation (profile bundle, workspace
 * bundle, or a packaged application). Resolving that placeholder here keeps the
 * document itself location-independent while still giving the model absolute
 * paths it can paste into a shell call.
 *
 * @param options - resolved directories and binaries.
 * @param options.skillDir - absolute skill asset directory.
 * @param options.cli - absolute wps365-cli path, or undefined when disabled.
 * @param options.cliExists - whether that executable exists on disk.
 * @param options.python - absolute interpreter path, or undefined to defer to
 *   `load_workspace_dependencies`.
 * @returns the markdown block appended to the skill body.
 */
export function buildRuntimeHint({ skillDir, cli, cliExists, python }) {
  // `new URL('./assets/dir/', ...)` yields a trailing separator; strip it so the
  // documented `{skillDir}/scripts/...` form expands without a doubled slash.
  const dir = skillDir.replace(/[/\\]+$/, '') || skillDir
  const scriptsDir = `${dir}/scripts/`
  const lines = [
    '',
    '## 本机资源（由 dsh-wps365-plugin 注入）',
    '',
    `- \`{skillDir}\` = \`${dir}\``,
    `- 脚本目录 = \`${scriptsDir}\`（${SKILL_SCRIPTS.join('、')}）`,
  ]

  if (cli === undefined) {
    lines.push('- `wps365-cli`：本插件配置为不注入 CLI 路径，按文档里的默认值使用。')
  } else if (cliExists) {
    lines.push(`- \`wps365-cli\` = \`${cli}\`（脚本默认值可能指向别处，必要时显式传 \`--cli ${cli}\`）`)
  } else {
    lines.push(
      `- ⚠️ \`wps365-cli\` 未找到：\`${cli}\`。先安装并授权（\`wps365-cli config init\`、\`wps365-cli auth login --device\`），` +
        '再把 `--cli <实际路径>` 传给脚本；否则所有读写都会失败。',
    )
  }

  if (python === undefined) {
    lines.push(
      '- Python：用 `load_workspace_dependencies` 返回的 python 跑脚本（`file_fetch.py` / `xlsx_edit.py` 需要 openpyxl，系统 python3 通常没有）。',
    )
  } else {
    lines.push(`- Python = \`${python}\`（已带 openpyxl / python-docx，直接用这个解释器跑脚本）`)
  }

  lines.push('', '本文档命令里的 `{skillDir}` 一律替换成上面的绝对路径后再执行。', '')
  return lines.join('\n')
}

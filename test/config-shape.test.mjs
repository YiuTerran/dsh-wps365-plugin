/**
 * Guards the shape of the exported `Config` object.
 *
 * DSH's settings service selects the schemas it renders with a duck-type test
 * that a `toJSON()` method alone satisfies
 * (`@deepseek-ai/dsh-settings`, `schema(entry)`):
 *
 *     const schema = entry.fiber?.runtime?.Config
 *     return schema !== void 0 && 'toJSON' in schema ? schema : void 0
 *
 * Whatever that test returns is passed to `volatileForm()`, whose first
 * statement reads `schema.meta.volatile`. A hand-written config object has no
 * `meta`, so the read throws and `settings/describe` fails — which surfaces as
 * "desktop welcome: Web RPC failed" and stops the Desktop application from
 * opening at all. Version 0.1.0 shipped exactly that regression, so these tests
 * pin the duck-type contract rather than just the validation behaviour.
 *
 * Keep this file dependency-free: it imports the plugin's own module only.
 */
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { Config, CONFIG_JSON_SCHEMA, normalizeConfig } from '../src/plugin-config.js'

/** Mirror of `@deepseek-ai/dsh-settings`'s `schema(entry)` duck-type test. */
function settingsSchemaSelector(candidate) {
  return candidate !== undefined && 'toJSON' in candidate ? candidate : undefined
}

test('settings must not mistake this Config for a native schema', () => {
  assert.equal(
    settingsSchemaSelector(Config),
    undefined,
    'Config carries a field that makes dsh-settings treat it as a native schema; '
      + 'volatileForm() would then throw on schema.meta.volatile and break app startup',
  )
})

test('Config exposes no member of the Schemastery graph protocol', () => {
  assert.equal('toJSON' in Config, false, 'Config must not define toJSON()')
  assert.equal(Config.meta, undefined, 'Config must not define meta')
  assert.equal(Config.type, undefined, 'Config must not define type')
  assert.equal(Config.dict, undefined, 'Config must not define dict')
  assert.equal(Config.inner, undefined, 'Config must not define inner')
  assert.equal(Config.list, undefined, 'Config must not define list')
  assert.equal(Reflect.get(Config, Symbol.for('schemastery')), undefined)
})

test('Config still validates through Standard Schema v1', () => {
  assert.equal(Config['~standard'].version, 1)
  assert.deepEqual(Config['~standard'].validate(undefined), { value: {} })
  assert.deepEqual(Config['~standard'].validate({ cli: '/tmp/wps365-cli' }), {
    value: { cli: '/tmp/wps365-cli' },
  })
  const rejected = Config['~standard'].validate({ typo: 'x' })
  assert.equal(Array.isArray(rejected.issues), true)
  assert.match(rejected.issues[0].message, /unknown config field "typo"/)
})

test('the JSON Schema stays available without hanging it off Config', () => {
  assert.equal(CONFIG_JSON_SCHEMA.type, 'object')
  assert.deepEqual(Object.keys(CONFIG_JSON_SCHEMA.properties).sort(), ['assetRoot', 'cli', 'python'])
  assert.equal(normalizeConfig({ python: false }).python, false)
})

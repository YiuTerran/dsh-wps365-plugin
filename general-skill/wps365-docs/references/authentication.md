# Authentication and recovery

The access token normally lasts two hours. The CLI handles automatic refresh and
401 retry. The delegated refresh token has a nominal 365-day lifetime and rotates;
revocation or failed persistence can invalidate it earlier. An expiry of
`0001-01-01` means unknown, not expired.

Use the approved execution context described in `../SKILL.md` for credential
access. The scripts cannot elevate themselves. Never print tokens, credential
files, or signed download URLs in diagnostics.

## Inspect versus recover

```sh
"{python}" "{skillDir}/scripts/auth.py" status --cli "{cli}"
"{python}" "{skillDir}/scripts/auth.py" ensure --cli "{cli}"
```

`status` is read-only and never refreshes. `has_refresh: true` only means a token
is stored. `ensure` checks local validity, attempts delegated refresh as needed,
and may request device login if no usable refresh grant remains. A successful
business read is stronger evidence of document access than local status.

Document helpers use `_wps_cli.run_cli()` for credential preflight and bounded
recovery. Direct CLI calls do not use this wrapper. If a direct call reports an
expired/invalid token, `required delegated scopes`, or fallback to app identity,
run the following, then retry the original request once after success:

```sh
"{python}" "{skillDir}/scripts/auth.py" ensure --force-refresh --cli "{cli}"
```

Do not blindly retry a mutation that may already have committed; first inspect
the affected cloud content. A bare permission error or `403 invalid_scope` with
confirmed user identity may be a real scope restriction, not an expired token.

## Classify failures

| Signal | Meaning and action |
|---|---|
| `credential_store_blocked`, denied `keyring.key` access, token-save permission error | Local credential storage is blocked. Stop; use the host's approval mechanism or ask the user to run the command in a permitted terminal. Do not repeatedly log in under the same denied permissions. |
| `refresh_grant_rejected` / rejected or missing refresh grant | The server does not accept the stored refresh token, regardless of local expiry. Device login can recover after credential storage access is available. |
| `auth_refresh_failed` | Network, startup, timeout, server, or malformed-response failure. Diagnose that failure; do not assume expiry or start repeated login. |
| `stage=preflight` | The temporary credential-directory write probe failed before the refresh/login/business request. It is not proof of a server denial. |
| `stage=status/refresh/login/business` | Indicates where a later failure happened; use the actual error rather than the category alone. |

The persistence probe only creates a temporary empty file in the CLI-reported
configuration directory. It does not alter credentials, replace approval, or prove
that a system keychain will accept the eventual save. A server may rotate a token
before saving fails; after restoring access, a rejected old grant needs new login.

Honor existing `WPS365_NO_AUTO_LOGIN=1` or `WPS365_AUTO_LOGIN=0`: they disable
device-login prompts, not refresh. Do not set or unset these options for the user.
Do not introduce `WPS365_ACCESS_TOKEN` for normal operation; it bypasses stored
credentials and normal refresh handling.

If the user explicitly requests app-identity work, preserve that choice and do not
run delegated recovery. These document helpers are intended for delegated access;
use supported direct CLI operations for app identity and verify their scopes.
App identity cannot substitute for the user's document permissions.

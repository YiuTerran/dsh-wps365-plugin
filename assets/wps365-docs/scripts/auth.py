#!/usr/bin/env python3
"""Inspect or repair the delegated wps365-cli login.

The CLI refreshes its 2-hour access token automatically and keeps a 365-day
refresh token. When that refresh token is expired/revoked (or the stored token
is missing), the CLI cannot help silently: this helper runs
``wps365-cli auth login --device``, which opens the browser for the user, and
verifies that a delegated token is usable afterwards.

Usage:

    python3 auth.py status --cli ~/.local/bin/wps365-cli
    python3 auth.py ensure --cli ~/.local/bin/wps365-cli
    python3 auth.py ensure --json

``ensure`` is safe to run before a cloud operation: it exits 0 quickly when the
delegated token is valid, and only refreshes/re-authenticates when needed.
Set ``WPS365_NO_AUTO_LOGIN=1`` to forbid the browser prompt.
"""

from __future__ import annotations

import argparse
import json
import sys

from _wps_cli import (
    AUTH_LOGIN_TIMEOUT,
    AUTH_TIMEOUT,
    auth_status,
    ensure_auth,
    resolve,
    use_utf8_stdio,
)

_LABEL = "auth"


def _summary(status: dict) -> str:
    delegated = (status.get("delegated") or {})
    if not delegated.get("available"):
        return "delegated: not_found"
    return (
        "delegated: {status}, remaining={remaining}s, "
        "has_refresh={refresh}, refresh_expires={expires}"
    ).format(
        status=delegated.get("status"),
        remaining=delegated.get("remaining_seconds"),
        refresh=delegated.get("has_refresh"),
        expires=delegated.get("refresh_token_expires_at"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查或修复 wps365-cli 的用户授权（过期时自动打开浏览器登录）")
    parser.add_argument("action", nargs="?", choices=("ensure", "status"), default="ensure",
                        help="ensure=必要时刷新/登录（默认）；status=只查看")
    parser.add_argument("--cli", default=resolve(), help=f"wps365-cli 路径（默认 {resolve()}）")
    parser.add_argument("--timeout", type=int, default=AUTH_TIMEOUT,
                        help=f"auth status/refresh 超时秒数（默认 {AUTH_TIMEOUT}）")
    parser.add_argument("--login-timeout", type=int, default=AUTH_LOGIN_TIMEOUT,
                        help=f"设备码登录等待秒数（默认 {AUTH_LOGIN_TIMEOUT}）")
    parser.add_argument("--json", action="store_true", help="输出完整 auth status JSON")
    return parser


def main() -> None:
    use_utf8_stdio()
    args = build_parser().parse_args()
    if args.action == "status":
        status = auth_status(args.cli, args.timeout)
        if status is None:
            sys.exit(f"[{_LABEL}] 无法读取授权状态；请确认 CLI 可用：{args.cli} auth status")
        print(json.dumps(status, ensure_ascii=False, indent=2)
              if args.json else _summary(status))
        return

    if not ensure_auth(args.cli, _LABEL, args.timeout, args.login_timeout):
        sys.exit(f"[{_LABEL}] 自动授权未完成；请手动运行：{args.cli} auth login --device")
    status = auth_status(args.cli, args.timeout)
    if status is None:
        sys.exit(f"[{_LABEL}] 授权后无法读回状态；请运行 {args.cli} auth status 检查")
    print(json.dumps(status, ensure_ascii=False, indent=2)
          if args.json else "✅ " + _summary(status))


if __name__ == "__main__":
    main()

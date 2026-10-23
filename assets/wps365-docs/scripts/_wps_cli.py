"""Locate the wps365-cli executable, and run it without losing its output.

Shared by every script in this directory. The documented installers place the
binary in ``~/.local/bin``, but on Windows the file is ``wps365-cli.exe`` and a
bare name is not stat-able, so a hardcoded POSIX path is not enough: the lookup
below prefers ``shutil.which``, which honours ``PATHEXT`` on Windows and
``PATH`` everywhere.

Resolution order for the ``--cli`` default:

1. ``WPS365_INSTALL_DIR`` when the installer was told to use a custom directory;
2. ``~/.local/bin/wps365-cli`` (``.exe`` on Windows);
3. the first ``wps365-cli`` on ``PATH``.

An explicit ``--cli`` always wins and is expanded (``~``, ``$VAR`` on POSIX,
``%VAR%`` on Windows) before use.

The second job of this module is ``run_cli``: every script shells out to the CLI
through it, so the pipe is decoded as UTF-8 exactly once. ``text=True`` alone
decodes with the *locale* encoding, which is cp936 on a Chinese Windows install
while the CLI always writes UTF-8 — the reader thread then dies on the first
non-ASCII byte (a Chinese file name, an error message, ``--help``) and the
script sees ``stdout is None`` instead of either the payload or the error.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading

#: Command name shared by every platform; Windows appends its own extension.
BASENAME = "wps365-cli"

#: Encoding of every pipe the CLI writes to. Set explicitly: the platform
#: default is cp936 on a Chinese Windows, which cannot decode the CLI's UTF-8.
CLI_ENCODING = "utf-8"

#: How long ``auth status``/``auth refresh`` may take. These are local reads plus
#: one token endpoint call; a much larger value only delays the error message.
AUTH_TIMEOUT = 30

#: Device login waits for the user to approve in a browser. Keep this generous:
#: a slow login is better than a second browser window and a failed command.
AUTH_LOGIN_TIMEOUT = 300

#: One recovery attempt per process: a permission error must not open the
#: browser repeatedly, and a login that the user is still completing must not be
#: replaced by another device-code request.
_AUTH_RECOVERY_ATTEMPTED = False

#: Errors that mean "the delegated token is missing/expired", not "the user
#: lacks a scope". The latter must not trigger an unnecessary login.
_AUTH_ERROR_HINTS = (
    "accesstokenexpired",
    "access token expired",
    "access_token expired",
    "token expired",
    "invalid_token",
    "invalid_grant",
    "invalid refresh token",
    "refresh token expired",
    "token not found",
    "usernotlogin",
    "user not login",
    "not logged in",
    "not login",
    "未登录",
    "登录已过期",
    "授权已过期",
    "请重新授权",
    "重新登录",
    "required delegated scopes",
    "does not support app authorization",
    "delegated token",
    "auth login",
    "please login",
    "please log in",
    "authentication required",
    "unauthorized",
    "refresh token",
)


def is_windows(platform: str | None = None) -> bool:
    """Whether the given (or current) platform is Windows."""
    return (platform or sys.platform).startswith("win")


def default_dir(env: dict | None = None) -> str:
    """The directory the installers use when no override is set."""
    env = os.environ if env is None else env
    override = env.get("WPS365_INSTALL_DIR")
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), ".local", "bin")


def default_cli(platform: str | None = None, env: dict | None = None) -> str:
    """The path to report when nothing is found (platform correct, may not exist)."""
    name = f"{BASENAME}.exe" if is_windows(platform) else BASENAME
    return os.path.join(default_dir(env), name)


def expand(value: str) -> str:
    """Expand ``~`` and environment references in a configured path."""
    return os.path.expandvars(os.path.expanduser(value))


def use_utf8_stdio() -> None:
    """Force UTF-8 on this process's own stdout/stderr.

    The mirror image of ``run_cli``: the *incoming* pipe is UTF-8, and so is what
    the scripts print — they embed ✅/⚠️/↳, and they echo file names and messages
    that come back from the server. On a Chinese Windows `sys.stdout` is cp936,
    so `print()`-ing any of those raises `UnicodeEncodeError`. That is worst when
    it lands late in a run: the script has already uploaded the document and then
    dies on a summary line, which reads as "the write failed" and invites a
    duplicate upload.

    ``errors="replace"`` keeps output flowing even for a glyph the target cannot
    represent, because a mangled character beats a crashed script.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def locate_cli(value: str | None = None, platform: str | None = None, env: dict | None = None) -> str | None:
    """Return a usable CLI path, or ``None`` when the CLI is not installed.

    An explicit ``value`` that is not an existing file falls back to a ``PATH``
    lookup of that name, so a bare ``--cli wps365-cli`` works on Windows.
    """
    env = os.environ if env is None else env
    if value:
        expanded = expand(value)
        if os.path.isfile(expanded):
            return expanded
        return shutil.which(expanded, path=env.get("PATH"))
    candidate = default_cli(platform, env)
    if os.path.isfile(candidate):
        return candidate
    return shutil.which(BASENAME, path=env.get("PATH"))


def resolve(value: str | None = None, platform: str | None = None, env: dict | None = None) -> str:
    """The path to advertise as the ``--cli`` default.

    Returns the located executable when there is one, otherwise the platform
    default so the failure message names the path the user should install to.
    """
    if value:
        return expand(value)
    return locate_cli(platform=platform, env=env) or default_cli(platform, env)


def _invoke(cmd: list[str], timeout: int):
    """Run one CLI process with the one correct pipe encoding."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=max(1, timeout),
                          encoding=CLI_ENCODING, errors="replace")


def _auto_login_disabled() -> bool:
    """Respect an explicit opt-out; useful in CI and unattended jobs."""
    if os.environ.get("WPS365_NO_AUTO_LOGIN", "").lower() in ("1", "true", "yes", "on"):
        return True
    return os.environ.get("WPS365_AUTO_LOGIN", "").lower() in ("0", "false", "no", "off")


def _uses_app_identity(cmd: list[str]) -> bool:
    """An explicit app-token call must not open a delegated browser login."""
    token_type = None
    for index, value in enumerate(cmd):
        if value == "--token-type" and index + 1 < len(cmd):
            token_type = cmd[index + 1]
        elif value.startswith("--token-type="):
            token_type = value.split("=", 1)[1]
    # Explicit flags override the default identity configured in the environment.
    if token_type is None:
        token_type = os.environ.get("WPS365_AUTH", "")
    return token_type.strip().lower() == "app"


def _looks_like_auth_error(detail: str) -> bool:
    """Whether a CLI failure is worth one delegated-auth recovery attempt."""
    text = (detail or "").lower()
    if any(hint in text for hint in _AUTH_ERROR_HINTS):
        return True
    return re.search(r"(?<!\d)401(?!\d)", text) is not None


def _check_credential_store(detail: str, label: str = "auth") -> None:
    """Stop recovery when local credential access, rather than OAuth, is denied."""
    text = detail.lower()
    denied = any(hint in text for hint in (
        "operation not permitted", "permission denied", "access is denied",
        "access denied", "read-only file system", "拒绝访问",
    ))
    store = any(hint in text for hint in (
        "keyring", "credential store", "credential file", "credentials.json",
        "save credential", "load credential", "save delegated token", "save app token",
        "load delegated token", "load app token", "token store",
        "wps365-cli/", "wps365-cli\\",
    ))
    if denied and store:
        sys.exit(
            f"[{label}] credential_store_blocked: 本地凭据读写被拒绝（沙盒或目录权限），"
            "不能据此判断 token 过期，也不能确认新 token 已保存。"
            "请申请所需沙盒外执行权限后重跑认证恢复命令，并用 auth status 验证；"
            "若宿主不支持提权，请在本机终端完成认证。"
            "刷新可能已轮换服务端 token，保存失败后若出现 invalid_grant，需重新登录。"
        )


def auth_status(cli: str, timeout: int = AUTH_TIMEOUT) -> dict | None:
    """Return ``wps365-cli auth status -o json``, or ``None`` if unavailable."""
    try:
        proc = _invoke([cli, "auth", "status", "-o", "json"], timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    _check_credential_store((proc.stderr or "") + "\n" + (proc.stdout or ""))
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _delegated(status: dict | None) -> dict:
    return (status or {}).get("delegated") or {}


def _delegated_usable(status: dict | None) -> bool:
    delegated = _delegated(status)
    return bool(delegated.get("available")) and delegated.get("status") == "valid"


def refresh_expiry(status: dict | None) -> datetime.datetime | None:
    """The parsed refresh-token expiry, or ``None`` when it is unknown.

    A fresh device login commonly returns ``refresh_token_expires_at`` as
    ``0001-01-01T00:00:00Z``: that is Go's zero time, meaning the response did
    not carry an expiry, **not** that the refresh token has expired. The same
    applies to a missing or unparseable value. Callers must not treat this as a
    reason to log in; the refresh token may still work and rotate normally.
    """
    value = _delegated(status).get("refresh_token_expires_at")
    if not value:
        return None
    text = str(value).strip()
    if not text or text.startswith("0001-"):
        return None
    try:
        expires = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=datetime.timezone.utc)
    return expires


def refresh_expiry_label(status: dict | None) -> str:
    """Human-readable refresh expiry; never presents Go's zero time as a date."""
    expires = refresh_expiry(status)
    if expires is None:
        return "未知（登录响应未提供有效期，不代表已过期）"
    now = datetime.datetime.now(datetime.timezone.utc)
    if expires <= now:
        return f"已过期（{expires.isoformat()}）"
    return expires.isoformat()


def _refresh_expired(status: dict | None) -> bool:
    """Whether the stored refresh token is past expiry (or within 5 minutes)."""
    expires = refresh_expiry(status)
    if expires is None:
        return False
    return expires <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)


def _refresh_delegated(cli: str, timeout: int = AUTH_TIMEOUT) -> bool:
    """Only a rejected/missing grant can fall back to browser login."""
    try:
        proc = _invoke([cli, "auth", "refresh", "--delegated", "-o", "json"], timeout)
    except (OSError, subprocess.SubprocessError):
        sys.exit("[auth] auth_refresh_failed: 无法完成刷新命令（启动或超时错误）；"
                 "尚不能判断 token 是否失效，请检查 CLI/网络后重试。")
    detail = (proc.stderr or "") + "\n" + (proc.stdout or "")
    _check_credential_store(detail)
    if proc.returncode != 0:
        if any(hint in detail.lower() for hint in (
            "invalid_grant", "invalid refresh token", "refresh token expired",
            "refresh token not found", "token not found",
            "no delegated token", "delegated token not found",
        )):
            print("[auth] refresh_grant_rejected: 服务端拒绝或找不到 refresh token；"
                  "本地记录未到期也不代表它仍有效。", file=sys.stderr, flush=True)
            return False
        sys.exit("[auth] auth_refresh_failed: 刷新未成功，且未确认 refresh token 失效；"
                 "请检查 CLI/网络/服务端状态后重试，不自动发起设备码登录。")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        sys.exit("[auth] auth_refresh_failed: 刷新响应不是有效 JSON，无法确认结果；"
                 "请检查 auth status 后重试。")
    delegated = data.get("delegated") if isinstance(data, dict) else None
    if not isinstance(delegated, dict) or not isinstance(delegated.get("refreshed"), bool):
        sys.exit("[auth] auth_refresh_failed: 刷新响应缺少 delegated.refreshed 布尔值；"
                 "请检查 auth status 后重试。")
    return delegated["refreshed"]


def _stream_login(cmd: list[str], timeout: int) -> tuple[int, str]:
    """Keep device instructions live while retaining a bounded output tail."""
    tail = ""
    with subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding=CLI_ENCODING, errors="replace") as proc:
        def relay() -> None:
            nonlocal tail
            for line in proc.stdout:
                tail = (tail + line)[-8192:]
                sys.stderr.write(line)
                sys.stderr.flush()

        reader = threading.Thread(target=relay, daemon=True)
        reader.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        finally:
            reader.join()
    return proc.returncode, tail


def _login_delegated(cli: str, label: str, timeout: int = AUTH_LOGIN_TIMEOUT) -> bool:
    """Run the device flow. The CLI opens the browser itself when possible."""
    if _auto_login_disabled():
        print(f"[{label}] 自动授权已关闭（WPS365_NO_AUTO_LOGIN / WPS365_AUTO_LOGIN）；"
              f"请手动运行：{cli} auth login --device", file=sys.stderr)
        return False
    if os.environ.get("WPS365_ACCESS_TOKEN"):
        print(f"[{label}] 当前通过 WPS365_ACCESS_TOKEN 注入 access token，"
              "无法用设备码登录覆盖；请更新或取消这个环境变量。", file=sys.stderr)
        return False
    print(f"[{label}] 用户授权缺失或已过期，正在运行 {cli} auth login --device 打开浏览器；"
          "请在浏览器中确认授权（若没有自动打开，按终端提示访问 URL）。",
          file=sys.stderr, flush=True)
    try:
        returncode, detail = _stream_login([cli, "auth", "login", "--device"], timeout)
    except subprocess.TimeoutExpired:
        print(f"[{label}] 设备码授权等待超时（{timeout}s），请手动运行 "
              f"{cli} auth login --device", file=sys.stderr)
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[{label}] 无法启动设备码授权：{exc}", file=sys.stderr)
        return False
    _check_credential_store(detail, label)
    if returncode != 0:
        print(f"[{label}] 设备码授权失败（退出码 {returncode}）；"
              "请查看上面的 CLI 提示。", file=sys.stderr)
        return False
    return True


def ensure_auth(cli: str, label: str = "wps365", timeout: int = AUTH_TIMEOUT,
                login_timeout: int = AUTH_LOGIN_TIMEOUT, *,
                force_refresh: bool = False) -> bool:
    """Make the delegated token usable, refreshing or logging in as needed.

    The CLI already refreshes an access token ten seconds before it expires and
    retries a 401 once. This fallback covers the cases the CLI deliberately does
    not handle silently: the refresh token is expired/revoked, the stored token
    is missing, or the CLI fell back to the app identity and the API rejected it.

    Returns ``True`` when a delegated token is usable afterwards. It never loops:
    at most one refresh and one device login are attempted.
    ``force_refresh`` bypasses the local-validity shortcut after an API auth
    failure: a token can be revoked remotely before its stored expiry time.
    """
    status = auth_status(cli, timeout)
    if status is None:
        print(f"[{label}] 无法读取授权状态（{cli} auth status）；"
              f"请检查 CLI 是否可用，或手动运行 {cli} auth login --device", file=sys.stderr)
        return False

    if status.get("env_access_token"):
        print(f"[{label}] 当前通过 WPS365_ACCESS_TOKEN 注入 access token；"
              "这个变量会绕过本地存储和自动刷新，无法用设备码登录修复。"
              "请更新/取消该变量，或改回正常的 auth login --device。",
              file=sys.stderr)
        return False

    delegated = _delegated(status)
    refresh_dead = _refresh_expired(status)
    if not force_refresh and _delegated_usable(status) \
            and delegated.get("has_refresh") and not refresh_dead:
        # A valid access token with a live refresh token needs no help here; the
        # CLI will rotate it on its own. This shortcut is only for proactive
        # checks; run_cli forces recovery after the API rejects the identity.
        return True

    if delegated.get("available") and delegated.get("has_refresh") \
            and _refresh_delegated(cli, timeout):
        status = auth_status(cli, timeout)
        if _delegated_usable(status) and _delegated(status).get("has_refresh") \
                and not _refresh_expired(status):
            print(f"[{label}] 已自动刷新用户 token。", file=sys.stderr, flush=True)
            return True
        print(f"[{label}] refresh 返回成功但授权仍不可用，继续尝试设备码登录。",
              file=sys.stderr, flush=True)
    else:
        print(f"[{label}] 未恢复用户 token（授权被拒绝或无可刷新凭据），"
              "改用设备码登录。", file=sys.stderr, flush=True)

    if not _login_delegated(cli, label, login_timeout):
        return False
    status = auth_status(cli, timeout)
    if _delegated_usable(status):
        if not _delegated(status).get("has_refresh"):
            print(f"[{label}] 设备码授权成功，但没有拿到 refresh token；"
                  "CLI 版本或应用配置可能导致 2 小时后再次过期，请运行 "
                  f"{cli} auth status -o json 检查 delegated.has_refresh。",
                  file=sys.stderr, flush=True)
        print(f"[{label}] 设备码授权完成，已恢复用户身份。", file=sys.stderr, flush=True)
        return True
    print(f"[{label}] 设备码登录返回成功，但 auth status 仍无可用 delegated token；"
          f"请运行 {cli} auth status 检查。", file=sys.stderr)
    return False


def run_cli(cmd: list[str], timeout: int, label: str, what: str) -> str:
    """Run a CLI command and return its stdout, or exit with its error.

    Output is decoded as UTF-8 with replacement, so one stray byte in a file
    name cannot take the whole pipe — and the error message — down with it.
    A non-zero exit is reported through ``label`` (the calling script's tag) and
    exits the process, which is what every caller wants: there is no recovery
    from a failed cloud call here.

    One exception is authentication: when the failure looks like a missing or
    expired delegated token (including the CLI's app-mode fallback 403), refresh
    it, or run the device login that opens the browser, then retry exactly once.
    A real permission error is not retried blindly; ``WPS365_NO_AUTO_LOGIN=1``
    opts out of the browser prompt.

    :param cmd: full argv, starting with the CLI path.
    :param timeout: seconds for the whole call.
    :param label: script tag used in the error message, e.g. ``file_fetch``.
    :param what: short description of the call, e.g. ``GET /v7/files``.
    :returns: the CLI's stdout, decoded.
    """
    global _AUTH_RECOVERY_ATTEMPTED
    proc = _invoke(cmd, timeout)
    _check_credential_store((proc.stderr or "") + "\n" + (proc.stdout or ""), label)
    detail = (proc.stderr or proc.stdout or "").strip()[:600]
    if proc.returncode != 0 and not _uses_app_identity(cmd) \
            and _looks_like_auth_error(detail) and not _AUTH_RECOVERY_ATTEMPTED:
        _AUTH_RECOVERY_ATTEMPTED = True
        if ensure_auth(cmd[0], label=label, force_refresh=True):
            proc = _invoke(cmd, timeout)
            _check_credential_store((proc.stderr or "") + "\n" + (proc.stdout or ""), label)
            detail = (proc.stderr or proc.stdout or "").strip()[:600]
    if proc.returncode != 0:
        sys.exit(f"[{label}] {what} 失败（退出码 {proc.returncode}）：{detail}")
    return proc.stdout or ""

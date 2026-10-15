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

import os
import shutil
import subprocess
import sys

#: Command name shared by every platform; Windows appends its own extension.
BASENAME = "wps365-cli"

#: Encoding of every pipe the CLI writes to. Set explicitly: the platform
#: default is cp936 on a Chinese Windows, which cannot decode the CLI's UTF-8.
CLI_ENCODING = "utf-8"


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


def run_cli(cmd: list[str], timeout: int, label: str, what: str) -> str:
    """Run a CLI command and return its stdout, or exit with its error.

    Output is decoded as UTF-8 with replacement, so one stray byte in a file
    name cannot take the whole pipe — and the error message — down with it.
    A non-zero exit is reported through ``label`` (the calling script's tag) and
    exits the process, which is what every caller wants: there is no recovery
    from a failed cloud call here.

    :param cmd: full argv, starting with the CLI path.
    :param timeout: seconds for the whole call.
    :param label: script tag used in the error message, e.g. ``file_fetch``.
    :param what: short description of the call, e.g. ``GET /v7/files``.
    :returns: the CLI's stdout, decoded.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding=CLI_ENCODING, errors="replace")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:600]
        sys.exit(f"[{label}] {what} 失败（退出码 {proc.returncode}）：{detail}")
    return proc.stdout or ""

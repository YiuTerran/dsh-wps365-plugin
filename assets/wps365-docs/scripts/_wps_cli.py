"""Locate the wps365-cli executable on this machine.

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
"""

from __future__ import annotations

import os
import shutil
import sys

#: Command name shared by every platform; Windows appends its own extension.
BASENAME = "wps365-cli"


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

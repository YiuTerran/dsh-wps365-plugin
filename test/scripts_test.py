#!/usr/bin/env python3
"""Contract tests for the shipped scripts: CLI discovery, pipes and stdio.

Run with any Python 3.9+:

    python3 test/scripts_test.py

These are the failures a POSIX-only CI could not see:

* Windows resolves an executable by extension, so a bare `…/wps365-cli` is not a
  file on a machine whose install is `wps365-cli.exe`.
* `subprocess.run(text=True)` decodes with the *locale* encoding, which is cp936
  on a Chinese Windows while the CLI always writes UTF-8 — the reader thread dies
  on the first non-ASCII byte and the caller sees `stdout is None`, so both the
  payload and the error message are lost.
* The same codec governs the scripts' *own* stdout: printing ✅/⚠️/↳ raises
  `UnicodeEncodeError`, and when that lands on the last line of a run the
  document has already been uploaded.

The environment, platform and filesystem are injected, so all of it is covered
on any host.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "wps365-docs", "scripts")
sys.path.insert(0, SCRIPTS)

import _wps_cli  # noqa: E402
import file_fetch  # noqa: E402
import xlsx_edit  # noqa: E402


class DefaultCliTest(unittest.TestCase):
    def test_windows_default_names_the_exe(self):
        path = _wps_cli.default_cli("win32", env={})
        # The injected platform selects the executable name; path separators
        # still follow the host running this test.
        self.assertEqual(path, os.path.join(_wps_cli.default_dir({}), "wps365-cli.exe"))

    def test_posix_default_stays_bare(self):
        path = _wps_cli.default_cli("linux", env={})
        self.assertTrue(path.endswith("wps365-cli"), path)
        self.assertFalse(path.endswith(".exe"), path)
        # The directory always follows the host's own join rule; only the leaf
        # name is platform-dependent, which is what the tests above pin down.
        self.assertEqual(path, os.path.join(_wps_cli.default_dir({}), "wps365-cli"))

    def test_install_dir_override_wins(self):
        path = _wps_cli.default_cli("linux", env={"WPS365_INSTALL_DIR": "/opt/wps"})
        # The separator follows the host, not the injected platform: `default_cli`
        # only spells the path, it does not pretend to run on another system.
        self.assertEqual(path, os.path.join("/opt/wps", "wps365-cli"))


class LocateCliTest(unittest.TestCase):
    def test_bare_name_falls_back_to_a_path_lookup(self):
        # `--cli wps365-cli` must work on Windows, where `shutil.which` applies
        # PATHEXT. The lookup itself is stubbed so the assertion is about the
        # contract rather than about what this machine happens to have installed.
        with mock.patch.object(_wps_cli.shutil, "which", return_value=r"C:\tools\wps365-cli.exe") as which:
            found = _wps_cli.locate_cli("wps365-cli", platform="win32", env={"PATH": r"C:\tools"})
        self.assertEqual(found, r"C:\tools\wps365-cli.exe")
        which.assert_called_once_with("wps365-cli", path=r"C:\tools")

    def test_explicit_path_is_honoured_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = os.path.join(tmp, "wps365-cli")
            with open(cli, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\n")
            self.assertEqual(_wps_cli.locate_cli(cli, platform="linux", env={}), cli)

    def test_missing_explicit_path_is_not_silently_replaced(self):
        # A typo must surface: with nothing else on PATH the answer is None.
        missing = os.path.join(tempfile.gettempdir(), "definitely-not-wps365-cli")
        found = _wps_cli.locate_cli(missing, platform="linux", env={"PATH": ""})
        self.assertIsNone(found)

    def test_resolve_reports_the_platform_default_when_absent(self):
        env = {"PATH": "", "WPS365_INSTALL_DIR": os.path.join(tempfile.gettempdir(), "no-cli")}
        reported = _wps_cli.resolve(platform="win32", env=env)
        self.assertTrue(reported.endswith("wps365-cli.exe"), reported)


class RunCliTest(unittest.TestCase):
    """The pipe must be decoded as UTF-8 regardless of the host locale."""

    def _capture_call(self, **kwargs):
        recorded = {}

        def fake_run(cmd, **options):
            recorded.update(options)
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(_wps_cli.subprocess, "run", side_effect=fake_run):
            out = _wps_cli.run_cli(["cli", "auth", "token"], 30, "file_write", "auth token")
        return out, recorded

    def test_pipes_are_decoded_as_utf8_with_replacement(self):
        out, options = self._capture_call()
        self.assertEqual(out, "ok")
        self.assertEqual(options.get("encoding"), "utf-8")
        self.assertEqual(options.get("errors"), "replace")
        self.assertTrue(options.get("text"))

    def test_nonzero_exit_reports_the_tag_and_the_detail(self):
        def fake_run(cmd, **options):
            return mock.Mock(returncode=1, stdout="", stderr="boom")

        with mock.patch.object(_wps_cli.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(SystemExit) as caught:
                _wps_cli.run_cli(["cli", "api"], 30, "otl_fetch", "GET /v7/files")
        message = str(caught.exception)
        self.assertIn("[otl_fetch]", message)
        self.assertIn("GET /v7/files", message)
        self.assertIn("boom", message)

    def test_none_stdout_cannot_reach_the_caller(self):
        # Before the fix a decode failure produced stdout=None and the caller
        # crashed on the TypeError instead of reporting the CLI's own error.
        def fake_run(cmd, **options):
            return mock.Mock(returncode=0, stdout=None, stderr=None)

        with mock.patch.object(_wps_cli.subprocess, "run", side_effect=fake_run):
            self.assertEqual(_wps_cli.run_cli(["cli"], 30, "file_fetch", "x"), "")


class StdoutEncodingTest(unittest.TestCase):
    """The scripts' own output must survive a cp936 console."""

    def test_stdout_never_dies_on_a_non_encodable_glyph(self):
        # The real failure: every script prints ✅/⚠️/↳, which cp936 cannot encode,
        # so a late summary line killed a run that had already uploaded the file.
        script = (
            "import sys; sys.path.insert(0, r'{0}'); import _wps_cli; "
            "_wps_cli.use_utf8_stdio(); print('\u2705 \u26a0\ufe0f \u21b3 \u2022 \u4e2d\u6587\u540d.xlsx')"
        ).format(SCRIPTS)
        env = dict(os.environ, PYTHONIOENCODING="gbk")
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn("中文名.xlsx".encode("utf-8"), proc.stdout)

    def test_every_entry_script_bootstraps_the_stdio(self):
        missing = []
        for name in ("otl_fetch.py", "otl_write.py", "file_fetch.py",
                     "file_write.py", "xlsx_edit.py"):
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                if "use_utf8_stdio" not in fh.read():
                    missing.append(name)
        self.assertEqual(missing, [], "these scripts print non-cp936 glyphs without the bootstrap")


class LegacyXlsGuardTest(unittest.TestCase):
    """A .xlsx name over an OLE2/.xls body must be explained, not crash."""

    def test_ole2_file_reports_the_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "original.xlsx")
            with open(path, "wb") as fh:
                fh.write(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64)
            with mock.patch.dict(sys.modules, {"openpyxl": None, "openpyxl.utils": None}):
                with self.assertRaises(SystemExit) as caught:
                    file_fetch.xlsx_read_local(path, tmp, 50, 20, False)
        message = str(caught.exception)
        self.assertIn("OLE2", message)
        self.assertIn("--via-api", message)

    def test_non_ole2_file_keeps_the_missing_dependency_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "valid.xlsx")
            with open(path, "wb") as fh:
                fh.write(b"PK\x03\x04" + b"\x00" * 64)
            with mock.patch.dict(sys.modules, {"openpyxl": None, "openpyxl.utils": None}):
                with self.assertRaises(SystemExit) as caught:
                    file_fetch.xlsx_read_local(path, tmp, 50, 20, False)
        message = str(caught.exception)
        self.assertIn("需要 openpyxl", message)
        self.assertIn("--via-api", message)


class EveryScriptSharesTheHelper(unittest.TestCase):
    #: Scripts that talk to wps365-cli and must therefore go through `run_cli`.
    CLI_CALLERS = ("otl_fetch.py", "otl_write.py", "file_fetch.py", "file_write.py")

    def test_cli_callers_do_not_call_subprocess_directly(self):
        offenders = []
        for name in self.CLI_CALLERS:
            with open(os.path.join(SCRIPTS, name), encoding="utf-8") as fh:
                body = fh.read()
            if "subprocess.run(" in body:
                offenders.append(f"{name} (raw subprocess.run)")
            if "_run_cli" not in body:
                offenders.append(f"{name} (no _run_cli)")
        self.assertEqual(offenders, [])

    def test_xlsx_edit_decodes_its_recalculate_pipe(self):
        # This one shells out to the LibreOffice kit rather than the CLI, so it
        # keeps its own call — but it must still pin the encoding.
        with open(os.path.join(SCRIPTS, "xlsx_edit.py"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("subprocess.run(", body)
        self.assertIn("encoding=CLI_ENCODING", body)


class FindKitTest(unittest.TestCase):
    """Kit discovery must not be tied to a macOS application path."""

    def _tree(self, tmp: str, node: bool = True, cli: bool = True) -> dict:
        paths = set()
        node_path = os.path.join(tmp, "resources", *xlsx_edit.KIT_NODE_RELATIVE[:-1], "node.exe")
        cli_path = os.path.join(tmp, "resources", *xlsx_edit.KIT_CLI_RELATIVE)
        if node:
            os.makedirs(os.path.dirname(node_path), exist_ok=True)
            open(node_path, "w", encoding="utf-8").close()
            paths.add(node_path)
        if cli:
            os.makedirs(os.path.dirname(cli_path), exist_ok=True)
            open(cli_path, "w", encoding="utf-8").close()
            paths.add(cli_path)
        return {"node": node_path, "cli": cli_path, "paths": paths}

    def test_finds_kit_beside_a_windows_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = self._tree(tmp)
            with mock.patch.dict(os.environ, {"DSH_APP_ROOT": os.path.join(tmp, "resources")},
                                 clear=False):
                for key in ("DSH_NODE", "DSH_LIBREOFFICE_CLI"):
                    os.environ.pop(key, None)
                node, cli = xlsx_edit.find_kit(None, None)
            self.assertEqual(cli, expected["cli"])
            # `node.exe` only exists on Windows; elsewhere the search must still
            # return a real interpreter rather than None.
            self.assertTrue(node and os.path.isfile(node), node)

    def test_explicit_arguments_win_over_discovery(self):
        node, cli = xlsx_edit.find_kit("/chosen/node", "/chosen/cli.js")
        self.assertEqual((node, cli), ("/chosen/node", "/chosen/cli.js"))

    def test_environment_overrides_are_used(self):
        with mock.patch.dict(os.environ, {"DSH_NODE": "/env/node",
                                          "DSH_LIBREOFFICE_CLI": "/env/cli.js"}, clear=False):
            node, cli = xlsx_edit.find_kit(None, None)
        self.assertEqual((node, cli), ("/env/node", "/env/cli.js"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

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

import contextlib
import io
import json
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
import auth as wps_auth  # noqa: E402
import file_fetch  # noqa: E402
import otl_fetch  # noqa: E402
import otl_sync  # noqa: E402
import otl_write  # noqa: E402
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


class AuthRecoveryTest(unittest.TestCase):
    """The 2-hour access token is refreshed by the CLI; when the 365-day refresh
    token itself is gone, the scripts must open the device login once and retry."""

    def setUp(self):
        _wps_cli._AUTH_RECOVERY_ATTEMPTED = False
        env = mock.patch.dict(os.environ, {"WPS365_AUTH": "delegated"})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        _wps_cli._AUTH_RECOVERY_ATTEMPTED = False

    @staticmethod
    def _status(state="expired", has_refresh=True):
        return {
            "delegated": {
                "available": state != "not_found",
                "status": state,
                "remaining_seconds": 0 if state != "valid" else 7000,
                "has_refresh": has_refresh,
            }
        }

    def _run(self, replies, statuses, refresh=False, login=False, cmd=None):
        calls = []

        def fake_run(cmd, **options):
            calls.append(cmd)
            return replies.pop(0)

        with mock.patch.object(_wps_cli.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(_wps_cli, "auth_status", return_value=None,
                                  side_effect=([statuses[0], *statuses] if statuses else None)), \
                mock.patch.object(_wps_cli, "_refresh_delegated", return_value=refresh) as refresh_mock, \
                mock.patch.object(_wps_cli, "_login_delegated", return_value=login) as login_mock:
            try:
                return _wps_cli.run_cli(cmd or ["cli", "api", "get", "/x"], 30, "test", "GET /x"), calls, refresh_mock, login_mock
            except SystemExit as exc:
                return exc, calls, refresh_mock, login_mock

    def test_auth_failure_refreshes_then_retries(self):
        out, calls, refresh_mock, login_mock = self._run(
            [mock.Mock(returncode=1, stdout="", stderr="userNotLogin"),
             mock.Mock(returncode=0, stdout="ok", stderr="")],
            [self._status("expired"), self._status("valid")], refresh=True)
        self.assertEqual(out, "ok")
        refresh_mock.assert_called_once()
        login_mock.assert_not_called()
        self.assertEqual(len(calls), 2)

    def test_failed_refresh_falls_back_to_browser_login(self):
        out, calls, refresh_mock, login_mock = self._run(
            [mock.Mock(returncode=1, stdout="", stderr="access_token expired"),
             mock.Mock(returncode=0, stdout="ok", stderr="")],
            [self._status("expired"), self._status("valid")],
            refresh=False, login=True)
        self.assertEqual(out, "ok")
        refresh_mock.assert_called_once()
        login_mock.assert_called_once()
        self.assertEqual(len(calls), 2)

    def test_auth_failure_refreshes_even_when_local_token_is_valid(self):
        out, calls, refresh_mock, login_mock = self._run(
            [mock.Mock(returncode=1, stdout="", stderr="userNotLogin"),
             mock.Mock(returncode=0, stdout="ok", stderr="")],
            [self._status("valid"), self._status("valid")], refresh=True)
        self.assertEqual(out, "ok")
        refresh_mock.assert_called_once()
        login_mock.assert_not_called()
        self.assertEqual(len(calls), 2)

    def test_revoked_refresh_token_logs_in_despite_valid_local_expiry(self):
        out, calls, refresh_mock, login_mock = self._run(
            [mock.Mock(returncode=1, stdout="", stderr="invalid_grant: refresh token revoked"),
             mock.Mock(returncode=0, stdout="ok", stderr="")],
            [self._status("valid"), self._status("valid")], login=True)
        self.assertEqual(out, "ok")
        refresh_mock.assert_called_once()
        login_mock.assert_called_once()
        self.assertEqual(len(calls), 2)

    def test_app_environment_does_not_start_delegated_recovery(self):
        with mock.patch.dict(os.environ, {"WPS365_AUTH": "app"}):
            out, calls, refresh_mock, login_mock = self._run(
                [mock.Mock(returncode=1, stdout="", stderr="required delegated scopes")],
                [], cmd=["cli", "airpage", "export", "create", "123"])
        self.assertIsInstance(out, SystemExit)
        self.assertEqual(len(calls), 1)
        refresh_mock.assert_not_called()
        login_mock.assert_not_called()

    def test_delegated_flag_overrides_app_environment_for_recovery(self):
        with mock.patch.dict(os.environ, {"WPS365_AUTH": "app"}):
            out, calls, refresh_mock, login_mock = self._run(
                [mock.Mock(returncode=1, stdout="", stderr="userNotLogin"),
                 mock.Mock(returncode=0, stdout="ok", stderr="")],
                [self._status("expired"), self._status("valid")], refresh=True,
                cmd=["cli", "api", "get", "/x", "--token-type", "delegated"])
        self.assertEqual(out, "ok")
        self.assertEqual(len(calls), 2)
        refresh_mock.assert_called_once()
        login_mock.assert_not_called()

    def test_explicit_app_flags_do_not_start_delegated_recovery(self):
        with mock.patch.dict(os.environ, {"WPS365_AUTH": "delegated"}):
            for flag in (["--token-type", "app"], ["--token-type=app"]):
                with self.subTest(flag=flag):
                    out, calls, refresh_mock, login_mock = self._run(
                        [mock.Mock(returncode=1, stdout="", stderr="required delegated scopes")],
                        [], cmd=["cli", "api", "get", "/x", *flag])
                    self.assertIsInstance(out, SystemExit)
                    self.assertEqual(len(calls), 1)
                    refresh_mock.assert_not_called()
                    login_mock.assert_not_called()

    def test_equals_delegated_flag_overrides_app_environment(self):
        with mock.patch.dict(os.environ, {"WPS365_AUTH": "app"}):
            self.assertFalse(_wps_cli._uses_app_identity(
                ["cli", "api", "get", "/x", "--token-type=delegated"]))

    def test_permission_error_is_not_treated_as_expiry(self):
        out, calls, refresh_mock, login_mock = self._run(
            [mock.Mock(returncode=1, stdout="", stderr="boom")], [], refresh=False, login=False)
        self.assertIsInstance(out, SystemExit)
        self.assertIn("boom", str(out))
        refresh_mock.assert_not_called()
        login_mock.assert_not_called()

    def test_app_fallback_403_counts_as_an_auth_symptom(self):
        detail = '{"code":403000004,"message":"this API does not support app authorization, required delegated scopes: [kso.user_base.read]"}'
        self.assertTrue(_wps_cli._looks_like_auth_error(detail))
        self.assertFalse(_wps_cli._looks_like_auth_error("403000004 permission denied"))

    def test_app_fallback_invalid_scope_refreshes_with_login_disabled(self):
        detail = ("delegated token unavailable, using app identity\n"
                  "api request failed with status 403: invalid_scope "
                  "kso.file_link.readwrite has not been granted")
        with mock.patch.dict(os.environ, {"WPS365_NO_AUTO_LOGIN": "1"}):
            out, calls, refresh_mock, login_mock = self._run(
                [mock.Mock(returncode=1, stdout="", stderr=detail),
                 mock.Mock(returncode=0, stdout='{"file_id":"example"}', stderr="")],
                [self._status("expired"), self._status("valid")], refresh=True)
        self.assertEqual(out, '{"file_id":"example"}')
        self.assertEqual(len(calls), 2)
        refresh_mock.assert_called_once()
        login_mock.assert_not_called()

    def test_expired_refresh_token_wins_over_a_valid_access_token(self):
        expired = self._status("valid", True)
        expired["delegated"]["refresh_token_expires_at"] = "2020-01-01T00:00:00+08:00"
        fresh = self._status("valid", True)
        fresh["delegated"]["refresh_token_expires_at"] = "2099-01-01T00:00:00+08:00"
        with mock.patch.object(_wps_cli, "auth_status", side_effect=[expired, fresh]), \
                mock.patch.object(_wps_cli, "_refresh_delegated", return_value=False), \
                mock.patch.object(_wps_cli, "_login_delegated", return_value=True) as login_mock:
            self.assertTrue(_wps_cli.ensure_auth("cli"))
        login_mock.assert_called_once()

    def test_unknown_refresh_expiry_is_not_an_expiry(self):
        for value in ("0001-01-01T00:00:00Z", "", "not-a-date"):
            with self.subTest(value=value):
                status = self._status("valid", True)
                status["delegated"]["refresh_token_expires_at"] = value
                self.assertFalse(_wps_cli._refresh_expired(status))
                self.assertIn("未知", _wps_cli.refresh_expiry_label(status))
                with mock.patch.object(_wps_cli, "auth_status", return_value=status), \
                        mock.patch.object(_wps_cli, "_refresh_delegated") as refresh_mock, \
                        mock.patch.object(_wps_cli, "_login_delegated") as login_mock:
                    self.assertTrue(_wps_cli.ensure_auth("cli"))
                refresh_mock.assert_not_called()
                login_mock.assert_not_called()

    def test_valid_token_needs_no_refresh_or_login(self):
        with mock.patch.object(_wps_cli, "auth_status", return_value=self._status("valid", True)), \
                mock.patch.object(_wps_cli, "_refresh_delegated") as refresh_mock, \
                mock.patch.object(_wps_cli, "_login_delegated") as login_mock:
            self.assertTrue(_wps_cli.ensure_auth("cli"))
        refresh_mock.assert_not_called()
        login_mock.assert_not_called()

    def test_auto_login_can_be_disabled(self):
        with mock.patch.dict(os.environ, {"WPS365_NO_AUTO_LOGIN": "1"}), \
                mock.patch.object(_wps_cli, "auth_status", return_value=self._status("expired")), \
                mock.patch.object(_wps_cli, "_refresh_delegated", return_value=False), \
                mock.patch.object(_wps_cli, "_invoke") as invoke_mock:
            self.assertFalse(_wps_cli.ensure_auth("cli"))
        invoke_mock.assert_not_called()


class AuthCommandTest(unittest.TestCase):
    def test_status_only_inspects_even_when_token_is_expired(self):
        with mock.patch.object(sys, "argv", ["auth.py", "status", "--cli", "cli"]), \
                mock.patch.object(wps_auth, "auth_status",
                                  return_value=AuthRecoveryTest._status()), \
                mock.patch.object(wps_auth, "ensure_auth") as ensure, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            wps_auth.main()
        ensure.assert_not_called()
        self.assertIn("expired", output.getvalue())

    def test_status_rejects_force_refresh_without_touching_credentials(self):
        with mock.patch.object(sys, "argv", ["auth.py", "status", "--force-refresh"]), \
                mock.patch.object(wps_auth, "auth_status") as status, \
                mock.patch.object(wps_auth, "ensure_auth") as ensure, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                wps_auth.main()
        self.assertEqual(caught.exception.code, 2)
        status.assert_not_called()
        ensure.assert_not_called()

    def test_force_refresh_bypasses_locally_valid_token(self):
        valid = AuthRecoveryTest._status("valid")
        with mock.patch.dict(os.environ, {"WPS365_NO_AUTO_LOGIN": "1"}), \
                mock.patch.object(sys, "argv",
                                  ["auth.py", "ensure", "--force-refresh", "--cli", "cli"]), \
                mock.patch.object(_wps_cli, "auth_status", return_value=valid), \
                mock.patch.object(wps_auth, "auth_status", return_value=valid), \
                mock.patch.object(_wps_cli, "_refresh_delegated",
                                  return_value=True) as refresh, \
                mock.patch.object(_wps_cli, "_login_delegated") as login, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            wps_auth.main()
        refresh.assert_called_once()
        login.assert_not_called()
        self.assertIn("valid", output.getvalue())

    def test_ensure_keeps_fast_path_for_valid_credentials(self):
        valid = AuthRecoveryTest._status("valid")
        with mock.patch.object(sys, "argv", ["auth.py", "ensure", "--cli", "cli"]), \
                mock.patch.object(_wps_cli, "auth_status", return_value=valid), \
                mock.patch.object(wps_auth, "auth_status", return_value=valid), \
                mock.patch.object(_wps_cli, "_refresh_delegated") as refresh, \
                mock.patch.object(_wps_cli, "_login_delegated") as login, \
                contextlib.redirect_stdout(io.StringIO()):
            wps_auth.main()
        refresh.assert_not_called()
        login.assert_not_called()

    def test_failed_recovery_exits_without_reporting_success(self):
        with mock.patch.object(sys, "argv", ["auth.py", "ensure", "--cli", "cli"]), \
                mock.patch.object(wps_auth, "ensure_auth", return_value=False), \
                mock.patch.object(wps_auth, "auth_status") as status, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit):
                wps_auth.main()
        status.assert_not_called()
        self.assertEqual(output.getvalue(), "")


class CredentialRecoveryTest(unittest.TestCase):
    """Storage failures stop recovery before another login or cloud request."""

    BLOCKED = ("save delegated token: open /home/user/wps365-cli/keyring.key: "
               "operation not permitted")

    def setUp(self):
        _wps_cli._AUTH_RECOVERY_ATTEMPTED = False
        env = mock.patch.dict(os.environ, {
            "WPS365_ACCESS_TOKEN": "", "WPS365_AUTH": "delegated",
            "WPS365_NO_AUTO_LOGIN": "", "WPS365_AUTO_LOGIN": "",
        })
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, _wps_cli, "_AUTH_RECOVERY_ATTEMPTED", False)

    def test_storage_denials_are_distinct_from_remote_scope_denials(self):
        for message in (
            self.BLOCKED,
            r"open C:\Users\user\wps365-cli\keyring.key: Access is denied.",
            "load delegated token: credentials: permission denied",
            "save app token: read-only file system",
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(SystemExit, "credential_store_blocked"):
                    _wps_cli._check_credential_store(message)
        _wps_cli._check_credential_store("403 invalid_scope: permission denied")
        _wps_cli._check_credential_store("refresh token endpoint: permission denied")
        _wps_cli._check_credential_store("401 invalid_client: credentials access denied")

    def test_status_storage_denial_does_not_start_refresh_or_login(self):
        denied = subprocess.CompletedProcess([], 1, "", self.BLOCKED)
        with mock.patch.object(_wps_cli, "_invoke", return_value=denied), \
                mock.patch.object(_wps_cli, "_refresh_delegated") as refresh, \
                mock.patch.object(_wps_cli, "_login_delegated") as login:
            with self.assertRaisesRegex(SystemExit, "credential_store_blocked"):
                _wps_cli.ensure_auth("cli")
        refresh.assert_not_called()
        login.assert_not_called()

    def test_preflight_failure_stops_even_with_locally_valid_credentials(self):
        for state in ("valid", "expired", "not_found"):
            with self.subTest(state=state):
                status = AuthRecoveryTest._status(state)
                status["config_dir"] = "/credential-directory"
                with mock.patch.object(_wps_cli, "auth_status", return_value=status), \
                        mock.patch.object(_wps_cli.tempfile, "TemporaryFile",
                                          side_effect=PermissionError("fixture denial")), \
                        mock.patch.object(_wps_cli, "_refresh_delegated") as refresh, \
                        mock.patch.object(_wps_cli, "_login_delegated") as login:
                    with self.assertRaisesRegex(SystemExit, "stage=preflight"):
                        _wps_cli.ensure_auth("cli", force_refresh=state == "valid")
                refresh.assert_not_called()
                login.assert_not_called()

    def test_valid_fast_path_still_checks_storage_before_returning(self):
        status = AuthRecoveryTest._status("valid")
        status["config_dir"] = "/credential-directory"
        with mock.patch.object(_wps_cli, "auth_status", return_value=status), \
                mock.patch.object(_wps_cli.tempfile, "TemporaryFile",
                                  side_effect=PermissionError("fixture denial")), \
                mock.patch.object(_wps_cli, "_refresh_delegated") as refresh:
            with self.assertRaisesRegex(SystemExit, "stage=preflight"):
                _wps_cli.ensure_auth("cli")
        refresh.assert_not_called()

    def test_business_read_is_blocked_before_implicit_cli_refresh(self):
        status = AuthRecoveryTest._status("expired")
        status["config_dir"] = "/credential-directory"
        with mock.patch.object(_wps_cli, "auth_status", return_value=status), \
                mock.patch.object(_wps_cli.tempfile, "TemporaryFile",
                                  side_effect=PermissionError("fixture denial")), \
                mock.patch.object(_wps_cli, "_invoke") as invoke, \
                mock.patch.object(_wps_cli, "ensure_auth") as ensure:
            with self.assertRaisesRegex(SystemExit, "stage=preflight"):
                _wps_cli.run_cli(["cli", "api", "get", "/x"], 30, "test", "GET /x")
        invoke.assert_not_called()
        ensure.assert_not_called()

    def test_preflight_leaves_existing_credentials_untouched_and_no_probe_files(self):
        with tempfile.TemporaryDirectory() as folder:
            credential = os.path.join(folder, "credential_delegated.enc")
            with open(credential, "wb") as stream:
                stream.write(b"fixture encrypted credential")
            _wps_cli._check_credential_write_access({"config_dir": folder}, "test")
            self.assertEqual(os.listdir(folder), ["credential_delegated.enc"])
            with open(credential, "rb") as stream:
                self.assertEqual(stream.read(), b"fixture encrypted credential")

    def test_old_cli_without_config_dir_keeps_existing_behavior(self):
        with mock.patch.object(_wps_cli.tempfile, "TemporaryFile") as probe:
            for status in (None, {}, {"config_dir": ""}, {"config_dir": []}):
                _wps_cli._check_credential_write_access(status, "test")
        probe.assert_not_called()

    def test_read_only_auth_status_does_not_probe_writes(self):
        status = AuthRecoveryTest._status("valid")
        status["config_dir"] = "/credential-directory"
        reply = subprocess.CompletedProcess([], 0, json.dumps(status), "")
        with mock.patch.object(_wps_cli, "_invoke", return_value=reply), \
                mock.patch.object(_wps_cli.tempfile, "TemporaryFile") as probe:
            self.assertEqual(_wps_cli.auth_status("cli"), status)
        probe.assert_not_called()

    def test_post_refresh_storage_error_identifies_stage_without_raw_credentials(self):
        reply = subprocess.CompletedProcess([], 1, "", self.BLOCKED)
        with mock.patch.object(_wps_cli, "_invoke", return_value=reply):
            with self.assertRaisesRegex(SystemExit, "stage=refresh"):
                _wps_cli._refresh_delegated("cli")

    def test_refresh_save_failure_stops_before_login_and_hides_raw_secrets(self):
        denied = subprocess.CompletedProcess(
            [], 1, "", self.BLOCKED + " refresh_token=fixture-secret")
        with mock.patch.object(_wps_cli, "auth_status",
                               return_value=AuthRecoveryTest._status()), \
                mock.patch.object(_wps_cli, "_invoke", return_value=denied), \
                mock.patch.object(_wps_cli, "_login_delegated") as login:
            with self.assertRaises(SystemExit) as caught:
                _wps_cli.ensure_auth("cli")
        login.assert_not_called()
        self.assertIn("credential_store_blocked", str(caught.exception))
        self.assertNotIn("fixture-secret", str(caught.exception))

    def test_business_storage_failure_does_not_enter_oauth_recovery(self):
        denied = subprocess.CompletedProcess([], 1, "", self.BLOCKED)
        with mock.patch.object(_wps_cli, "auth_status", return_value=None), \
                mock.patch.object(_wps_cli, "_invoke", return_value=denied) as invoke, \
                mock.patch.object(_wps_cli, "ensure_auth") as ensure:
            with self.assertRaisesRegex(SystemExit, "credential_store_blocked"):
                _wps_cli.run_cli(["cli", "api", "get", "/x"], 30, "test", "GET /x")
        self.assertEqual(invoke.call_count, 1)
        ensure.assert_not_called()

    def test_successful_document_content_is_not_interpreted_as_a_storage_error(self):
        payload = json.dumps({"content": self.BLOCKED})
        with mock.patch.object(_wps_cli, "auth_status", return_value=None), \
                mock.patch.object(_wps_cli, "_invoke", return_value=
                               subprocess.CompletedProcess([], 0, payload, "")):
            self.assertEqual(_wps_cli.run_cli(
                ["cli", "api", "get", "/x"], 30, "test", "GET /x"), payload)

    def test_retry_storage_failure_is_classified_without_a_second_recovery(self):
        replies = [subprocess.CompletedProcess([], 1, "", "invalid_token"),
                   subprocess.CompletedProcess([], 1, "", self.BLOCKED)]
        with mock.patch.object(_wps_cli, "auth_status", return_value=None), \
                mock.patch.object(_wps_cli, "_invoke", side_effect=replies) as invoke, \
                mock.patch.object(_wps_cli, "ensure_auth", return_value=True) as ensure:
            with self.assertRaisesRegex(SystemExit, "credential_store_blocked"):
                _wps_cli.run_cli(["cli", "api", "get", "/x"], 30, "test", "GET /x")
        self.assertEqual(invoke.call_count, 2)
        ensure.assert_called_once()

    def test_invalid_grant_still_recovers_through_device_login(self):
        rejected = subprocess.CompletedProcess(
            [], 1, "", '401 invalid_grant: The refresh token has not been found: not_found')
        with mock.patch.object(_wps_cli, "auth_status", side_effect=[
                AuthRecoveryTest._status(), AuthRecoveryTest._status("valid")]), \
                mock.patch.object(_wps_cli, "_invoke", return_value=rejected), \
                mock.patch.object(_wps_cli, "_login_delegated", return_value=True) as login, \
                contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertTrue(_wps_cli.ensure_auth("cli"))
        login.assert_called_once()
        self.assertIn("refresh_grant_rejected", output.getvalue())

    def test_missing_refresh_token_goes_directly_to_device_login(self):
        with mock.patch.object(_wps_cli, "auth_status", side_effect=[
                AuthRecoveryTest._status("not_found", False),
                AuthRecoveryTest._status("valid")]), \
                mock.patch.object(_wps_cli, "_refresh_delegated") as refresh, \
                mock.patch.object(_wps_cli, "_login_delegated", return_value=True) as login, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(_wps_cli.ensure_auth("cli"))
        refresh.assert_not_called()
        login.assert_called_once()

    def test_transient_and_client_errors_do_not_start_device_login(self):
        for message in ("503 Service Unavailable", "network timeout",
                        "401 invalid_client", "404 endpoint not_found"):
            with self.subTest(message=message):
                reply = subprocess.CompletedProcess([], 1, "", message)
                with mock.patch.object(_wps_cli, "auth_status",
                                       return_value=AuthRecoveryTest._status()), \
                        mock.patch.object(_wps_cli, "_invoke", return_value=reply), \
                        mock.patch.object(_wps_cli, "_login_delegated") as login:
                    with self.assertRaisesRegex(SystemExit, "auth_refresh_failed"):
                        _wps_cli.ensure_auth("cli")
                login.assert_not_called()

    def test_refresh_timeout_does_not_start_device_login(self):
        with mock.patch.object(_wps_cli, "auth_status",
                               return_value=AuthRecoveryTest._status()), \
                mock.patch.object(_wps_cli, "_invoke",
                                  side_effect=subprocess.TimeoutExpired("cli", 1)), \
                mock.patch.object(_wps_cli, "_login_delegated") as login:
            with self.assertRaisesRegex(SystemExit, "auth_refresh_failed"):
                _wps_cli.ensure_auth("cli")
        login.assert_not_called()

    def test_malformed_refresh_response_is_not_reported_as_expiry(self):
        for payload in ("invalid-json", "[]", "{}", '{"delegated":[]}',
                        '{"delegated":{"refreshed":"false"}}'):
            with self.subTest(payload=payload), \
                    mock.patch.object(_wps_cli, "_invoke", return_value=
                                      subprocess.CompletedProcess([], 0, payload, "")):
                with self.assertRaisesRegex(SystemExit, "auth_refresh_failed"):
                    _wps_cli._refresh_delegated("cli")

    def test_no_refresh_result_can_fall_back_to_login(self):
        with mock.patch.object(_wps_cli, "_invoke", return_value=subprocess.CompletedProcess(
                [], 0, '{"delegated":{"refreshed":false}}', "")):
            self.assertFalse(_wps_cli._refresh_delegated("cli"))

    def test_device_login_save_failure_is_not_reported_as_success(self):
        with mock.patch.object(_wps_cli, "_stream_login", return_value=(1, self.BLOCKED)), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, "credential_store_blocked"):
                _wps_cli._login_delegated("cli", "test")

    def test_login_stream_captures_both_channels_and_keeps_instructions_visible(self):
        code = ("import sys; print('device verification URL', flush=True); "
                "print('save delegated token: keyring.key: operation not permitted', "
                "file=sys.stderr, flush=True); sys.exit(1)")
        with contextlib.redirect_stderr(io.StringIO()) as output:
            returncode, detail = _wps_cli._stream_login([sys.executable, "-c", code], 5)
        self.assertEqual(returncode, 1)
        self.assertIn("device verification URL", output.getvalue())
        self.assertIn("keyring.key", detail)

    def test_login_timeout_terminates_the_child(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(subprocess.TimeoutExpired):
                _wps_cli._stream_login(
                    [sys.executable, "-c", "import time; time.sleep(10)"], 0.05)

    @unittest.skipUnless(os.name == "posix", "executable fixture uses a POSIX shebang")
    def test_auth_entrypoint_with_real_fixture_processes(self):
        for scenario in ("refresh-blocked", "login-blocked", "status-blocked",
                         "network-failure", "invalid-grant", "success",
                         "preflight-refresh-blocked", "preflight-valid-blocked"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                cli = os.path.join(tmp, "fixture cli")
                log = os.path.join(tmp, "calls.jsonl")
                state = os.path.join(tmp, "logged-in")
                source = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
scenario = {scenario!r}
with open({log!r}, 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:2] == ['auth', 'status']:
    if scenario == 'status-blocked':
        sys.exit({self.BLOCKED!r})
    print(json.dumps({{'config_dir': {cli!r} if scenario.startswith('preflight-') else {tmp!r}, 'delegated': {{
        'available': True, 'has_refresh': True,
        'status': 'valid' if os.path.exists({state!r}) or scenario == 'preflight-valid-blocked' else 'expired'
    }}}}))
elif args[:2] == ['auth', 'refresh']:
    if scenario == 'refresh-blocked':
        sys.exit({self.BLOCKED!r})
    if scenario == 'network-failure':
        sys.exit('503 Service Unavailable')
    if scenario in ('login-blocked', 'invalid-grant'):
        sys.exit('401 invalid_grant: refresh token not found')
    open({state!r}, 'w').close()
    print('{{"delegated":{{"refreshed":true}}}}')
elif args[:2] == ['auth', 'login']:
    print('Confirm device authorization', flush=True)
    if scenario == 'login-blocked':
        sys.exit({self.BLOCKED!r})
    open({state!r}, 'w').close()
else:
    sys.exit('unexpected command')
"""
                with open(cli, "w", encoding="utf-8") as stream:
                    stream.write(source)
                os.chmod(cli, 0o755)
                proc = subprocess.run(
                    [sys.executable, os.path.join(SCRIPTS, "auth.py"),
                     "ensure", "--cli", cli],
                    capture_output=True, text=True, timeout=15, encoding="utf-8")
                with open(log, encoding="utf-8") as stream:
                    calls = [json.loads(line)[:2] for line in stream]
                if scenario in ("invalid-grant", "success"):
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn("valid", proc.stdout)
                else:
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertNotIn("✅", proc.stdout)
                    self.assertIn("auth_refresh_failed" if scenario == "network-failure"
                                  else "credential_store_blocked", proc.stderr)
                    self.assertNotIn("请手动运行", proc.stderr)
                self.assertEqual(calls.count(["auth", "login"]),
                                 int(scenario in ("login-blocked", "invalid-grant")))
                self.assertEqual(calls.count(["auth", "refresh"]),
                                 int(scenario != "status-blocked" and not scenario.startswith("preflight-")))


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
        for name in ("otl_fetch.py", "otl_write.py", "otl_sync.py", "file_fetch.py",
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
    CLI_CALLERS = ("otl_fetch.py", "otl_write.py", "file_fetch.py", "file_write.py",
                   "dbsheet.py")

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


class ImageMagicTest(unittest.TestCase):
    """Attachment URLs may serve a different image format than the filename says."""

    def test_guess_ext_sniffs_content_not_names(self):
        self.assertEqual(otl_fetch.guess_ext(b"\x89PNG\r\n\x1a\n0000"), ".png")
        self.assertEqual(otl_fetch.guess_ext(b"\xff\xd8\xff\xe00000"), ".jpg")
        self.assertEqual(otl_fetch.guess_ext(b"RIFF0000WEBP0000"), ".webp")
        self.assertEqual(otl_fetch.guess_ext(b"GIF89a0000"), ".gif")
        self.assertEqual(otl_fetch.guess_ext(b"not an image"), ".bin")


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
                with mock.patch.object(xlsx_edit, "_node_spellings", return_value=["node.exe"]):
                    node, cli = xlsx_edit.find_kit(None, None)
            self.assertEqual(cli, expected["cli"])
            self.assertEqual(node, expected["node"])

    def test_explicit_arguments_win_over_discovery(self):
        node, cli = xlsx_edit.find_kit("/chosen/node", "/chosen/cli.js")
        self.assertEqual((node, cli), ("/chosen/node", "/chosen/cli.js"))

    def test_environment_overrides_are_used(self):
        with mock.patch.dict(os.environ, {"DSH_NODE": "/env/node",
                                          "DSH_LIBREOFFICE_CLI": "/env/cli.js"}, clear=False):
            node, cli = xlsx_edit.find_kit(None, None)
        self.assertEqual((node, cli), ("/env/node", "/env/cli.js"))

    def test_finds_node_in_dsh_runtime_with_both_platform_spellings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = os.path.join(tmp, "home")
            runtime = os.path.join(home, "dsh-runtimes", "v1")
            node_dir = os.path.join(runtime, "dependencies", "node", "bin")
            os.makedirs(node_dir)
            for spelling in ("node", "node.exe"):
                node_path = os.path.join(node_dir, spelling)
                with open(node_path, "w", encoding="utf-8"):
                    pass
                with mock.patch.object(xlsx_edit, "_search_roots", return_value=[]), \
                     mock.patch.object(xlsx_edit, "_dsh_home", return_value=home), \
                     mock.patch.object(xlsx_edit, "_node_spellings", return_value=[spelling]), \
                     mock.patch.dict(os.environ, {"DSH_NODE": ""}), \
                     mock.patch.object(xlsx_edit.shutil, "which", return_value=None):
                    node, cli = xlsx_edit.find_kit(None, "/chosen/cli.js")
                self.assertEqual(node, node_path)
                self.assertEqual(cli, "/chosen/cli.js")
                os.remove(node_path)

    def test_missing_bundled_node_stays_missing(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.object(xlsx_edit, "_search_roots", return_value=[]), \
                 mock.patch.object(xlsx_edit, "_dsh_home", return_value=home), \
                 mock.patch.dict(os.environ, {"DSH_NODE": ""}), \
                 mock.patch.object(xlsx_edit.shutil, "which", return_value=None):
                node, cli = xlsx_edit.find_kit(None, "/chosen/cli.js")
        self.assertIsNone(node)
        self.assertEqual(cli, "/chosen/cli.js")


class VerifySnapshotTest(unittest.TestCase):
    """Block order participates in whole-document conflict checks."""

    @staticmethod
    def _snapshot(ids, *, scope=None, scope_explicit=False, version=1):
        return {
            "version": version,
            "scope": scope or [],
            "scope_explicit": scope_explicit,
            "blocks": [{"block_id": bid, "hash": f"hash-{bid}"} for bid in ids],
        }

    def _verify(self, old, new, *, mode="append", allow_added=False, force=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "snapshot.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(old, fh)
            with mock.patch.object(otl_write, "snapshot", return_value=new) as current:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    count = otl_write.verify_snapshot(
                        "cli", "file-id", path, mode, 30, allow_added, force)
            current.assert_called_once_with("cli", "file-id", 30)
        return count, output.getvalue()

    def test_unchanged_snapshot_is_allowed(self):
        count, output = self._verify(
            self._snapshot(["a", "b"]), self._snapshot(["a", "b"], version=2))
        self.assertEqual(count, 2)
        self.assertIn("未被改动", output)

    def test_replace_rejects_reorder_even_with_scope_and_allow_added(self):
        old = self._snapshot(["a", "b"], scope=["a"], scope_explicit=True)
        new = self._snapshot(["b", "a"], version=2)
        with self.assertRaises(SystemExit) as caught:
            self._verify(old, new, mode="replace", allow_added=True)
        self.assertIn("一级块顺序变化", str(caught.exception))
        self.assertIn("受影响：", str(caught.exception))

    def test_unscoped_allow_added_does_not_allow_reorder(self):
        with self.assertRaises(SystemExit) as caught:
            self._verify(self._snapshot(["a", "b"]), self._snapshot(["b", "a"]),
                         allow_added=True)
        self.assertIn("一级块顺序变化", str(caught.exception))

    def test_allow_added_accepts_insert_with_existing_order_preserved(self):
        count, output = self._verify(
            self._snapshot(["a", "b"]), self._snapshot(["a", "new", "b"]),
            allow_added=True)
        self.assertEqual(count, 3)
        self.assertIn("仅新增块", output)

    def test_explicit_scope_allows_unrelated_reorder(self):
        old = self._snapshot(["a", "b"], scope=["a"], scope_explicit=True)
        count, output = self._verify(old, self._snapshot(["b", "a"], version=2))
        self.assertEqual(count, 2)
        self.assertIn("只比对声明的 1 个依赖块", output)
        self.assertIn("一级块顺序变化", output)

    def test_force_logs_and_proceeds_on_reorder(self):
        count, output = self._verify(
            self._snapshot(["a", "b"]), self._snapshot(["b", "a"], version=2),
            force=True)
        self.assertEqual(count, 2)
        self.assertIn("已忽略冲突（--force）", output)
        self.assertIn("一级块顺序变化", output)


class XlsxReadbackTest(unittest.TestCase):
    """字节覆盖对「在线表格」不生效，所以写完必须读回来核对。

    2026-10-15 实测：B1 从 1 改成 42 后按字节上传，version 1→2 且云端字节与本机
    一致，但 sheets 接口读回仍是 1 —— 只看版本号会把这种假成功当成功。
    """

    def test_literal_expectations_skip_formulas_and_foreign_entries(self):
        got = xlsx_edit.literal_expectations(
            ["数据!B2=99", "数据!D2==B2*C2", "说明!A2=脚本写入", "没有感叹号=1", "数据!B3="])
        self.assertEqual(got, [("数据", "B2", "99"), ("说明", "A2", "脚本写入"), ("数据", "B3", "")])

    def test_cell_indices_follow_the_sheets_api(self):
        self.assertEqual(xlsx_edit.cell_indices("A1"), (0, 0))
        self.assertEqual(xlsx_edit.cell_indices("B10"), (9, 1))
        self.assertEqual(xlsx_edit.cell_indices("aa3"), (2, 26))
        with self.assertRaises(ValueError):
            xlsx_edit.cell_indices("12")

    def test_same_literal_tolerates_type_and_format(self):
        for actual in (99, "99", 99.0, " 99 "):
            self.assertTrue(xlsx_edit.same_literal("99", actual), actual)
        self.assertTrue(xlsx_edit.same_literal('"你好"', '"你好"'))
        self.assertTrue(xlsx_edit.same_literal("", None))
        self.assertFalse(xlsx_edit.same_literal("99", 1))
        self.assertFalse(xlsx_edit.same_literal("42", None))

    def test_boolean_expectations_match_the_written_type(self):
        for raw, value in (("true", True), ("FALSE", False)):
            for actual in (value, str(value), str(value).upper()):
                self.assertTrue(xlsx_edit.same_literal(raw, actual))
            for actual in (not value, int(value), str(int(value)), None):
                self.assertFalse(xlsx_edit.same_literal(raw, actual))
        self.assertFalse(xlsx_edit.same_literal("1", True))
        self.assertFalse(xlsx_edit.same_literal("0", False))

    def test_text_preserves_quotes_and_whitespace(self):
        for raw, stale in (('"你好"', "你好"), (" 你好 ", "你好")):
            self.assertTrue(xlsx_edit.same_literal(raw, raw))
            self.assertFalse(xlsx_edit.same_literal(raw, stale))
        self.assertFalse(xlsx_edit.same_literal("9007199254740993", "9007199254740992"))

    def test_repeated_assignments_verify_only_the_final_value(self):
        self.assertEqual(xlsx_edit.literal_expectations(
            ["数据!a1=1", "数据!$A$1=2"]), [("数据", "A1", "2")])
        self.assertEqual(xlsx_edit.literal_expectations(
            ["数据!A1=1", "数据!A1==B1*2"]), [])
        self.assertEqual(xlsx_edit.literal_expectations(
            ["数据!A1==B1*2", "数据!A1=3"]), [("数据", "A1", "3")])

    def test_empty_range_accepts_clearing_but_rejects_nonempty_values(self):
        sheets = {"sheets": [{"sheet_id": 1, "name": "数据"}]}
        self.assertEqual(self._adopted([sheets, {"range_data": []}],
                                      [("数据", "B2", "")]), [])
        self.assertEqual(len(self._adopted([sheets, {"range_data": []}],
                                          [("数据", "B2", "42")])), 1)

    def _adopted(self, side_effect, expectations):
        with mock.patch.object(file_fetch, "api", side_effect=side_effect):
            return xlsx_edit.verify_adopted("cli", "file-id", expectations, 5)

    def test_reports_a_cell_the_cloud_never_adopted(self):
        sheets = {"sheets": [{"sheet_id": 1, "name": "数据"}]}
        stale = {"range_data": [{"row_from": 1, "col_from": 1, "original_cell_value": 1}]}
        problems = self._adopted([sheets, stale], [("数据", "B2", "42")])
        self.assertEqual(len(problems), 1)
        self.assertIn("期望 '42'", problems[0])
        self.assertIn("云端读回 1", problems[0])

    def test_accepts_a_cell_the_cloud_adopted(self):
        sheets = {"sheets": [{"sheet_id": 1, "name": "数据"}]}
        fresh = {"range_data": [{"row_from": 1, "col_from": 1, "original_cell_value": 42}]}
        self.assertEqual(self._adopted([sheets, fresh], [("数据", "B2", "42")]), [])

    def test_flags_a_sheet_the_cloud_does_not_have(self):
        problems = self._adopted([{"sheets": [{"sheet_id": 1, "name": "别的表"}]}],
                                 [("数据", "B2", "42")])
        self.assertIn("云端没有这张工作表", problems[0])

    def test_empty_range_is_reported_as_not_adopted(self):
        sheets = {"sheets": [{"sheet_id": 1, "name": "数据"}]}
        problems = self._adopted([sheets, {"range_data": []}], [("数据", "B2", "42")])
        self.assertEqual(len(problems), 1)
        self.assertIn("云端读回 None", problems[0])




class OtlImagePlanTest(unittest.TestCase):
    """Markdown 里的图片必须按出现顺序配对到 convert 出的 picture 块。"""

    def test_collects_both_syntaxes_in_document_order(self):
        md = ('前文 <img src="a.png" width="86%"> 后文\n'
              '![说明](img/b.png)\n'
              '<img src="https://example.com/c.png">\n')
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "img"))
            for rel in ("a.png", os.path.join("img", "b.png")):
                open(os.path.join(tmp, rel), "wb").close()
            plan = otl_write.image_plan(md, tmp)
        self.assertEqual([item["src"] for item in plan], ["a.png", "img/b.png",
                                                          "https://example.com/c.png"])
        self.assertEqual([item["local"] for item in plan], [True, True, False])

    def test_a_missing_local_file_is_reported_as_not_local(self):
        plan = otl_write.image_plan('<img src="gone.png">', "/nonexistent-dir")
        self.assertFalse(plan[0]["local"])
        self.assertTrue(plan[0]["path"].endswith("gone.png"))

    def test_remote_urls_never_touch_the_filesystem(self):
        plan = otl_write.image_plan("![x](https://cdn.example.com/y.png?sign=1)", ".")
        self.assertEqual(plan[0]["path"], None)
        self.assertFalse(plan[0]["local"])


class OtlFillPicturesTest(unittest.TestCase):
    """空 id 的 picture 块写进去会被 400 拒，所以必须回填附件 id 与真实尺寸。"""

    def _blocks(self, count=1):
        return [{"picture": {"attributes": {"id": "", "width": 300, "height": 300},
                             "range_marks": []}} for _ in range(count)]

    def test_fills_id_and_real_size_and_caps_render_width(self):
        blocks = self._blocks()
        plan = [{"src": "a.png", "path": "/tmp/a.png", "local": True,
                 "id": "ABCDEFGHIJKLM", "width": 1058, "height": 367}]
        with contextlib.redirect_stdout(io.StringIO()):
            filled = otl_write.fill_pictures(blocks, plan, 86, 740)
        attrs = blocks[0]["picture"]["attributes"]
        self.assertEqual(filled, 1)
        self.assertEqual(attrs["id"], "ABCDEFGHIJKLM")
        self.assertEqual((attrs["width"], attrs["height"]), (1058, 367))
        self.assertEqual(attrs["render_width"], 636)   # 86% × 740
        self.assertEqual(attrs["align"], "center")

    def test_a_narrow_image_keeps_its_original_width(self):
        blocks = self._blocks()
        plan = [{"src": "a.png", "path": "/tmp/a.png", "local": True,
                 "id": "ID", "width": 508, "height": 422}]
        with contextlib.redirect_stdout(io.StringIO()):
            otl_write.fill_pictures(blocks, plan, 86, 740)
        self.assertEqual(blocks[0]["picture"]["attributes"]["render_width"], 508)

    def test_a_remote_image_becomes_a_placeholder_paragraph(self):
        blocks = self._blocks()
        plan = [{"src": "https://example.com/a.png", "path": None, "local": False}]
        with contextlib.redirect_stdout(io.StringIO()):
            otl_write.fill_pictures(blocks, plan, 86, 740)
        paragraph = blocks[0]["paragraph"]
        self.assertIn("https://example.com/a.png",
                      paragraph["elements"][0]["text"]["attributes"]["content"])

    def test_a_mismatch_aborts_instead_of_writing_blank_ids(self):
        with self.assertRaises(SystemExit) as caught:
            otl_write.fill_pictures(self._blocks(2),
                                    [{"src": "a.png", "path": "/tmp/a.png", "local": True,
                                      "id": "ID", "width": 10, "height": 10}], 86, 740)
        self.assertIn("顺序配对不成立", str(caught.exception))


class OtlWideTableTest(unittest.TestCase):
    """服务端 table.rows 是 maxItems=100，超了写入 400。"""

    def _table(self, rows):
        head = {"cells": [{"attributes": {"colspan": 1, "rowspan": 1},
                           "block_id": "head-cell",
                           "children": [{"paragraph": {"elements": []}}]}]}
        body = [{"cells": [{"attributes": {"colspan": 1, "rowspan": 1},
                            "block_id": f"cell-{i}",
                            "children": [{"paragraph": {"elements": []}}]}]}
                for i in range(rows)]
        return {"table": {"attributes": {"border_type": "solid", "grid": [740]},
                          "range_marks": [], "rows": [head] + body}}

    def test_a_short_table_is_left_alone(self):
        blocks = [self._table(40)]
        out, split = otl_write.split_wide_tables(blocks)
        self.assertEqual(split, 0)
        self.assertEqual(len(out), 1)

    def test_a_long_table_is_split_into_capped_pieces_with_repeated_header(self):
        out, split = otl_write.split_wide_tables([self._table(429)])
        self.assertEqual(len(out), 5)          # 429 数据行 = 4×99 + 33
        self.assertEqual(split, 4)             # 净增 4 张
        header = json.loads(json.dumps(self._table(0)["table"]["rows"][0]))
        otl_write._strip_block_ids(header)
        for piece in out:
            rows = piece["table"]["rows"]
            self.assertLessEqual(len(rows), otl_write.MAX_TABLE_ROWS)
            self.assertEqual(rows[0], header, "表头必须重复")

    def test_split_copies_do_not_reuse_block_ids(self):
        out, _ = otl_write.split_wide_tables([self._table(200)])
        blob = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("head-cell", blob)
        self.assertNotIn("cell-0", blob)


class OtlPayloadTransportTest(unittest.TestCase):
    """千块级写入的 JSON 塞进 argv 会 E2BIG，必须改走 --file。"""

    def _run(self, payload):
        seen = {}

        def fake_run_cli(cmd, timeout, label, what):
            seen["cmd"] = cmd
            for index, part in enumerate(cmd):
                if part == "--file":
                    with open(cmd[index + 1], encoding="utf-8") as fh:
                        seen["body"] = json.load(fh)
                    os.unlink(cmd[index + 1])
            return json.dumps({"code": 0, "data": {"ok": True}})

        with mock.patch.object(otl_write, "_run_cli", side_effect=fake_run_cli):
            otl_write.api("/cli", "post", "/v7/x", payload, 30)
        return seen

    def test_a_small_body_stays_on_the_command_line(self):
        seen = self._run({"a": 1})
        self.assertIn("--data", seen["cmd"])
        self.assertNotIn("--file", seen["cmd"])

    def test_a_large_body_goes_through_a_file(self):
        seen = self._run({"blob": "中" * (otl_write.INLINE_BODY_LIMIT)})
        self.assertIn("--file", seen["cmd"])
        self.assertNotIn("--data", seen["cmd"])
        self.assertEqual(len(seen["body"]["blob"]), otl_write.INLINE_BODY_LIMIT)

    def test_query_parameters_are_passed_separately_from_the_path(self):
        seen = self._run(None)
        self.assertIn("/v7/x", seen["cmd"])
        self.assertNotIn("--query", seen["cmd"])

        seen = {}
        with mock.patch.object(otl_write, "_run_cli",
                               side_effect=lambda cmd, timeout, label, what:
                               (seen.setdefault("cmd", cmd), json.dumps({"code": 0}))[1]):
            otl_write.api("/cli", "get", "/v7/x", None, 30, query={"page_size": "100"})
        self.assertIn("page_size=100", seen["cmd"])


class OtlSyncHelpersTest(unittest.TestCase):
    """批量同步脚本：front matter 摘除与文件名安全化。"""

    def test_front_matter_is_replaced_by_metadata(self):
        md = "---\ntitle: T\nsource: s.pdf\npages: 84\nconverted: 2026-11-01\n---\n\n# 前言\n"
        body, meta = otl_write.strip_front_matter(md)
        self.assertTrue(body.startswith("# 前言"))
        quote = otl_sync.meta_blockquote(meta)
        self.assertIn("来源文件：s.pdf", quote)
        self.assertIn("页数：84", quote)
        self.assertTrue(quote.endswith("\n\n"))

    def test_markdown_without_front_matter_is_untouched(self):
        body, meta = otl_write.strip_front_matter("# 标题\n")
        self.assertEqual(body, "# 标题\n")
        self.assertEqual(meta, {})

    def test_names_drop_path_separators(self):
        self.assertEqual(otl_sync.safe_name("SL651/水文：规约?"), "SL651-水文：规约")

    def test_a_blank_paragraph_is_recognised(self):
        self.assertTrue(otl_write.is_blank_paragraph(
            {"paragraph": {"attributes": {}, "elements": [], "range_marks": []}}))
        self.assertFalse(otl_write.is_blank_paragraph(
            {"paragraph": {"elements": [{"text": {"attributes": {"content": "x"}}}]}}))
        self.assertFalse(otl_write.is_blank_paragraph({"heading": {"elements": []}}))




class OtlTitleTest(unittest.TestCase):
    """新建智能文档的标题块是空的，只写正文会一直留着「输入标题」占位。"""

    def test_explicit_text_wins(self):
        self.assertEqual(
            otl_write.resolve_title("我的标题", "meta.pdf", "/tmp/文件.md"), "我的标题")

    def test_front_matter_title_is_used_by_auto(self):
        self.assertEqual(
            otl_write.resolve_title("auto", "SL651-水文监测数据通信规约", "/tmp/x.md"),
            "SL651-水文监测数据通信规约")

    def test_md_stem_is_the_last_resort(self):
        self.assertEqual(otl_write.resolve_title("auto", None, "/tmp/规范 文档.md"), "规范 文档")

    def test_none_disables_the_title(self):
        self.assertEqual(otl_write.resolve_title("none", "有标题", "/tmp/x.md"), "")

    def test_auto_without_any_source_yields_nothing(self):
        self.assertEqual(otl_write.resolve_title("auto", None, None), "")

    def test_doc_title_reads_the_title_block(self):
        def fake_api(cli, method, path, data, timeout, query=None):
            return {"block": {"doc": {"title": {"block_id": "title", "elements": [
                {"text": {"attributes": {"content": "标准 A"}}},
                {"text": {"attributes": {"content": "-2021"}}}]}}}}

        with mock.patch.object(otl_write, "api", side_effect=fake_api):
            self.assertEqual(otl_write.doc_title("/cli", "fid", 5), "标准 A-2021")

    def _ensure(self, current, title, overwrite=False):
        written = []
        with mock.patch.object(otl_write, "doc_title", return_value=current), \
                mock.patch.object(otl_write, "set_title",
                                  side_effect=lambda cli, fid, text, timeout: written.append(text)):
            action = otl_write.ensure_title("/cli", "fid", title, 5, overwrite=overwrite)
        return action, written

    def test_an_empty_title_is_filled(self):
        action, written = self._ensure("", "标准 A")
        self.assertEqual((action, written), ("set", ["标准 A"]))

    def test_an_existing_title_is_not_clobbered(self):
        action, written = self._ensure("别人写的标题", "标准 A")
        self.assertEqual((action, written), ("kept", []))

    def test_the_same_title_needs_no_write(self):
        action, written = self._ensure("标准 A", "标准 A")
        self.assertEqual((action, written), ("unchanged", []))

    def test_retitle_overwrites_on_request(self):
        action, written = self._ensure("旧标题", "标准 A", overwrite=True)
        self.assertEqual((action, written), ("set", ["标准 A"]))

    def test_an_empty_candidate_never_clears_a_title(self):
        action, written = self._ensure("旧标题", "")
        self.assertEqual((action, written), ("unchanged", []))


class OtlServiceTestCase(unittest.TestCase):
    """Exercise the actual entrypoints against a stateful, local block service."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.children = [self.paragraph("old", "old-id")]
        self.converted = [self.paragraph("new")]
        self.events = []
        self.fail_convert = False
        self.fail_create = None
        self.drop_create = False
        self.create_count = 0
        self.commit_then_timeout = False
        self.after_convert = None
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(otl_write, "api", side_effect=self.api).start()
        mock.patch.object(otl_sync, "api", side_effect=self.api).start()
        mock.patch.object(otl_write, "ensure_title", return_value="unchanged").start()
        mock.patch.object(otl_sync, "ensure_title", return_value="unchanged").start()
        original_tempfile = tempfile.NamedTemporaryFile

        def local_tempfile(*args, **kwargs):
            kwargs.setdefault("dir", self.tmp.name)
            return original_tempfile(*args, **kwargs)

        mock.patch.object(tempfile, "NamedTemporaryFile", side_effect=local_tempfile).start()

    @staticmethod
    def paragraph(text, block_id=None):
        inner = {"elements": [{"text": {"attributes": {"content": text}}}]} if text else {"elements": []}
        if block_id:
            inner["block_id"] = block_id
        return {"paragraph": inner}

    def api(self, cli, method, path, data, timeout, query=None):
        self.events.append(path.rsplit("/", 1)[-1])
        if path.endswith("/convert"):
            if self.fail_convert:
                raise SystemExit("conversion failed")
            if self.after_convert:
                self.after_convert()
            return {"blocks": json.loads(json.dumps(self.converted))}
        if path.endswith("/blocks"):
            return {"block": {"doc": {"children": json.loads(json.dumps(self.children))}}}
        if path.endswith("/delete"):
            del self.children[data["start_index"]:data["end_index"]]
            return {}
        if path.endswith("/create"):
            self.create_count += 1
            if self.create_count == self.fail_create:
                raise RuntimeError("interrupted")
            if not self.drop_create:
                children = json.loads(json.dumps(data["doc_children"]["children"]))
                for index, block in enumerate(children):
                    next(iter(block.values()))["block_id"] = f"new-{self.create_count}-{index}"
                self.children[data["index"]:data["index"]] = children
            if self.commit_then_timeout:
                raise RuntimeError("response timed out after commit")
            return {}
        raise AssertionError(f"Unexpected cloud request: {path}")

    def run_write(self, *args):
        argv = ["otl_write.py", "--file-id", "fid", "--cli", "/cli",
                "--text", "# new", "--title", "none", *args]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            otl_write.main()


class OtlWriteSafetyTest(OtlServiceTestCase):
    def test_all_dry_run_modes_leave_empty_and_existing_documents_unchanged(self):
        for mode in ("append", "prepend", "replace"):
            for body in ([self.paragraph("")], [self.paragraph("old", "old-id")]):
                with self.subTest(mode=mode, body=body):
                    self.children = json.loads(json.dumps(body))
                    self.events.clear()
                    self.run_write("--mode", mode, "--dry-run")
                    self.assertEqual(self.children, body)
                    self.assertNotIn("delete", self.events)
                    self.assertNotIn("create", self.events)

    def test_failed_conversion_keeps_old_body(self):
        self.fail_convert = True
        with self.assertRaises(SystemExit):
            self.run_write("--mode", "replace")
        self.assertEqual(self.children, [self.paragraph("old", "old-id")])
        self.assertNotIn("delete", self.events)

    def test_picture_count_mismatch_keeps_old_body_and_uploads_nothing(self):
        self.converted = [{"picture": {"attributes": {"id": ""}}}]
        with mock.patch.object(otl_write, "upload_attachment") as upload:
            with self.assertRaises(SystemExit):
                self.run_write("--mode", "replace")
            upload.assert_not_called()
        self.assertNotIn("delete", self.events)

    def test_invalid_local_image_keeps_old_body(self):
        path = os.path.join(self.tmp.name, "image.png")
        with open(path, "wb") as fh:
            fh.write(b"not an image")
        self.converted = [{"picture": {"attributes": {"id": ""}}}]
        with mock.patch.object(otl_write, "image_size", side_effect=ValueError("broken image")):
            with self.assertRaises(ValueError):
                with mock.patch.object(sys, "argv", ["otl_write.py", "--file-id", "fid",
                                                     "--text", f"![]({path})", "--mode", "replace"]), \
                        contextlib.redirect_stdout(io.StringIO()):
                    otl_write.main()
        self.assertNotIn("delete", self.events)

    def test_conversion_and_validation_precede_delete_and_keep_backup(self):
        self.run_write("--mode", "replace")
        self.assertLess(self.events.index("convert"), self.events.index("delete"))
        backups = [name for name in os.listdir(self.tmp.name) if name.startswith("otl-backup-")]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(self.tmp.name, backups[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["children"], [self.paragraph("old", "old-id")])

    def test_concurrent_change_during_preparation_stops_replace(self):
        self.after_convert = lambda: self.children.append(self.paragraph("someone else's edit", "other"))
        with self.assertRaises(SystemExit):
            self.run_write("--mode", "replace")
        self.assertNotIn("delete", self.events)
        self.assertEqual(len(self.children), 2)

    def test_missing_prepend_and_append_batches_are_failures(self):
        for mode in ("prepend", "append"):
            with self.subTest(mode=mode):
                self.children = [self.paragraph(f"old-{i}", str(i)) for i in range(100)]
                self.drop_create = True
                with self.assertRaises(SystemExit):
                    self.run_write("--mode", mode)
                self.assertEqual(len(self.children), 100)

    def test_prepend_success_preserves_original_body(self):
        self.run_write("--mode", "prepend")
        self.assertEqual(len(self.children), 2)
        self.assertEqual(self.children[1], self.paragraph("old", "old-id"))

    def test_blank_document_can_be_written_in_each_mode(self):
        for mode in ("append", "prepend", "replace"):
            with self.subTest(mode=mode):
                self.children = [self.paragraph("")]
                self.run_write("--mode", mode)
                self.assertEqual(len(self.children), 1)
                self.assertEqual(self.children[0]["paragraph"]["elements"][0]["text"]["attributes"]["content"], "new")

    def test_previews_cannot_be_committed_even_via_helper_api(self):
        prepared = {"file_id": "fid", "dry_run": True}
        with self.assertRaises(ValueError):
            otl_write.commit_document("cli", "fid", prepared, mode="replace", baseline=self.children)
        with self.assertRaises(ValueError):
            otl_write.write_prepared("cli", "fid", prepared)
        self.assertEqual(self.events, [])

    def test_upload_failure_keeps_existing_body(self):
        path = os.path.join(self.tmp.name, "a.png")
        with open(path, "wb") as fh:
            fh.write(b"image bytes")
        self.converted = [{"picture": {"attributes": {"id": ""}}}]
        with mock.patch.object(otl_write, "image_size", return_value=(10, 20)), \
                mock.patch.object(otl_write, "upload_attachment", side_effect=RuntimeError("upload failed")), \
                mock.patch.object(sys, "argv", ["otl_write.py", "--file-id", "fid", "--text", f"![]({path})", "--mode", "replace"]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                otl_write.main()
        self.assertNotIn("delete", self.events)
        self.assertEqual(self.children, [self.paragraph("old", "old-id")])

    def test_dry_run_checks_picture_pairing_and_does_not_upload(self):
        path = os.path.join(self.tmp.name, "a.png")
        with open(path, "wb") as fh:
            fh.write(b"image")
        self.converted = [{"picture": {"attributes": {"id": ""}}}]
        with mock.patch.object(otl_write, "image_size", return_value=(10, 20)), \
                mock.patch.object(otl_write, "upload_attachment") as upload:
            otl_write.prepare_markdown("cli", "fid", "![](a.png)", base=self.tmp.name,
                                       dry_run=True, progress=lambda *args: None)
            upload.assert_not_called()
        self.assertNotIn("delete", self.events)
        self.assertNotIn("create", self.events)

    def test_image_cache_requires_current_content_and_document(self):
        path = os.path.join(self.tmp.name, "a.png")
        with open(path, "wb") as fh:
            fh.write(b"first image")
        cache = {}
        self.converted = [{"picture": {"attributes": {"id": ""}}}]
        with mock.patch.object(otl_write, "image_size", return_value=(10, 20)), \
                mock.patch.object(otl_write, "upload_attachment", side_effect=["one", "two", "three"]) as upload:
            def prepare(fid="fid"):
                return otl_write.prepare_markdown("cli", fid, "![](a.png)", base=self.tmp.name,
                                                  image_cache=cache, progress=lambda *args: None)
            first = prepare()
            second = prepare()
            self.assertEqual(upload.call_count, 1)
            self.assertEqual(first["children"], second["children"])
            with open(path, "wb") as fh:
                fh.write(b"changed image")
            changed = prepare()
            self.assertEqual(upload.call_count, 2)
            self.assertEqual(changed["children"][0]["picture"]["attributes"]["id"], "two")
            prepare("another-document")
            self.assertEqual(upload.call_count, 3)


class OtlSyncSafetyTest(OtlServiceTestCase):
    def setUp(self):
        super().setUp()
        self.md_dir = os.path.join(self.tmp.name, "markdown")
        os.mkdir(self.md_dir)
        with open(os.path.join(self.md_dir, "a.md"), "w", encoding="utf-8") as fh:
            fh.write("# new")
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.folder = {"a.otl": {"id": "fid"}}
        mock.patch.object(otl_sync, "list_folder", side_effect=lambda *args: self.folder.copy()).start()

    def run_sync(self, *args):
        argv = ["otl_sync.py", "--md-dir", self.md_dir, "--drive-id", "drive-A",
                "--parent-id", "0", "--state", self.state_path, "--cli", "/cli", *args]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            otl_sync.main()

    def seed_state(self, **entry):
        state = {"version": 1, "target": otl_sync.state_target("drive-A", "0", self.md_dir),
                 "documents": {"a.otl": {"file_id": "fid", **entry}}}
        otl_sync.save_state(self.state_path, state)

    def test_default_state_paths_separate_drives_and_sources(self):
        paths = {otl_sync.default_state_path(otl_sync.state_target(drive, "0", source))
                 for drive, source in (("A", "/one"), ("B", "/one"), ("A", "/two"))}
        self.assertEqual(len(paths), 3)

    def test_mismatched_and_legacy_state_cannot_issue_cloud_calls(self):
        self.seed_state(status="pending")
        with mock.patch.object(otl_sync, "list_folder") as listing:
            with self.assertRaises(SystemExit):
                self.run_sync("--drive-id", "drive-B")
            listing.assert_not_called()
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump({"a.otl": {"file_id": "foreign-document"}}, fh)
        with self.assertRaises(SystemExit):
            self.run_sync()
        self.assertEqual(self.events, [])

    def test_cached_document_absent_from_target_folder_is_never_reused(self):
        self.seed_state(status="pending")
        self.folder = {}
        with self.assertRaises(SystemExit):
            self.run_sync()
        self.assertNotIn("delete", self.events)

    def test_pending_document_is_resumed_under_default_skip(self):
        self.seed_state(status="pending", checkpoint=otl_write.body_fingerprint(self.children))
        self.run_sync()
        self.assertIn("create", self.events)
        with open(self.state_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["documents"]["a.otl"]["status"], "complete")

    def test_completed_or_unowned_existing_document_is_skipped(self):
        self.run_sync()
        self.assertEqual(self.events, [])
        self.seed_state(status="complete")
        self.run_sync()
        self.assertEqual(self.events, [])

    def test_partial_chunk_failure_resumes_without_duplicate_blocks(self):
        self.converted = [self.paragraph(f"new-{i}") for i in range(3)]
        self.fail_create = 2
        with self.assertRaises(RuntimeError):
            self.run_sync("--existing", "replace", "--chunk", "1")
        self.assertEqual(len(self.children), 1)
        with open(self.state_path, encoding="utf-8") as fh:
            entry = json.load(fh)["documents"]["a.otl"]
        self.assertEqual(entry["status"], "writing")
        self.assertEqual(entry["checkpoint"], otl_write.body_fingerprint(self.children))
        self.fail_create = None
        self.run_sync("--chunk", "1")
        self.assertEqual(len(self.children), 3)
        values = [block["paragraph"]["elements"][0]["text"]["attributes"]["content"]
                  for block in self.children]
        self.assertEqual(values, ["new-0", "new-1", "new-2"])

    def test_remote_changes_after_interruption_require_reconciliation(self):
        self.seed_state(status="writing", checkpoint="different checkpoint")
        with self.assertRaises(SystemExit):
            self.run_sync()
        self.assertNotIn("delete", self.events)

    def test_timeout_after_remote_commit_does_not_blindly_replay(self):
        self.commit_then_timeout = True
        with self.assertRaises(RuntimeError):
            self.run_sync("--existing", "replace")
        self.events.clear()
        with self.assertRaises(SystemExit):
            self.run_sync()
        self.assertNotIn("delete", self.events)
        self.assertNotIn("create", self.events)

    def test_batch_conversion_failure_keeps_existing_body(self):
        self.fail_convert = True
        with self.assertRaises(SystemExit):
            self.run_sync("--existing", "replace")
        self.assertEqual(self.children, [self.paragraph("old", "old-id")])
        self.assertNotIn("delete", self.events)

    def test_batch_dry_run_does_not_write_state_or_cloud_content(self):
        self.run_sync("--existing", "replace", "--dry-run")
        self.assertFalse(os.path.exists(self.state_path))
        self.assertEqual(self.events, [])

    def test_explicit_replace_can_reconcile_an_unknown_checkpoint(self):
        self.seed_state(status="writing", checkpoint="unknown after timeout")
        self.run_sync("--existing", "replace")
        self.assertIn("delete", self.events)
        self.assertEqual(len(self.children), 1)

    def test_new_document_conversion_failure_can_be_recovered_without_recreation(self):
        self.folder = {}
        self.children = [self.paragraph("")]

        def create(*args):
            self.folder["a.otl"] = {"id": "fid"}
            return "fid"

        with mock.patch.object(otl_sync, "create_doc", side_effect=create) as create_doc:
            self.fail_convert = True
            with self.assertRaises(SystemExit):
                self.run_sync()
            self.fail_convert = False
            self.run_sync()
            self.assertEqual(create_doc.call_count, 1)
        self.assertEqual(len(self.children), 1)

    def test_replacement_checkpoint_is_checked_after_conversion(self):
        self.after_convert = lambda: self.children.append(self.paragraph("concurrent", "other"))
        with self.assertRaises(SystemExit):
            self.run_sync("--existing", "replace")
        self.assertNotIn("delete", self.events)

    def test_atomic_state_save_preserves_previous_file_on_replace_failure(self):
        self.seed_state(status="pending")
        with open(self.state_path, "rb") as fh:
            original = fh.read()
        with mock.patch.object(os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                otl_sync.save_state(self.state_path, {"bad": "new state"})
        with open(self.state_path, "rb") as fh:
            self.assertEqual(fh.read(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)

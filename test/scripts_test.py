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
                mock.patch.object(_wps_cli, "auth_status", side_effect=list(statuses)), \
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
        with mock.patch.object(_wps_cli, "_invoke", return_value=denied) as invoke, \
                mock.patch.object(_wps_cli, "ensure_auth") as ensure:
            with self.assertRaisesRegex(SystemExit, "credential_store_blocked"):
                _wps_cli.run_cli(["cli", "api", "get", "/x"], 30, "test", "GET /x")
        self.assertEqual(invoke.call_count, 1)
        ensure.assert_not_called()

    def test_retry_storage_failure_is_classified_without_a_second_recovery(self):
        replies = [subprocess.CompletedProcess([], 1, "", "invalid_token"),
                   subprocess.CompletedProcess([], 1, "", self.BLOCKED)]
        with mock.patch.object(_wps_cli, "_invoke", side_effect=replies) as invoke, \
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
                         "network-failure", "invalid-grant", "success"):
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
    print(json.dumps({{'delegated': {{
        'available': True, 'has_refresh': True,
        'status': 'valid' if os.path.exists({state!r}) else 'expired'
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
                                 int(scenario != "status-blocked"))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)

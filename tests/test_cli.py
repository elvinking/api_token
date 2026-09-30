import http.server
import json
import os
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest import mock

from apitoken import cli


class Handler(http.server.BaseHTTPRequestHandler):
    """Эхо-обработчик: возвращает то, что получил, и умеет отдавать 503 заданное число раз."""

    fail_times = 0

    def respond(self):
        cls = type(self)
        if cls.fail_times > 0:
            cls.fail_times -= 1
            self.send_response(503)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.dumps({
            "method": self.command,
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "apikey": self.headers.get("X-API-Key"),
            "body": self.rfile.read(length).decode() if length else None,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Demo", "yes")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = respond

    def log_message(self, *args):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp.name, "config.json")
        self.env = mock.patch.dict(os.environ, {cli.ENV_CONFIG: self.cfg_path}, clear=False)
        self.env.start()
        for var in (cli.ENV_TOKEN, cli.ENV_BASE_URL, cli.ENV_PROFILE):
            os.environ.pop(var, None)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, *argv):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def write_config(self, payload):
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)


class TokenStorageTest(Base):
    def test_login_saves_token_with_private_permissions(self):
        self.run_cli("login", "secret-token-123")
        path = cli.config_path()
        self.assertEqual(cli.get_profile(cli.load_config(), "default")["token"], "secret-token-123")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_env_overrides_saved_token(self):
        self.run_cli("login", "saved-token-0000")
        os.environ[cli.ENV_TOKEN] = "env-token-11111"
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("env-…1111", out)

    def test_status_without_token_fails(self):
        code, _ = self.run_cli("status")
        self.assertEqual(code, 1)

    def test_logout_clears_token_but_keeps_profile(self):
        self.run_cli("login", "secret-token-123")
        self.run_cli("set-url", "https://api.example.com")
        self.run_cli("logout")
        prof = cli.get_profile(cli.load_config(), "default")
        self.assertNotIn("token", prof)
        self.assertEqual(prof["base_url"], "https://api.example.com")

    def test_logout_purge_removes_whole_profile(self):
        self.run_cli("login", "secret-token-123")
        self.run_cli("logout", "--purge")
        self.assertEqual(cli.load_config()["profiles"], {})

    def test_token_with_base64_padding_survives_round_trip(self):
        # Форма как у реальных токенов: 32 символа base64 с завершающим "=".
        token = "ZmFrZS10b2tlbi1mb3ItdGVzdHMtMDE="
        self.run_cli("login", token)
        self.assertEqual(cli.get_profile(cli.load_config(), "default")["token"], token)


class MigrationTest(Base):
    def test_flat_legacy_config_is_migrated_into_default_profile(self):
        self.write_config({"token": "legacy-token-xyz", "base_url": "https://old.example.com"})
        cfg = cli.load_config()
        self.assertEqual(cfg["current_profile"], "default")
        self.assertEqual(cli.get_profile(cfg, "default"),
                         {"token": "legacy-token-xyz", "base_url": "https://old.example.com"})

    def test_legacy_token_stays_usable_and_is_not_lost_on_save(self):
        self.write_config({"token": "legacy-token-xyz"})
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("lega…-xyz", out)
        # После любой записи конфиг уже в новом формате, токен на месте.
        self.run_cli("set-url", "https://api.example.com")
        self.assertEqual(cli.get_profile(cli.load_config(), "default")["token"], "legacy-token-xyz")

    def test_empty_config_needs_no_migration_fields(self):
        cfg = cli.load_config()
        self.assertEqual(cfg["profiles"], {})


class ProfileTest(Base):
    def test_profiles_are_independent(self):
        self.run_cli("login", "prod-token-0001")
        self.run_cli("--profile", "staging", "login", "stage-token-002")
        cfg = cli.load_config()
        self.assertEqual(cli.get_profile(cfg, "default")["token"], "prod-token-0001")
        self.assertEqual(cli.get_profile(cfg, "staging")["token"], "stage-token-002")

    def test_first_created_profile_becomes_current(self):
        self.run_cli("--profile", "staging", "login", "stage-token-002")
        self.assertEqual(cli.load_config()["current_profile"], "staging")

    def test_use_switches_current_profile(self):
        self.run_cli("login", "prod-token-0001")
        self.run_cli("--profile", "staging", "login", "stage-token-002")
        self.run_cli("use", "staging")
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("stag…-002", out)

    def test_use_rejects_unknown_profile(self):
        self.run_cli("login", "prod-token-0001")
        with self.assertRaises(SystemExit):
            self.run_cli("use", "nope")
        self.assertEqual(cli.load_config()["current_profile"], "default")

    def test_profiles_listing_marks_current(self):
        self.run_cli("login", "prod-token-0001")
        self.run_cli("--profile", "staging", "login", "stage-token-002")
        _, out = self.run_cli("profiles")
        self.assertIn("* default:", out)
        self.assertIn("  staging:", out)

    def test_env_profile_selects_profile(self):
        self.run_cli("login", "prod-token-0001")
        self.run_cli("--profile", "staging", "login", "stage-token-002")
        os.environ[cli.ENV_PROFILE] = "staging"
        _, out = self.run_cli("status")
        self.assertIn("stag…-002", out)


class UrlTest(Base):
    def test_query_params_are_encoded(self):
        url = cli.build_url("https://api.example.com", "/search", [("q", "привет мир"), ("n", "2")])
        self.assertEqual(url, "https://api.example.com/search?q=%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82+%D0%BC%D0%B8%D1%80&n=2")

    def test_query_params_merge_with_existing(self):
        url = cli.build_url("https://api.example.com", "/search?page=1", [("n", "2")])
        self.assertEqual(url, "https://api.example.com/search?page=1&n=2")

    def test_full_url_overrides_base(self):
        self.assertEqual(cli.build_url("https://api.example.com", "https://other.test/x"),
                         "https://other.test/x")

    def test_missing_base_url_is_reported(self):
        with self.assertRaises(SystemExit):
            cli.build_url(None, "/users")

    def test_bad_query_is_reported(self):
        with self.assertRaises(SystemExit):
            cli.parse_query(["broken"])


class RequestTest(Base):
    def setUp(self):
        super().setUp()
        Handler.fail_times = 0
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.run_cli("login", "secret-token-123")
        self.run_cli("set-url", f"http://127.0.0.1:{self.server.server_port}/")

    def test_request_sends_bearer_token(self):
        code, out = self.run_cli("request", "GET", "/users/me")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["path"], "/users/me")
        self.assertEqual(payload["auth"], "Bearer secret-token-123")

    def test_custom_auth_header_sends_raw_token(self):
        _, out = self.run_cli("request", "GET", "/users/me", "--auth-header", "X-API-Key")
        payload = json.loads(out)
        self.assertIsNone(payload["auth"])
        self.assertEqual(payload["apikey"], "secret-token-123")

    def test_request_uses_selected_profile_token(self):
        self.run_cli("--profile", "staging", "login", "stage-token-002")
        self.run_cli("--profile", "staging", "set-url", f"http://127.0.0.1:{self.server.server_port}/")
        _, out = self.run_cli("--profile", "staging", "request", "GET", "/whoami")
        self.assertEqual(json.loads(out)["auth"], "Bearer stage-token-002")

    def test_query_params_reach_server(self):
        _, out = self.run_cli("request", "GET", "/search", "-q", "q=test", "-q", "n=2")
        self.assertEqual(json.loads(out)["path"], "/search?q=test&n=2")

    def test_include_prints_status_and_headers(self):
        _, out = self.run_cli("request", "GET", "/x", "-i")
        self.assertTrue(out.startswith("HTTP 200\n"))
        self.assertIn("X-Demo: yes", out)

    def test_output_writes_body_to_file(self):
        target = os.path.join(self.tmp.name, "body.json")
        code, out = self.run_cli("request", "GET", "/x", "-o", target)
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        with open(target, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["path"], "/x")

    def test_raw_skips_json_formatting(self):
        _, out = self.run_cli("request", "GET", "/x", "--raw")
        self.assertNotIn("\n  ", out)

    def test_retries_recover_from_temporary_failure(self):
        Handler.fail_times = 2
        code, out = self.run_cli("request", "GET", "/x", "--retries", "3", "--retry-delay", "0")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["path"], "/x")
        self.assertEqual(Handler.fail_times, 0)

    def test_retries_exhausted_returns_error_status(self):
        Handler.fail_times = 5
        code, _ = self.run_cli("request", "GET", "/x", "--retries", "1", "--retry-delay", "0")
        self.assertEqual(code, 1)

    def test_no_retry_without_flag(self):
        Handler.fail_times = 1
        code, _ = self.run_cli("request", "GET", "/x")
        self.assertEqual(code, 1)

    def test_negative_retries_rejected(self):
        with self.assertRaises(SystemExit):
            self.run_cli("request", "GET", "/x", "--retries", "-1")

    def test_body_from_argument_and_content_type(self):
        _, out = self.run_cli("request", "POST", "/items", "-d", '{"name":"test"}')
        payload = json.loads(out)
        self.assertEqual(payload["method"], "POST")
        self.assertEqual(payload["body"], '{"name":"test"}')

    def test_error_status_returns_exit_code_1(self):
        Handler.fail_times = 1
        code, _ = self.run_cli("request", "GET", "/x")
        self.assertEqual(code, 1)

    def test_bad_header_is_reported(self):
        with self.assertRaises(SystemExit):
            self.run_cli("request", "GET", "/x", "-H", "broken")


class RetryAfterTest(unittest.TestCase):
    def test_numeric_retry_after_is_used(self):
        self.assertEqual(cli.retry_after({"Retry-After": "5"}, 1.0), 5.0)

    def test_date_retry_after_falls_back(self):
        self.assertEqual(cli.retry_after({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 1.0), 1.0)

    def test_retry_after_is_capped(self):
        self.assertEqual(cli.retry_after({"Retry-After": "99999"}, 1.0), cli.RETRY_DELAY_CAP)

    def test_missing_retry_after_falls_back(self):
        self.assertEqual(cli.retry_after({}, 2.5), 2.5)


class SignalTest(Base):
    """`apitoken … | head` и Ctrl-C не должны печатать трейсбек."""

    def test_broken_pipe_exits_quietly(self):
        with mock.patch.object(cli, "cmd_status", side_effect=BrokenPipeError):
            code, _ = self.run_cli("status")
        self.assertEqual(code, 141)

    def test_keyboard_interrupt_exits_quietly(self):
        with mock.patch.object(cli, "cmd_status", side_effect=KeyboardInterrupt):
            code, _ = self.run_cli("status")
        self.assertEqual(code, 130)


if __name__ == "__main__":
    unittest.main()

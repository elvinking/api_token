import http.server
import json
import os
import stat
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from apitoken import cli


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"path": self.path, "auth": self.headers.get("Authorization")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = os.path.join(self.tmp.name, "config.json")
        self.env = mock.patch.dict(os.environ, {cli.ENV_CONFIG: cfg}, clear=False)
        self.env.start()
        os.environ.pop(cli.ENV_TOKEN, None)
        os.environ.pop(cli.ENV_BASE_URL, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv):
        out = StringIO()
        with redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_login_saves_token_with_private_permissions(self):
        self.run_cli("login", "secret-token-123")
        path = cli.config_path()
        self.assertEqual(json.loads(path.read_text())["token"], "secret-token-123")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_env_overrides_saved_token(self):
        self.run_cli("login", "saved-token-0000")
        os.environ[cli.ENV_TOKEN] = "env-token-11111"
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("env-…1111", out)

    def test_status_without_token_fails(self):
        code, out = self.run_cli("status")
        self.assertEqual(code, 1)

    def test_logout(self):
        self.run_cli("login", "secret-token-123")
        self.run_cli("logout")
        self.assertNotIn("token", cli.load_config())

    def test_request_sends_bearer_token(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.run_cli("login", "secret-token-123")
            self.run_cli("set-url", f"http://127.0.0.1:{server.server_port}/")
            code, out = self.run_cli("request", "GET", "/users/me")
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"path": "/users/me", "auth": "Bearer secret-token-123"})


if __name__ == "__main__":
    unittest.main()

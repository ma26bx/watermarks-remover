"""HTTP integration tests for MCP OAuth. Run with stdlib unittest."""

import base64
import hashlib
import http.client
import json
import re
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "service"))
sys.path.insert(0, str(ROOT / "service" / "scripts"))

from mcp_oauth import CALLBACK, OAuthState, SCOPE
from mcp_server import MCPHandler


class OAuthIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), MCPHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def setUp(self):
        MCPHandler.oauth = OAuthState("test-owner-key-not-a-real-secret", "https://example.test")
        self.state = MCPHandler.oauth

    def request(self, method, path, data=None, headers=None, form=False):
        headers = dict(headers or {})
        if data is not None:
            data = urlencode(data) if form else json.dumps(data)
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=5)
        try:
            conn.request(method, path, data, headers)
            r = conn.getresponse()
            body = r.read().decode()
            return r.status, dict(r.getheaders()), json.loads(body) if r.getheader("Content-Type", "").startswith("application/json") else body
        finally:
            conn.close()

    def register(self, redirect=CALLBACK):
        status, _, body = self.request("POST", "/oauth/register", {"redirect_uris": [redirect], "token_endpoint_auth_method": "none"})
        self.assertEqual(status, 201, body)
        return body["client_id"]

    def start(self, client=None, **changes):
        client = client or self.register()
        verifier = "v" * 64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        p = {"client_id": client, "redirect_uri": CALLBACK, "response_type": "code", "state": "state-roundtrip",
             "code_challenge": challenge, "code_challenge_method": "S256", "resource": self.state.resource, "scope": SCOPE}
        p.update(changes)
        status, h, body = self.request("GET", "/oauth/authorize?" + urlencode(p))
        self.assertEqual(status, 200, body)
        flow = re.search(r'name="flow" value="([^"]+)"', body).group(1)
        return client, verifier, flow, ""

    def code(self, client=None):
        client, verifier, flow, cookie = self.start(client)
        status, h, body = self.request("POST", "/oauth/authorize", {"flow": flow, "owner_key": self.state.owner_key},
                                       {"Origin": self.state.base, "Cookie": cookie}, form=True)
        self.assertEqual(status, 303, body)
        query = parse_qs(urlparse(h["Location"]).query)
        self.assertEqual(query["iss"], [self.state.base])
        self.assertEqual(query["state"], ["state-roundtrip"])
        self.assertNotIn(self.state.owner_key, h["Location"])
        return {"grant_type": "authorization_code", "client_id": client, "code": query["code"][0],
                "code_verifier": verifier, "redirect_uri": CALLBACK, "resource": self.state.resource}

    def token(self):
        p = self.code()
        status, _, body = self.request("POST", "/oauth/token", p, form=True)
        self.assertEqual(status, 200, body)
        return p["client_id"], body

    def test_discovery_and_unauthorized_challenge(self):
        status, h, _ = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 401)
        self.assertIn('/.well-known/oauth-protected-resource/mcp', h["WWW-Authenticate"])
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            status, _, body = self.request("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(body["resource"], self.state.resource)
        _, _, body = self.request("GET", "/.well-known/oauth-authorization-server")
        self.assertEqual(body["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(body["authorization_response_iss_parameter_supported"])

    def test_complete_flow_initialization_tools_and_real_inspection(self):
        _, token = self.token()
        headers = {"Authorization": "Bearer " + token["access_token"]}
        status, _, body = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers)
        self.assertEqual(status, 200)
        self.assertIn("tools", body["result"]["capabilities"])
        status, _, body = self.request("POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
        self.assertEqual(status, 202)
        _, _, body = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers)
        self.assertIn("inspect_file", [t["name"] for t in body["result"]["tools"]])
        data = base64.b64encode("texto de teste\u200b".encode()).decode()
        _, _, body = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                                  "params": {"name": "inspect_file", "arguments": {"name": "teste.txt", "file": data}}}, headers)
        self.assertFalse(body["result"]["isError"], body)
        self.assertTrue(json.loads(body["result"]["content"][0]["text"])["ok"])

    def test_code_is_one_use(self):
        p = self.code()
        self.assertEqual(self.request("POST", "/oauth/token", p, form=True)[0], 200)
        status, _, body = self.request("POST", "/oauth/token", p, form=True)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_grant")

    def test_wrong_pkce_and_resource_rejected(self):
        p = self.code()
        p["code_verifier"] = "w" * 64
        self.assertEqual(self.request("POST", "/oauth/token", p, form=True)[2]["error"], "invalid_grant")
        p = self.code()
        p["resource"] = "https://attacker.test/mcp"
        self.assertEqual(self.request("POST", "/oauth/token", p, form=True)[0], 400)

    def test_wrong_client_cannot_redeem_code(self):
        p = self.code()
        p["client_id"] = self.register()
        self.assertEqual(self.request("POST", "/oauth/token", p, form=True)[2]["error"], "invalid_grant")

    def test_refresh_rotation_and_replay_rejection(self):
        client, token = self.token()
        p = {"grant_type": "refresh_token", "client_id": client, "resource": self.state.resource, "refresh_token": token["refresh_token"]}
        status, _, new = self.request("POST", "/oauth/token", p, form=True)
        self.assertEqual(status, 200)
        self.assertNotEqual(new["refresh_token"], token["refresh_token"])
        self.assertEqual(self.request("POST", "/oauth/token", p, form=True)[2]["error"], "invalid_grant")

    def test_password_and_signed_form_required(self):
        _, _, flow, cookie = self.start()
        p = {"flow": flow, "owner_key": self.state.owner_key}
        bad_form = {**p, "flow": flow + "tampered"}
        self.assertEqual(self.request("POST", "/oauth/authorize", bad_form, {"Origin": self.state.base}, form=True)[0], 400)
        self.assertEqual(self.request("POST", "/oauth/authorize", p, {"Origin": "https://attacker.test", "Cookie": cookie}, form=True)[0], 400)
        p["owner_key"] = "wrong"
        self.assertEqual(self.request("POST", "/oauth/authorize", p, {"Origin": self.state.base, "Cookie": cookie}, form=True)[0], 401)

    def test_redirect_and_client_tampering_rejected(self):
        status, _, _ = self.request("POST", "/oauth/register", {"redirect_uris": ["https://chatgpt.com.attacker.test/callback"]})
        self.assertEqual(status, 400)
        client = self.register()
        status, _, _ = self.request("GET", "/oauth/authorize?" + urlencode({"client_id": client, "redirect_uri": "https://attacker.test"}))
        self.assertEqual(status, 400)
        self.assertIsNone(self.state.client(client + "tamper"))

    def test_desktop_authorization_without_cookies(self):
        for origin in (None, "null"):
            with self.subTest(origin=origin):
                _, _, flow, cookie = self.start()
                headers = {} if origin is None else {"Origin": origin}
                p = {"flow": flow, "owner_key": self.state.owner_key}
                self.assertEqual(self.request("POST", "/oauth/authorize", p, headers, form=True)[0], 303)

    def test_consent_survives_service_restart_but_expired_form_fails(self):
        _, _, flow, _ = self.start()
        MCPHandler.oauth = OAuthState(self.state.owner_key, self.state.base)
        p = {"flow": flow, "owner_key": self.state.owner_key}
        self.assertEqual(self.request("POST", "/oauth/authorize", p, form=True)[0], 303)
        data = self.state.verify(flow, "flow")
        data.pop("kind")
        data["exp"] = 0
        p["flow"] = self.state.sign("flow", data)
        self.assertEqual(self.request("POST", "/oauth/authorize", p, form=True)[0], 400)

    def test_direct_key_still_works_and_tokens_are_bound(self):
        self.assertTrue(self.state.authorized("Bearer " + self.state.owner_key))
        self.assertFalse(self.state.authorized("Bearer incorrect"))
        expired = self.state.sign("access", {"aud": self.state.resource, "scope": SCOPE, "exp": 0})
        self.assertFalse(self.state.authorized("Bearer " + expired))
        wrong_aud = self.state.sign("access", {"aud": "https://attacker.test", "scope": SCOPE})
        self.assertFalse(self.state.authorized("Bearer " + wrong_aud))
        _, token = self.token()
        self.assertFalse(self.state.authorized("Bearer " + token["access_token"] + "tamper"))

    def test_missing_owner_key_fails_closed(self):
        MCPHandler.oauth = OAuthState("", "https://example.test")
        self.assertFalse(MCPHandler.oauth.authorized("Bearer anything"))
        self.assertEqual(self.request("GET", "/mcp")[0], 401)
        self.assertEqual(self.request("GET", "/oauth/authorize")[0], 503)

    def test_client_registration_survives_restart(self):
        client = self.register()
        restarted = OAuthState(self.state.owner_key, self.state.base)
        self.assertIsNotNone(restarted.client(client))


if __name__ == "__main__":
    unittest.main()
